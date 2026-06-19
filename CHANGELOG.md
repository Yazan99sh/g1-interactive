# Changelog

All notable changes to the G1 Interactive Voice Pipeline.

## [0.3.1] — 2026-06-19 — Brave web search

### Added — web search with a spoken announcement
- The robot now uses the **Brave Search** API (`BRAVE_SEARCH_API_KEY`, already in config)
  to answer from live web results — for explicit "search / look it up / google" requests
  and for questions needing current info (latest, news, weather, prices, recent events;
  English + Arabic). New `ai/search.py` (Brave client + bilingual intent detector + result
  formatter), wired into `handle_audio` so it works on both the streaming and one-shot paths.
- **It says it's searching first**, in the conversation's language (`WEB_SEARCH_ANNOUNCE_EN/AR`),
  and the Brave fetch overlaps that announcement to hide latency. Then it answers from the
  results (briefly, mentions it's from the web, no URLs read aloud). Toggle with
  `WEB_SEARCH_ENABLED`; auto-off if the key is missing. **Live-verified EN + AR.**
- `DIALOGFLOW_KEY_PATH` now expands `~`; `HostMic` auto-falls-back to an input-capable
  device when the default has none.

## [0.3.0] — 2026-06-19 — Dialogflow CX, movement commands, gesture relax, faster STT

### Fixed — chunked speech no longer overlaps itself on the robot
- `robot/speaker.py` fed each chunk at 0.9× its real duration, so `play()` returned
  ~0.1× early while the robot was still emitting buffered audio. In single-shot mode
  that was invisible, but in **chunked/streaming** mode the next piece started over the
  previous piece's tail → the robot talked over itself. Now it drains the built-up lead
  (+ a small transport margin) before returning, so pieces play back-to-back cleanly.

### Changed — talking gesture returns to rest after 1–3 s
- The robot does its one move when it starts talking, **holds ~`TALK_GESTURE_HOLD_MS`
  (default 2000 ms, clamped 1000–3000), then relaxes to neutral even if still speaking**
  — holding a raised hand through a long reply looked odd. Short replies still relax when
  speech ends.

### Added — Dialogflow CX as the first answer (LLM fallback)
- New `ai/dialogflow.py`: each turn is sent to a Dialogflow CX agent first; a confident
  **intent** match (rejecting `NO_MATCH`/generative `PLAYBOOK`) is spoken verbatim and
  the LLM is skipped. Anything it doesn't match falls through to the LLM. Off by default
  (`DIALOGFLOW_ENABLED`); graceful no-op if the lib/key is missing. Fresh CX session per
  visitor. New **Dialogflow** panel tab: enable + project/location/agent/key/confidence +
  a live "test the agent" box.
- Targets the bilingual **Nova-1** agent (`nova-1-474411` / `asia-south1`).
  `tools/cx_import.py` discovers the agent by name, **adds Arabic**, pins it to its intent
  flow (not a generative Playbook), purges, imports `tools/nif_qa.json` (NIF/NDF Q&A — the
  two verbatim answer blocks + faithful translations + many EN/AR phrasings), and trains.
  `tools/cx_test.py` verifies both languages. **Verified: both Arabic and English test
  cases match at confidence 1.00 with the exact answers.**
- **Arabic numbers spelled out:** Arabic responses (and the system persona) write
  numbers/years/dates as Arabic WORDS, not digits (e.g. «ألفين وأربعة وعشرين»), and drop
  Latin acronyms — ElevenLabs' Arabic voice skips digits and mispronounces Latin text.

### Hardening — post-review fixes (adversarial review of this changeset)
- Movement parser rewritten to be safe: Arabic now requires a real motion **verb**
  (whole-word) before any direction, so clitic-glued nouns (أحزاب اليمين, باليمين,
  التقدم, وقفة) no longer move the robot; English drops the "go ahead" filler and guards
  idioms ("move forward with the plan"). Length-capped to terse commands.
- `robot/locomotion.py` no longer drives if `LocoClient.Start()` fails (won't lurch a
  non-standing robot). Movement safety bounds centralized in `app/movement.py`.
- Dialogflow client does its blocking init off the event loop, and accepts only true
  INTENT matches (allow-list). `/api/dialogflow/test` no longer blocks the panel loop.
- `ROBOT_SPEAKER_TAIL_DRAIN_MS` makes the chunk-drain margin tunable for the real robot.

### Added — experimental voice movement commands (off by default)
- `MOVEMENT_COMMANDS_ENABLED`: "move forward/back/left/right", "turn left/right", "stop"
  (English + Arabic) drive the G1 a short, bounded, low-speed distance via `LocoClient`
  (`robot/locomotion.py` + `app/movement.py` parser). Robot must be standing in Main mode.
  Toggle + speed/duration in the panel's Gestures tab. Safety-clamped; never fires on
  ordinary questions.

### Added — selectable STT backend (faster)
- `STT_BACKEND=openai|groq`. Groq **`whisper-large-v3-turbo`** is an OpenAI-compatible,
  much faster + cheaper backend with strong Arabic+English (set `GROQ_API_KEY`, pick it in
  the Speech tab; falls back to OpenAI if the key is missing). See `RESEARCH.md` for the
  STT comparison and a custom-gesture (`rt/arm_sdk`) implementation path.

### Changed — control-panel service control is diagnosable
- The panel now captures the pipeline's stdout/stderr to `logs/pipeline.out.log`, waits
  for it to settle, and **shows the exit code + last output when it fails to start**
  (subprocess mode) instead of a silent "stopped"; systemd mode surfaces
  ActiveState/Result + the journal tail. New "pipeline (stdout)" console log + a
  dashboard error box.

## [0.2.1] — 2026-06-18 — One-move gesture, richer head LED, chunked speech

Refinements after on-robot feedback.

### Changed — talking gesture is now ONE move
- The robot does a **single** arm move the moment it starts talking, then holds it,
  instead of cycling gestures the whole time ("one move is enough").
  `ArmController.talk()` performs one gesture then waits for speech to end.
- Removed the loop-only knobs `TALK_GESTURE_GAP_MS` / `TALK_GESTURE_MAX_PER_REPLY`
  (silently ignored if still present in an old `.env`). The move is `TALK_GESTURE_IDS`
  (first id; default **23** right-hand-up); wake/meet-and-greet stays `WAKE_GESTURE_ID`
  (**25**, wave with the right hand up near the head).

### Added — richer head-LED state indicator (`robot/led.py`)
- Distinct, easy-to-read colours per state: **standby** calm blue (idle/normal, matches
  the head's default glow), **listening** green, **thinking** amber, **speaking**
  magenta, plus a red **error** blink when a turn fails.
- *Thinking* **breathes** (smoothly pulses) so "busy" is obvious; configurable via
  `LED_PULSE_STATES` / `LED_PULSE_PERIOD_MS`. Solid states cost one RPC then idle (no
  spam); pulsing defaults to thinking only (no audio plays then → no RPC contention).
- A shared `LedIndicator` owned by the controller drives standby/listening/thinking +
  error; the pipeline drives speaking. All colours editable from the Environment tab.

### Added — chunked speech for faster first audio
- `TTS_CHUNKING_ENABLED` (default on) + `TTS_CHUNK_MAX_CHARS` (default 180): a long
  reply is split on sentence/clause boundaries (never mid-word; EN+AR aware,
  `ai/text_chunk.py`) and synth+played piece by piece, **prefetching the next piece
  while the current one plays** — first audio starts almost immediately, no gaps.
- Applies to the non-streaming path and to over-long streamed sentences.

### Added — control-panel tabs
- New **Speech** tab: toggle streaming + chunking and set the piece size.
- **Gestures** tab now uses single dropdowns (talking move + wake wave) instead of
  the multi-checkbox picker.

## [0.2.0] — 2026-06-17 — Continuous gestures, control panel, new-host setup 🚧

Second iteration after moving to a new host PC. Focus: the robot now **moves the
whole time it talks**, the environment is reproducible, and a **web control panel**
manages the feature (scripts, knowledge, instructions, env/keys/voice, live logs).

### Fixed — "no movement while the robot responds"
- The reply gesture was a single discrete action that ended in ~2-3s while speech
  ran 8-15s (robot froze), and `NEUTRAL`/`THOUGHTFUL` replies got **no** gesture at
  all (and replies default to `NEUTRAL` when the LLM omits the `[EMOTION:]` tag).
- New **talking-gesture loop** (`ArmController.talk(emotion, stop)`): cycles an
  emotion-flavored palette continuously, concurrently with playback, and stops the
  instant speech ends. Every mood now moves (quiet moods get a calm wave on repeat).
- Tunable via `TALK_GESTURES_ENABLED`, `TALK_GESTURE_GAP_MS`,
  `TALK_GESTURE_MAX_PER_REPLY` (safety cap), `TALK_GESTURE_IDS` (id override).

### Added — lower latency
- **Streaming replies** (`STREAMING_ENABLED`, default on): the LLM reply is streamed
  (OpenAI SSE, `LLMEngine.stream()`), split into sentences, and spoken as each one is
  ready — the robot starts talking after sentence 1 instead of after the whole reply +
  whole TTS. Producer/consumer in `pipeline._stream_speak`; the talk-gesture loop spans
  the entire reply. Auto-falls back to the one-shot path if streaming yields nothing.

### Added — wake word
- **Selectable wake engine** `WAKE_ENGINE=stt|openwakeword`. Default `stt` (bilingual,
  unchanged). `openwakeword` runs a local model in standby (no STT cost, faster, offline,
  English/phrase-only) — `audio/wake_audio.py`, opt-in, graceful fallback to `stt`.

### Added — web control panel (`controlpanel/`, FastAPI + vanilla-JS SPA)
- Manage the whole feature from a browser on the LAN (headless host OK): **process
  control** (systemd `--user` or panel-managed subprocess, auto-detected), **live log
  console** (WebSocket) + log-level toggle, **knowledge** / **instructions** / **.env**
  editors (keys masked, voice id highlighted, comments preserved), **scripts** runner
  (run `tools/`+`scripts/`, stream output, upload), and a **transcript + cost** view.
- Optional `PANEL_TOKEN` guard; KB/script paths confined; scripts run via argv (no shell).
- Run: `python -m controlpanel` → `http://<host>:8800`. See `CONTROL_PANEL.md`.

### Added — clean environment & deploy
- `setup.sh` (venv/conda + deps + optional unitree SDK + `.env` bootstrap),
  `tools/doctor.py` preflight (python/deps/keys/DDS iface/robot ping/audio),
  `deploy/` systemd `--user` units + install/uninstall scripts for pipeline + panel.

### Added — config & editability
- **Editable instructions:** persona moved out of code into `prompts/persona.md`
  (built-in fallback kept); `ConversationManager.reload_persona()`.
- **Per-turn event log** `logs/events.jsonl` (`app/metrics.py`) — transcript +
  rough cost source for the control panel; best-effort, never disturbs a turn.

### Added — repo
- Git repository initialized (scope: feature + control panel); `.gitattributes`
  (LF for the Linux host); `.gitignore` covers `.env`, `events.jsonl`, models, state.

### In progress (this iteration)
- Push to GitHub (coordinating account/repo/auth on the new host).

## [0.1.0] — 2026-06-10 — Initial build + first working hardware demo ✅

First end-to-end demo confirmed on the real robot: **"Hi Robot" → "Aha!" + wave →
conversation**, deployed on the **altkamul-g1** Ubuntu PC (conda env `tv`) talking
to the Unitree G1 over CycloneDDS on `eno1`.

### Added — pipeline
- State machine `Standby → Ack → Listen → Think → Respond` (`app/controller.py`,
  `app/pipeline.py`), ported from the `super-star` voice pipeline.
- **Standby:** VAD-gated wake-word detection ("Hi Robot" / "هاي روبوت"); transcribe
  only when someone speaks (`audio/wake.py`, `audio/vad.py`).
- **Ack:** robot says "Aha!" and waves at the same time.
- **Listen:** OpenAI `gpt-4o-transcribe` (`ai/stt.py`).
- **Think:** knowledge base (RAG + verbatim FAQ, `ai/knowledge_base.py`) → LLM
  OpenRouter (`openai/gpt-4o-mini`) → OpenAI fallback (`ai/llm.py`).
- **Respond:** ElevenLabs `eleven_flash_v2_5`, `output_format=pcm_16000` →
  G1 speaker, with an emotion-matched arm gesture played while talking.
- **Idle rule:** after `IDLE_AFTER_SILENT_TURNS` (10) consecutive silent turns,
  return to standby until the next "Hi Robot".
- Bilingual English/Arabic throughout; `[EMOTION:x]` tag drives the gesture + LED.

### Added — robot integration (verified against `unitree_sdk2_python` source)
- `robot/speaker.py` — G1 `AudioClient.PlayStream(app, stream_id, bytes)`, 16 kHz
  mono s16le, ≤96000-byte chunks, volume + LED.
- `robot/arm_gestures.py` — `G1ArmActionClient.ExecuteAction(id)`; gestures
  25 face-wave, 26 high-wave, 17 clap, 27 shake, 99 release (best-effort).
- `robot/mic_robot.py` — experimental G1 onboard mic via UDP multicast (opt-in).
- `robot/dds.py` — one-time `ChannelFactoryInitialize(0, DDS_INTERFACE)`.

### Added — architecture & tooling
- Pluggable `AudioSink` / `MicSource` / `ArmController` with host fallbacks
  (`HostSpeaker`, `HostMic`, `NullArmController`) so the whole cloud pipeline runs
  with no robot; `main.py` selects per `.env` and degrades gracefully.
- Logging: `logs/g1.log` (all) + `logs/errors.log` (errors + tracebacks).
- Tools: `smoke_test.py`, `_selftest.py`, `list_audio_devices.py`,
  `g1_speaker_test.py`, `g1_list_actions.py`, `robot_mic_test.py`.
- `README.md`, `RUNBOOK.md`; API keys imported from the `super-star` project.

### Verified on hardware (altkamul-g1 → G1)
- DDS reaches the robot over `eno1` (host `192.168.123.100`).
- G1 speaker plays a 440 Hz test tone.
- `GetActionList()` confirmed the firmware action ids match the code
  (`25/26/17/27/99`); `11 = blow_kiss`, correctly unused.
- Arm gestures require the robot in **Main mode (R1+X) + standing**.

### Fixed — pre-deploy adversarial code review (12 confirmed bugs)
- **STT language was pinned** to one language, breaking EN↔AR switching → now
  auto-detects every turn.
- **STT API errors were counted as "silent turns"** (could falsely idle to
  standby) → distinguished via `Transcription.error`.
- **HostMic shutdown hang** → bounded `queue.get` + flush on stop.
- **G1Speaker** thread-safety lock around `AudioClient` RPCs.
- `asyncio.gather(..., return_exceptions=True)` so a failed gesture can't cancel
  speech (and vice-versa).
- **Task-based shutdown** so Ctrl+C is honored mid-LLM-call.
- **KB-strict FAQ** over-fired on unrelated queries and ignored emotion/language →
  two-sided scoring + `parse_emotion` + FAQ language.
- Robot-mic multicast now binds to the robot NIC (`ROBOT_MIC_IFACE_IP`).
- Blocking robot `Init()` moved off the event loop at startup.

### Fixed — during deployment
- **Mic sample rate:** the host mic (ALC897 / USB) doesn't support 16 kHz, causing
  `PortAudioError: Invalid sample rate`. `HostMic` now captures at the device's
  native rate (e.g. 48000) and resamples to 16 kHz in `read_chunk`.

### Decisions
- **Robot mic not available** on this G1 "Plus"/U6 variant (UDP multicast
  `239.168.123.161:5555` returned 0 packets) → use a **host USB mic**
  (`MIC_SOURCE=host`).
- Reuse the teleop `tv` conda env (already has `unitree_sdk2_python`).

### Known limitations / next
- No acoustic echo cancellation (mic flushed during playback; no barge-in).
- LLM reply is non-streaming (speaks after the full reply).
- Possible next: tune wake sensitivity / `MIC_DEVICE`, streaming TTS, a `deploy.ps1`
  one-shot (scp + remote install).
