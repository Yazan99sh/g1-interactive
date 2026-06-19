# G1 Interactive Voice Pipeline

Make the **Unitree G1** humanoid *talk with people*: someone says **"Hi Robot"**,
the robot answers **"Aha!"**, listens, thinks (knowledge base + LLM), and **speaks
back through its own speaker while gesturing with its arms**.

The conversation design is ported from the proven **`super-star`** pipeline
(OpenAI STT → LLM → ElevenLabs TTS, RMS voice-activity detection, emotion-driven
expression) and adapted to drive the G1 over **CycloneDDS** (`unitree_sdk2_python`),
deployed exactly like the teleop setup: a Linux host on the robot's
`192.168.123.x` wired LAN.

```
        ┌─────────── STANDBY ───────────┐
        │  listen cheaply for "Hi Robot" │
        └───────────────┬────────────────┘
                        │  wake word
                        ▼
              "Aha!"  +  wave  (ack)
                        │
        ┌───────────────▼────────────────┐
        │            CONVERSE             │
        │  Listen ─► Think ─► Respond ─►  │  (loops)
        │  STT      KB+LLM    TTS+gesture │
        └───────────────┬────────────────┘
            10 silent turns │ → back to STANDBY
```

## Pipeline stages

| Stage | What happens | Tech |
|------|---------------|------|
| **Standby** | VAD-gated capture; transcribe only when someone speaks; match a wake phrase | OpenAI STT + wake matcher |
| **Ack** | Robot says "Aha!" and waves | ElevenLabs + arm gesture |
| **Listening** | Capture the user's utterance, end-pointed by silence | `sounddevice` mic + RMS VAD |
| **Thinking** | Optional Dialogflow CX answer-first → else knowledge base retrieval → LLM (persona, bilingual) | Dialogflow CX → OpenRouter → OpenAI |
| **Responding** | Speak the reply; one arm move (then relaxes after 1–3 s); LED by state | ElevenLabs (PCM) → G1 speaker |

**STT** is selectable (`STT_BACKEND`): OpenAI `gpt-4o-transcribe` (default) or Groq
`whisper-large-v3-turbo` (faster). Experimental voice **movement** commands ("move
forward", "turn left", "وقف") can be enabled from the panel. See `RESEARCH.md`.

Reply text starts with an `[EMOTION:happy]`-style tag (stripped before speaking)
that selects the arm gesture + LED colour — same trick as `super-star`.

**Idle rule:** after `IDLE_AFTER_SILENT_TURNS` (default **10**) consecutive silent
listening turns, the robot drops back to standby until the next "Hi Robot".

## Layout

```
g1-interactive/
  main.py                 entry point — wires deps from config, runs the controller
  config.py               all tunables (reads .env)
  .env / .env.example     secrets + deployment config (.env is gitignored)
  requirements.txt
  app/
    logging_setup.py      rotating logs: g1.log (all) + errors.log (errors only)
    state.py              PipelineState / Language / Emotion / tag parsing
    conversation.py       history + persona/system-prompt builder (bilingual)
    pipeline.py           one turn: Transcribe → Think → Respond(+gesture)
    controller.py         master state machine: Standby ↔ Converse, idle-after-10
  ai/
    stt.py                STT — OpenAI gpt-4o-transcribe or Groq Whisper (STT_BACKEND)
    tts.py                ElevenLabs flash v2.5 (raw PCM output)
    llm.py                OpenRouter (primary) → OpenAI (fallback)
    dialogflow.py         optional Dialogflow CX "answer-first" stage (LLM fallback)
    knowledge_base.py     loads knowledge/*.md, RAG + verbatim FAQ
  app/
    movement.py           experimental voice movement-command parser (EN + AR)
  robot/
    locomotion.py         experimental G1 LocoClient walk wrapper (off by default)
  audio/
    mic.py                host mic capture (sounddevice) + utterance recorder
    vad.py                RMS voice-activity segmenter
    sink.py               audio output interface + HostSpeaker
    wake.py               wake-word matcher (EN + AR, diacritic-robust)
    wav.py                PCM/WAV helpers + resample
  robot/
    dds.py                ChannelFactoryInitialize on the robot NIC
    interfaces.py         ArmController interface + NullArmController
    speaker.py            G1 speaker (PCM out over DDS)      ← from verified spec
    arm_gestures.py       G1 arm gestures while talking      ← from verified spec
    mic_robot.py          optional G1 onboard mic over DDS   ← from verified spec
  prompts/persona.md      the robot's instructions/persona (editable; built-in fallback)
  knowledge/about.md      the robot's editable knowledge base
  tools/                  smoke test + device list + doctor.py preflight
  logs/                   runtime logs + events.jsonl transcript (gitignored)
  controlpanel/           web control panel (FastAPI) — manage the feature in a browser
  deploy/                 systemd --user units + install scripts (pipeline + panel)
  setup.sh                one-shot host setup (deps + optional SDK + .env)
```

## Control panel

A browser-based control panel (FastAPI) manages the whole feature from any device on
the LAN — process start/stop/restart, a live log console, and editors for the
knowledge base, instructions, and `.env` (keys + voice id), plus a script runner and
a live transcript + cost view. Run it on the host:

```bash
python -m pip install -r controlpanel/requirements.txt
python -m controlpanel            # -> http://<host>:8800
```

See **`CONTROL_PANEL.md`** for the full tour, the optional `PANEL_TOKEN`, and running
both the pipeline and panel as `systemd --user` services (`deploy/install_services.sh`).

## Setup

On the **Linux host** that sits on the robot's `192.168.123.x` LAN (the same
machine class used for teleop). The quickest path is the setup script:

