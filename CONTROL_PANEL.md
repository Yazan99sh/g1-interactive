# G1 Interactive — Control Panel

A small **web** panel (FastAPI) that runs on the same Linux host as the voice
pipeline and manages the whole feature from a browser — on the host, the dev PC, or
a phone on the LAN. It's headless (no display needed) and can drive the pipeline
whether it runs as a **systemd service** or as a **panel-managed subprocess**
(auto-detected).

## What it does

| Tab | What you can do |
|-----|-----------------|
| **Dashboard** | Pipeline status (running/stopped + mode), turns logged, estimated API cost, install-as-service button. If the pipeline **fails to start**, shows the exit code + last output so you can see why |
| **Console** | Live `g1.log` / `errors.log` / `pipeline (stdout)` stream, pause/clear, change the **log level** |
| **Conversation** | Live transcript (what the visitor said / what the robot said, with language + emotion), estimated cost with an editable price table |
| **Knowledge** | Browse/edit/create/delete the `knowledge/*.md` FAQ + facts |
| **Instructions** | Edit the robot's persona / system prompt (`prompts/persona.md`) |
| **Gestures** | Pick the one arm move the robot makes when it starts talking, and the wake / meet-and-greet wave — named-gesture dropdowns (no raw ids). Also the **experimental Movement** toggle (voice "move forward/left/…", speed + duration) |
| **Models** | Pick the **LLM** provider + model (Gemini 3.1 Flash-Lite, GPT-4o mini via OpenRouter/OpenAI, GPT-4o) and the **ElevenLabs TTS** model (Flash v2.5 ultra-low latency / Eleven v3 expressive / …). Writes `LLM_BACKEND` + the matching model key and `ELEVENLABS_MODEL`; shows ⚠️ when the chosen provider's API key is missing |
| **Speech** | Pick the **STT engine** (OpenAI / Groq Whisper), toggle streaming + chunked speech and set the piece size — controls how fast the robot listens + starts talking |
| **Dialogflow** | Turn **Dialogflow CX answer-first** on/off (LLM fallback), set the agent (project/location/agent id/key) + confidence, and **live-test** the agent from a query box |
| **Web Search** | Turn **Brave web search** on/off, set how many results to use + the spoken "let me search" announcement (EN/AR), and **live-test** a query |
| **Memory** | The robot's **brain**: toggle per-session host snapshots + **long-term memory** (teams/supervisors persist, visitors expire), set retention + recall depth, browse/edit/delete saved memories, view session snapshots, and **forget all visitors** now |
| **Vision** | **Peek** — turn the head camera + "look at / show me / what do you see" on/off, set the Jetson snapshot URL + vision model + spoken announcements (EN/AR), and **test-capture** one frame |
| **Teleop** | **Teleop mode** — one switch that releases the **arms, locomotion & head camera** so a VR/arm teleoperator (`xr_teleoperate`) can drive the body while the robot keeps **talking** (mic/speaker/LED stay on). Overrides your gesture/movement/camera settings without changing them |
| **Environment** | Edit `.env` — API keys (masked), **ElevenLabs voice id**, head-LED colours, and every tunable, grouped by section |
| **Scripts** | Run the diagnostic/action scripts in `tools/` and `scripts/` with live output; upload new `.py` scripts |

Start/Stop/Restart buttons are in the top bar. Settings that need a restart (env,
instructions, log level) raise a "restart to apply" banner.

## Install & run (on the host)

```bash
python -m pip install -r controlpanel/requirements.txt   # fastapi, uvicorn, multipart
python -m controlpanel                                    # -> http://<host>:8800
```

Host/port come from `PANEL_HOST` (default `0.0.0.0`) and `PANEL_PORT` (default `8800`).

### Run both as services (survive reboot/logout)

```bash
bash deploy/install_services.sh          # installs g1-interactive + g1-control-panel (systemctl --user)
systemctl --user start g1-control-panel  # the panel
systemctl --user start g1-interactive    # the voice pipeline (or use the panel's Start button)
journalctl --user -u g1-interactive -f   # live logs
```

`deploy/uninstall_services.sh` removes them. Once the user unit exists, the panel
manages the pipeline through systemd automatically; otherwise it launches
`python main.py` itself and tracks the PID.

## Security

The panel can edit `.env` and **run scripts**, so keep it on the trusted robot LAN.
To require a shared token, set `PANEL_TOKEN` (in `.env` or the panel's service
`Environment=`). Then open the panel as `http://<host>:8800/?token=YOURTOKEN` — the
UI attaches it to every request; calls without it get `401`. KB/script names are
confined to their folders (no path traversal) and scripts run as `python <file>`
(never a shell).

## Notes

- Editing knowledge / instructions / env takes effect on the **next pipeline start**
  — hit Restart (or the banner button).
- Costs are **estimates** (tokens approximated from character counts); tune the
  price table on the Conversation tab to match your plans.
- See `RUNBOOK.md` for the robot bring-up order and `README.md` for architecture.
```
