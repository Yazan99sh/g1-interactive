"""Lightweight knowledge base (the robot's 'brain' facts).

Loads every ``.md`` / ``.txt`` file under ``knowledge/`` and supports two uses:

* ``context_for(query)`` — retrieve the most relevant paragraphs to inject into
  the LLM system prompt (RAG, keyword-overlap scoring — no embeddings dependency,
  works fully offline and is plenty for a small curated KB).
* ``match_faq(query, language)`` — for KB-strict mode: if the question closely
  matches a ``Q:``/``A:`` pair, return the answer verbatim and skip the LLM.

FAQ format inside any knowledge file:
    Q: What are your opening hours?
    A: We are open from 9am to 9pm, seven days a week.
(Arabic works the same: ``س:`` / ``ج:``.)
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

from app.logging_setup import get_logger
from app.state import Language, detect_language

log = get_logger("ai.kb")

_TOKEN = re.compile(r"\w+", re.UNICODE)
# Very common words to ignore when scoring (EN + a few AR particles).
_STOP = {
    "the", "a", "an", "is", "are", "to", "of", "and", "or", "in", "on", "for",
    "what", "how", "do", "you", "i", "me", "my", "your", "can", "please", "tell",
    "في", "من", "الى", "على", "عن", "هل", "ما", "كيف", "هو", "هي", "و",
}


def _tokens(text: str) -> set[str]:
    return {t.lower() for t in _TOKEN.findall(text) if t.lower() not in _STOP and len(t) > 1}


class KnowledgeBase:
    def __init__(self, knowledge_dir: Path) -> None:
        self.dir = knowledge_dir
        self.chunks: list[tuple[str, str, set[str]]] = []   # (source, text, tokens)
        # (question, answer, q_tokens, answer_language)
        self.faqs: list[tuple[str, str, set[str], "Language | None"]] = []
        self.reload()

    def reload(self) -> None:
        self.chunks.clear()
        self.faqs.clear()
        if not self.dir.exists():
            log.warning("Knowledge dir %s does not exist — KB is empty.", self.dir)
            return
        files = list(self.dir.glob("*.md")) + list(self.dir.glob("*.txt"))
        for path in files:
            try:
                text = path.read_text(encoding="utf-8")
            except Exception:
                log.warning("Could not read KB file %s", path)
                continue
            self._parse_faqs(text)
            for para in re.split(r"\n\s*\n", text):
                para = para.strip()
                if len(para) >= 20:
                    self.chunks.append((path.name, para, _tokens(para)))
        log.info("KB loaded: %d chunks, %d FAQs from %d files", len(self.chunks), len(self.faqs), len(files))

    def _parse_faqs(self, text: str) -> None:
        q: Optional[str] = None
        for raw in text.splitlines():
            line = raw.strip()
            m_q = re.match(r"^(?:Q|س)\s*[:：]\s*(.+)$", line, re.IGNORECASE)
            m_a = re.match(r"^(?:A|ج)\s*[:：]\s*(.+)$", line, re.IGNORECASE)
            if m_q:
                q = m_q.group(1).strip()
            elif m_a and q:
                answer = m_a.group(1).strip()
                faq_lang = detect_language(answer) or detect_language(q)
                self.faqs.append((q, answer, _tokens(q), faq_lang))
                q = None

    def context_for(self, query: str, max_chunks: int = 4, max_chars: int = 1500) -> str:
        if not self.chunks:
            return ""
        q = _tokens(query)
        if not q:
            return ""
        scored = []
        for source, text, toks in self.chunks:
            overlap = len(q & toks)
            if overlap:
                scored.append((overlap, source, text))
        scored.sort(key=lambda x: x[0], reverse=True)
        out, total = [], 0
        for _score, source, text in scored[:max_chunks]:
            if total + len(text) > max_chars:
                break
            out.append(text)
            total += len(text)
        return "\n\n".join(out)

    def match_faq(
        self, query: str, language: Language, threshold: float = 0.6
    ) -> Optional[tuple[str, "Language | None"]]:
        """Return (answer, answer_language) for a close FAQ match, else None.

        Scores two-sided to avoid firing on an incidental shared word in an
        unrelated query: ``recall`` = share of the FAQ question covered, and
        ``precision`` = share of the user's query that is the FAQ. A single
        shared keyword in a long unrelated question fails the precision gate.
        NB: bag-of-words can't perfectly disambiguate — write distinct FAQs.
        """
        q = _tokens(query)
        if not q:
            return None
        best_score, best_answer, best_lang = 0.0, None, None
        for _question, answer, q_toks, faq_lang in self.faqs:
            if not q_toks:
                continue
            overlap = len(q & q_toks)
            if overlap == 0:
                continue
            recall = overlap / len(q_toks)
            precision = overlap / len(q)
            if len(q_toks) == 1:
                ok = overlap == 1 and precision > 0.5  # query dominated by that one word
            else:
                ok = overlap >= 2 and recall >= threshold and precision >= 0.5
            if ok and recall * precision > best_score:
                best_score, best_answer, best_lang = recall * precision, answer, faq_lang
        if best_answer is not None:
            log.info("KB FAQ hit (score=%.2f)", best_score)
            return best_answer, best_lang
        return None