```bash
./setup.sh                # venv/conda + core+panel deps + .env bootstrap
./setup.sh --with-sdk     # also install unitree_sdk2_python from source
python tools/doctor.py    # preflight: keys, DDS iface, robot ping, audio
```

Or do it by hand inside a conda/venv:

```bash
python -m pip install -r requirements.txt
# Robot DDS SDK (not on PyPI) — same as teleop:
git clone https://github.com/unitreerobotics/unitree_sdk2_python
cd unitree_sdk2_python && pip install -e . && cd -
cp .env.example .env      # the real .env already has the keys; keep it private
```

`.env` is pre-filled with the shared Altkamul API keys (OpenAI, OpenRouter,
ElevenLabs, Gemini, Brave). Set `DDS_INTERFACE` to the NIC that reaches the robot
(e.g. `ens37`), and pick your audio routing:

| `.env` | Robot deployment | Laptop test (no robot) |
|--------|------------------|------------------------|
| `ROBOT_ENABLED` | `true` | `false` |
| `AUDIO_SINK` | `robot` | `host` |
| `MIC_SOURCE` | `host` (USB mic near robot) or `robot` | `host` |

## Run

```bash
python main.py            # then say "Hi Robot"
# Stop with Ctrl+C
```

**Validate the cloud path first** (keys + STT/LLM/TTS, no robot, no mic) — type a
line and hear the answer on your PC speakers:

```bash
python tools/smoke_test.py
python tools/list_audio_devices.py   # find your mic/speaker index for MIC_DEVICE
```

## Logs

Everything is logged. Check these first when something misbehaves:

* `logs/errors.log` — errors only, with full tracebacks
* `logs/g1.log` — full detail (rotating)
* console — INFO live view

## Robot mode

The G1 must be in its normal high-level control state (standing, arms free) for
audio + arm-action services to respond — **not** Debug mode (which disables the
high-level RPC) and not a walking/sport gait. The exact R3 remote combo is
firmware-dependent; see the deployment runbook and `g1-teleop/06-runbook.md`.

## Known limitations / next steps

* No acoustic echo cancellation yet — the mic is flushed during playback to avoid
  self-triggering, so the user can't barge in mid-reply. Add AEC or a robot-mic
  with onboard AEC later.
* Streaming is on by default (`STREAMING_ENABLED`): the reply is streamed and spoken
  sentence-by-sentence so the robot starts talking after the first sentence. **Chunked
  speech** (`TTS_CHUNKING_ENABLED`) additionally splits a long reply into small pieces
  sent to TTS one at a time (prefetched) for fast first audio — both toggle from the
  panel's Speech tab.
* The robot does **one** arm move when it starts talking (`TALK_GESTURE_IDS`, default
  right-hand-up) and waves near its head on wake (`WAKE_GESTURE_ID`); pick both from the
  panel's Gestures tab. Ids are firmware presets — confirm with `tools/g1_list_actions.py`.
* The head LED shows state by colour (standby blue · listening green · thinking amber,
  breathing · speaking magenta · error red); colours are editable in the Environment tab.
* Knowledge retrieval is keyword-based (no embeddings) — great for a small curated
  KB; swap in embeddings if the KB grows large.
