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
import ai.stt, ai.tts, ai.llm, ai.dialogflow, app.pipeline, app.controller  # noqa
import app.movement, app.memory, app.peek, robot.locomotion, robot.camera, audio.mic, audio.sink  # noqa
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

# 11. movement parser (EN + AR + false-positive guards)
from app.movement import parse_movement  # noqa: E402
check("mv forward", parse_movement("move forward").kind == "forward")
check("mv backup", parse_movement("back up").kind == "backward")
check("mv turn left", parse_movement("turn left").kind == "turn_left")
check("mv ar forward", parse_movement("تقدم للأمام").kind == "forward")
check("mv ar turn", parse_movement("لف يسار").kind == "turn_left")
check("mv ar stop", parse_movement("وقف").kind == "stop")
check("mv no-overfire", parse_movement("what is to the left of the building") is None)
check("mv question", parse_movement("tell me about the fund") is None)
check("mv ar-noun-right", parse_movement("أحزاب اليمين المتطرف") is None)   # right-wing parties
check("mv ar-noun-eat", parse_movement("ما حكم الأكل باليمين") is None)     # eating with the right hand
check("mv ar-pause-noun", parse_movement("وقفة احتجاجية") is None)          # a protest (not 'stop')
check("mv ar-progress", parse_movement("ما هو التقدم الذي أحرزته") is None) # 'progress' (not 'forward')
check("mv en-idiom", parse_movement("let's move forward with the plan") is None)
check("mv en-goahead", parse_movement("go ahead and tell me a joke") is None)
check("mv ar-stop-word", parse_movement("وقف").kind == "stop")             # bare stop still works

# 12. STT backend factory (default openai; groq falls back without a key)
import httpx  # noqa: E402
from ai.stt import make_transcriber, OPENAI_URL, GROQ_URL  # noqa: E402
_h = httpx.AsyncClient()
_orig_backend, _orig_key = settings.STT_BACKEND, settings.GROQ_API_KEY
try:
    settings.STT_BACKEND = "openai"
    check("stt default openai", make_transcriber(_h).base_url == OPENAI_URL)
    settings.STT_BACKEND = "groq"; settings.GROQ_API_KEY = ""
    check("stt groq fallback", make_transcriber(_h).name == "openai")
    settings.GROQ_API_KEY = "gsk_test"
    _g = make_transcriber(_h)
    check("stt groq selected", _g.name == "groq" and _g.base_url == GROQ_URL)
finally:
    settings.STT_BACKEND, settings.GROQ_API_KEY = _orig_backend, _orig_key
    import asyncio as _a; _a.run(_h.aclose())

# 13. Dialogflow client is a safe no-op when disabled
from ai.dialogflow import DialogflowClient  # noqa: E402
_df = DialogflowClient(); _df.enabled = False
check("df noop ensure", _df._ensure() is False)
_s1 = _df._session_id; _df.new_session()
check("df session rotates", _df._session_id != _s1)

# 14. web-search intent detection (EN + AR triggers, no over-fire on normal Q&A)
from ai.search import search_query  # noqa: E402
check("ws en explicit", search_query("search for the latest news") is not None)
check("ws en fresh", search_query("what's the weather today") is not None)
check("ws ar explicit", search_query("ابحث عن آخر الأخبار") is not None)
check("ws no-fire kb", search_query("what is the national infrastructure fund") is None)
check("ws no-fire greet", search_query("hello how are you") is None)
check("ws no-fire ar", search_query("ما هو صندوق البنية التحتية الوطني") is None)

# 15. brain: write/recall/expiry/forget + index round-trip + NullBrain no-op
import tempfile, datetime  # noqa: E402
from app.memory import Brain, NullBrain, _parse_candidates  # noqa: E402
from app.state import ChatMessage as _CM  # noqa: E402
_bdir = Path(tempfile.mkdtemp()) / "brain"
_b = Brain(_bdir)
_today = datetime.datetime.now().date()
_sup = _b._normalise({"type": "supervisor", "subject": "Yazan",
                      "content": "Yazan is the supervisor; prefers concise replies.",
                      "salience": 0.9, "tags": ["operator"]}, _today)
_vis = _b._normalise({"type": "person", "subject": "Ahmed",
                      "content": "Ahmed asked about backflips.", "salience": 0.3, "tags": []}, _today)
