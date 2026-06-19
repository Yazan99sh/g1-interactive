# Research notes — faster STT & custom G1 gestures

Two investigations requested for the G1 interactive robot. Findings are verified
against primary sources (provider docs, GitHub) as of June 2026.

---

## 1. Faster speech-to-text (Arabic + English)

**Shipped now:** a selectable STT backend (`STT_BACKEND`, panel → Speech tab):

| Backend | Model | Why |
|---|---|---|
| `openai` (default) | `gpt-4o-transcribe` | what we already used; safe default |
| `groq` | `whisper-large-v3-turbo` | **much faster + ~1/9th the cost, strong Arabic + English** |

Groq exposes an **OpenAI-compatible** transcription endpoint, so it's a drop-in: same
multipart request, just a different base URL / key / model. Set `GROQ_API_KEY` and pick
"Groq" in the Speech tab; it falls back to OpenAI automatically if the key is missing.

- Endpoint: `https://api.groq.com/openai/v1/audio/transcriptions`
- Verified: Whisper-class Arabic quality, ~216× real-time inference speed, $0.04/hr
  (10-second minimum billing per request). Source: Groq STT docs + pricing.
- **Caveat:** Groq is **batch-only** (no streaming partials) — but so is our pipeline
  (we buffer a whole utterance, then transcribe), so it's a pure latency win with no
  architecture change. Our turn-based "listen → think → speak" loop doesn't need
  partials.

**If we later want true streaming** (robot reacts mid-sentence), the verified best
options with first-class Arabic are:
- **Deepgram Nova-3 Arabic** — dedicated Arabic model (17 dialects), WebSocket streaming,
  ~$0.0077/min. Best streaming-with-Arabic story; would be the next selectable backend.
- **ElevenLabs Scribe v2 Realtime** — best published Arabic accuracy (~3.1% WER), ~150 ms
  streaming latency, pricier.

**Avoid for us:** AssemblyAI Universal-Streaming and NVIDIA Parakeet/Canary — **no Arabic**.
Azure/Google support Arabic streaming but are ~25× Groq's price with heavier SDKs.

---

## 2. Custom gestures on the G1 (beyond the ~10 preset arm actions)

We currently call `G1ArmActionClient.ExecuteAction(action_id)` — fixed presets only. To
play **arbitrary** gestures, drop one level to the **`rt/arm_sdk`** DDS topic, which is
already in the `unitree_sdk2_python` package we have installed (no new dependency).

**Lowest-effort path (recommended for an arm-only greeting robot):**
- Reference: `example/g1/high_level/g1_arm7_sdk_dds_example.py` in `unitree_sdk2_python`.
- Publish `LowCmd_` (IDL **`unitree_hg`**, not `unitree_go`) to topic `rt/arm_sdk`;
  subscribe `rt/lowstate` (`LowState_`) to read the current pose first.
- The **weight blend trick:** joint index **29 (`kNotUsedJoint`)** isn't a motor — its
  `q` is the arm_sdk master weight. Ramp it `0 → 1` to take arm control, `1 → 0` to hand
  it back. Always start from the *measured* current pose and ramp gradually, or the arm
  lurches at high speed.
- 7-DOF arm joint indices — left: 15-21, right: 22-28. Loop at 50 Hz (`dt=0.02`),
  `kp≈60, kd≈1.5`, set `low_cmd.crc = CRC().Crc(low_cmd)` each tick.
- Author gestures as joint-space keyframes: put the robot in damping/hold, pose the arm
  by hand, read `rt/lowstate`, save the angles, interpolate between keyframes. No ML.
- **Safety:** `rt/arm_sdk` has **no self-collision avoidance** — clamp to joint limits,
  keep velocities low, validate slowly, keep clear space. Requires a standing FSM where
  the arms are free.

**Dexterous hands (if fitted):** controlled over their own DDS topics, separate from the
arm — Dex3-1 via `rt/dex3/{left,right}/cmd` (`HandCmd_`); Inspire RH56DFX via the official
`dfx_inspire_service` (`MotorCmds_` → `rt/inspire/cmd`). A single open-palm / wave-fingers
pose can be sent alongside an arm gesture.

**Advanced path (record real motions):** `xr_teleoperate` (Unitree's official tool, the
renamed `avp_teleoperate`) teleoperates G1 arms + hands from a Quest 3 / Apple Vision Pro
and **records episodes** (press `s`). The recorded JSON is per-frame joint vectors — replay
them straight through the same `rt/arm_sdk` 50 Hz loop (no ML needed), or convert via
`unitree_lerobot` to train a policy. Heavier setup; worth it only for lifelike,
hard-to-hand-tune motions.

**Recommendation:** for a handful of greeting gestures, hand-authored `rt/arm_sdk`
keyframes are far less effort than the VR stack. This is a clean follow-up feature: a
`CustomGesture` player module that loads keyframe JSON and streams it over `rt/arm_sdk`,
selectable as a `TALK_GESTURE`/`WAKE_GESTURE` alongside the existing presets. Not
implemented yet — it moves the arms via low-level control and must be validated on the
real robot first.

**Skeptic flags:** `dex-retargeting` is a converter, not a player. LAFAN1 is full-body,
kinematic-only (not a greeting library). `OpenTeleVision` and community ROS2 stacks are
research/community — verify license + maintenance before depending on them. Use
`xr_teleoperate`, not the deprecated `avp_teleoperate` name.

### Key repos
| Repo | Use |
|---|---|
| `unitreerobotics/unitree_sdk2_python` | **already installed** — `g1_arm7_sdk_dds_example.py` is the custom-gesture reference |
| `unitreerobotics/xr_teleoperate` | VR record→replay of arms + hands (was `avp_teleoperate`) |
| `unitreerobotics/dfx_inspire_service` | Inspire hand control over DDS |
| `unitreerobotics/unitree_lerobot` | convert/train recorded episodes (optional) |
| `dexsuite/dex-retargeting` | human→robot hand retargeting (a component, MIT) |
