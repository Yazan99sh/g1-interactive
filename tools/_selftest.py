"""Throwaway self-test of the pure-logic slice (no network, no robot, no mic)."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.state import parse_emotion, detect_language, Emotion, Language
from audio.wake import WakeWordDetector
from audio.vad import UtteranceSegmenter
from ai.knowledge_base import KnowledgeBase
from ai.text_chunk import split_for_tts
from app.conversation import ConversationManager
from config import settings

ok = True
def check(name, cond):
    global ok
    print(("PASS" if cond else "FAIL"), name)
    ok = ok and cond

# 1. emotion tag parsing
e, t = parse_emotion("[EMOTION:happy] Hello there")
check("parse_emotion", e is Emotion.HAPPY and t == "Hello there")

# 2. language detection
check("detect ar", detect_language("مرحبا كيف حالك") is Language.ARABIC)
check("detect en", detect_language("hello how are you") is Language.ENGLISH)
check("detect none", detect_language("123 !!!") is None)

# 3. wake word (EN punctuation + Arabic diacritics/alef variants)
w = WakeWordDetector(["hi robot", "هاي روبوت"])
check("wake en", w.matches("Hi, Robot! how are you"))
check("wake ar", w.matches("هَايْ روبوت لو سمحت"))
check("wake neg", not w.matches("hello there friend"))

# 4. knowledge base load + retrieval + FAQ
kb = KnowledgeBase(settings.KNOWLEDGE_DIR)
check("kb chunks", len(kb.chunks) > 0)
_faq = kb.match_faq("what is your name", Language.ENGLISH)
check("kb faq", _faq is not None and _faq[0].strip() != "")
check("kb faq lang", _faq is not None and _faq[1] is Language.ENGLISH)
check("kb faq no-overfire", kb.match_faq("what time does the metro leave the city", Language.ENGLISH) is None)
check("kb ctx", "Altkamul" in kb.context_for("who made you company"))

# 5. VAD segmenter: silence -> loud -> trailing silence => done w/ speech
seg = UtteranceSegmenter(16000, 500, 900, 800, 15000, no_speech_timeout_ms=8000)
import numpy as np
def chunk(amp, ms=50):
    n = int(16000 * ms / 1000)
    return (np.random.randint(-amp, amp + 1, n).astype(np.int16)).tobytes()
res = None
for _ in range(20): res = seg.feed(chunk(3000))   # 1s of speech
for _ in range(25): res = seg.feed(chunk(5))      # ~1.25s silence
check("vad done", res.done and res.had_speech)

# 6. no-speech timeout path
seg2 = UtteranceSegmenter(16000, 500, 900, 800, 15000, no_speech_timeout_ms=2000)
r2 = None
for _ in range(60): r2 = seg2.feed(chunk(5))      # only silence
check("vad no-speech", r2.done and not r2.had_speech)

# 7. conversation messages
cm = ConversationManager(); cm.set_language(Language.ARABIC); cm.add_user("مرحبا")
msgs = cm.build_messages("مرحبا", "some kb context")
check("convo msgs", msgs[0].role == "system" and "Arabic" in msgs[0].content and msgs[-1].content == "مرحبا")

# 8. full module graph imports (no network calls)
import ai.stt, ai.tts, ai.llm, app.pipeline, app.controller, audio.mic, audio.sink  # noqa
check("imports", True)

# 9. TTS chunk splitter (for faster first audio)
check("chunk short", split_for_tts("Hello there.", 180) == ["Hello there."])
check("chunk empty", split_for_tts("   ", 180) == [])
_long = "A" * 50 + ". " + "B" * 50 + ". " + "C" * 50 + ". " + "D" * 50 + "."
_pieces = split_for_tts(_long, 80)
check("chunk splits long", len(_pieces) >= 3)
check("chunk respects max", all(len(p) <= 80 for p in _pieces))
check("chunk nonempty", all(p.strip() for p in _pieces))
# never split inside a word
_wp = split_for_tts(" ".join(["word"] * 60), 50)
check("chunk no midword", all(all(t == "word" for t in p.split()) for p in _wp))
# Arabic terminator/comma aware
_ar = split_for_tts("مرحبا، كيف حالك؟ أنا بخير شكرا لك جزيلا على هذا السؤال اللطيف.", 16)
check("chunk arabic", len(_ar) >= 2 and all(p.strip() for p in _ar))

# 10. LED indicator is a safe no-op without a sink
from robot.led import LedIndicator  # noqa: E402
_led = LedIndicator(None)
_led.set_state("thinking"); _led.set_state("speaking")  # must not raise
check("led noop", _led._enabled is False)

print("\nALL PASS" if ok else "\nSOME FAILED")
sys.exit(0 if ok else 1)
