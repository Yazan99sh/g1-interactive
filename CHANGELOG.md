# Changelog

All notable changes to the G1 Interactive Voice Pipeline.

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

### Added — config & editability
- **Editable instructions:** persona moved out of code into `prompts/persona.md`
  (built-in fallback kept); `ConversationManager.reload_persona()`.
- **Per-turn event log** `logs/events.jsonl` (`app/metrics.py`) — transcript +
  rough cost source for the control panel; best-effort, never disturbs a turn.

### In progress (this iteration)
- Web control panel (FastAPI) · clean-env setup + `doctor.py` · git repo + GitHub push.

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