check("brain ttl permanent", _sup is not None and _sup.expiry == "never")
check("brain ttl visitor", _vis is not None and _vis.expiry != "never")
check("brain salience gate", _b._normalise({"type": "fact", "subject": "x", "content": "trivial",
                                            "salience": 0.05, "tags": []}, _today) is None)
for _r in (_sup, _vis):
    _b._atomic_write(_b._record_path(_r.id), _b._serialise(_r))
_b._rebuild_index([_sup, _vis])
check("brain recall hit", "Yazan" in _b.recall("tell me about Yazan"))
check("brain permanent floor", "Yazan" in _b.recall("what is the weather"))  # supervisor stays visible
_rt = _b._parse_file(_b._record_path(_sup.id).read_text(encoding="utf-8"))
check("brain file round-trip", _rt is not None and _rt.subject == "Yazan" and _rt.type == "supervisor")
_vis.expiry = (_today - datetime.timedelta(days=1)).isoformat()
_b._atomic_write(_b._record_path(_vis.id), _b._serialise(_vis))
check("brain sweep expires visitor", _b.sweep_expired() == 1 and not _b._record_path(_vis.id).exists())
check("brain sweep keeps supervisor", _b._record_path(_sup.id).exists())
check("brain snapshot", (lambda p: bool(p and p.exists()))(
    _b.snapshot_session([_CM("user", "hi"), _CM("assistant", "hello")])))
check("brain parse candidates", len(_parse_candidates('x [{"type":"team","subject":"F","content":"c","salience":0.9}] y')) == 1)
check("brain parse empty", _parse_candidates("nothing") == [])
_nb = NullBrain()
check("brain null noop", _nb.recall("x") == "" and _nb.snapshot_session([]) is None and _nb.enabled is False)

# 16. peek intent (EN + AR look/show requests; no over-fire on see/look-it-up/web search)
from app.peek import parse_peek_intent  # noqa: E402
check("peek en look", parse_peek_intent("can you take a look at the table") is not None)
check("peek en see", parse_peek_intent("what do you see") is not None)
check("peek en show", parse_peek_intent("show me what's around you") is not None)
check("peek ar shoof", parse_peek_intent("شوف شو في على الطاولة") is not None)
check("peek ar tara", parse_peek_intent("ماذا ترى أمامك") is not None)
check("peek no-fire isee", parse_peek_intent("I see what you mean, thanks") is None)
check("peek no-fire lookup", parse_peek_intent("can you look it up online") is None)
check("peek no-fire greet", parse_peek_intent("hello how are you today") is None)
from robot.camera import NullCamera  # noqa: E402
import asyncio as _a2  # noqa: E402
check("camera null noop", _a2.run(NullCamera().capture()) is None and NullCamera().enabled is False)

# 17. idle (sleep) intent + noise filter (EN + AR)
from app.intents import parse_sleep_intent, looks_like_noise  # noqa: E402
check("idle en go-idle", parse_sleep_intent("go idle") is True)
check("idle en idle-state", parse_sleep_intent("idle state please") is True)
check("idle en thats-all", parse_sleep_intent("okay that's all thanks") is True)
check("idle en goodbye", parse_sleep_intent("goodbye robot") is True)
check("idle ar nam", parse_sleep_intent("نام يا روبوت") is True)
check("idle ar mode", parse_sleep_intent("ادخل وضع الخمول") is True)
check("idle no-fire q", parse_sleep_intent("what does idle mean in a car engine exactly today") is None or
      parse_sleep_intent("what does idle mean in a car engine exactly today") is False)  # too long
check("idle no-fire normal", parse_sleep_intent("tell me about the fund") is False)
check("idle ar no-fire sleep-word", parse_sleep_intent("أنا ما بنام بالليل من كثر التفكير") is False)
check("noise empty", looks_like_noise("") is True)
check("noise punct", looks_like_noise("...") is True)
check("noise you", looks_like_noise("you") is True)
check("noise thanks", looks_like_noise("Thanks for watching!") is True)
check("noise ar shukran", looks_like_noise("شكرا") is True)
check("noise real en", looks_like_noise("what time do you open") is False)
check("noise real ar", looks_like_noise("ما هو صندوق البنية التحتية") is False)

print("\nALL PASS" if ok else "\nSOME FAILED")
sys.exit(0 if ok else 1)
