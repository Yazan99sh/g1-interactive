# G1 Interactive — Deployment Runbook

How to deploy and bring up the interactive voice pipeline on the host that talks
to the G1, using the **same wired-LAN setup as teleop**: a Linux PC on the robot's
`192.168.123.x` network (robot wired to the TP-Link router, host wired to the same
router), talking CycloneDDS to the G1.

> Legend: ✅ source-verified · ⚠️ confirm on hardware

---

## 0. Architecture

```
  [ Person ]  --speaks-->  USB mic on host   (MIC_SOURCE=host, recommended)
                                  │
            ┌─────────────────────▼──────────────────────┐
            │  Host PC (Linux, 192.168.123.x, wired)      │
            │  python main.py                             │
            │   OpenAI STT → KB+LLM → ElevenLabs (pcm16k) │
            └───────────────┬──────────────┬──────────────┘
                 DDS "voice" │              │ DDS "arm"
                  PlayStream  ▼              ▼ ExecuteAction
            [ G1 speaker (16k PCM) ]   [ G1 arms gesture ]
```

Internet is needed (OpenAI / ElevenLabs / OpenRouter). The robot LAN itself can be
WAN-less; give the **host** internet on a second interface (NAT/Wi-Fi), exactly
like the teleop VM (`ens33` NAT for internet + `ens37` for the robot LAN).

---

## 1. Install (on the host)

```bash
# Fast path on a fresh host (venv/conda + core+panel deps + .env bootstrap):
./setup.sh                 # add --with-sdk to also install unitree_sdk2_python
python tools/doctor.py     # preflight: keys, DDS iface, robot ping, audio devices

# …or by hand, inside a conda/venv (reuse the teleop env if it has the SDK):
python -m pip install -r requirements.txt
python -m pip install -r controlpanel/requirements.txt   # for the control panel
git clone https://github.com/unitreerobotics/unitree_sdk2_python
cd unitree_sdk2_python && pip install -e . && cd -
```

`.env` already holds the shared API keys. Set the deployment knobs:

```ini
ROBOT_ENABLED=true
AUDIO_SINK=robot            # speak out the G1 speaker
MIC_SOURCE=host            # USB mic near the robot (most reliable)
DDS_INTERFACE=ens37        # ⚠️ the host NIC on the 192.168.123.x net (see step 2)
ARM_GESTURES_ENABLED=true
ARM_ENTER_FSM=false        # you set the robot mode by remote (safer)
```

---

## 2. Network check (find the DDS interface) ⚠️

```bash
ip addr                     # find the iface holding your 192.168.123.x address
ping 192.168.123.161        # PC1 (locomotion board) must reply
ping 192.168.123.164        # PC2 (Jetson) should reply
```

Put that iface name in `DDS_INTERFACE`. A wrong NIC = silent DDS timeouts (the
app will fall back to host speaker / log errors).

---

## 3. Robot mode (R3 remote) ⚠️ firmware-dependent

* **Audio + LED need NO special mode** — the speaker works as soon as the robot is
  powered and DDS is up. ✅
* **Arm gestures need the robot standing in Main/Regular mode:**
  * Main / Regular operation mode: **`R1 + X`** (FW 1.2 & 1.4)
  * Lock standing: **`L1 + ↑`** (FW 1.2) / `L2 + ↑` variant (FW 1.4)
  * Keep hardware damping / soft e-stop in hand: **`L1 + A`** (FW1.2) / **`L2 + B`** (FW1.4)
  * ⛔ Never Running mode (`R2 + A`) or Debug mode (`L2 + R2`, disables high-level RPC).

Cross-check against `g1-teleop/06-runbook.md` (R3 combo table) for your firmware.

---

## 4. Bring-up sequence

Run these in order; each gates the next.

| # | Command | Confirms |
|---|---------|----------|
| 1 | `python tools/smoke_test.py` (any machine) | API keys + STT/LLM/TTS work; you hear a reply on PC speakers |
| 2 | `python tools/_selftest.py` | wake/VAD/KB/state logic all green |
| 3 | `python tools/list_audio_devices.py` | pick the USB mic index → `MIC_DEVICE` |
| 4 | `python tools/g1_speaker_test.py` | **clean 440 Hz beep from the robot** = DDS iface + PCM path good |
| 5 | `python tools/g1_list_actions.py` | arm preset ids match `arm_gestures.py` (25/26/17/27/99) |
| 6 | put robot in Main mode (R1+X) + standing, e-stop in hand | gestures will be accepted |
| 7 | `python main.py` then say **"Hi Robot"** | full pipeline: "Aha!" + wave → conversation |

The robot answers **"Aha!"** and waves, then listens. After **10** silent turns
(`IDLE_AFTER_SILENT_TURNS`) it returns to standby until the next "Hi Robot". The
robot now **gestures continuously while it speaks** and **starts talking after the
first sentence** (streaming) — see `TALK_GESTURE_*` and `STREAMING_ENABLED`.

### Control panel (optional, recommended)

Instead of editing files + restarting by hand, run the web panel and do it from a
browser (start/stop, live logs, env/keys/voice, knowledge, instructions, scripts,
transcript+cost):

```bash
python -m controlpanel            # -> http://<host>:8800
# or install both as services:  bash deploy/install_services.sh
```

See `CONTROL_PANEL.md`.

### Head camera for "peek" (optional)

"Peek" (look at something + describe it out loud) needs a frame from the G1 head
camera. The G1 has **no DDS video service** (that was Go2-only) — the head RealSense
is on the **Jetson (PC2, `192.168.123.164`)** over USB. Run a tiny helper there that
serves one JPEG over HTTP, then point the app at it:

```bash
ssh unitree@192.168.123.164          # password: 123
rs-enumerate-devices                 # confirm the RealSense is seen (or: lsusb | grep -i Intel)
pip install pyrealsense2 opencv-python
python3 jetson_camera_server.py      # serves http://0.0.0.0:8090/snapshot
```

On the voice-app host, set (Vision tab or `.env`): `CAMERA_ENABLED=true`,
`CAMERA_SNAPSHOT_URL=http://192.168.123.164:8090/snapshot`. Test from the panel's
**Vision** tab ("Capture test frame") or `curl http://192.168.123.164:8090/snapshot`.
If your batch wired the RealSense to PC1 (`192.168.123.161`), run the helper there.

### Memory (optional)

Session snapshots are on by default → `brain/sessions` + `brain/logs` (browse in the
panel's **Memory** tab). For memory **across** sessions, set `LONG_TERM_MEMORY_ENABLED=true`;
teams/supervisors persist, visitors expire (`MEMORY_VISITOR_TTL_DAYS`).

### Teleop mode — run the voice app while teleoperating the arms (optional)

To drive the G1's arms from VR (`xr_teleoperate`) **and** keep the robot talking, turn on
**teleop mode** so the voice app lets go of the body. It controls the arms over the
low-level `arm_sdk` interface, which can't be shared with the voice app's arm gestures.

1. Panel **🕹️ Teleop** tab → enable **Teleop mode** → **Restart** (or set `TELEOP_MODE=true`
   in `.env` and restart). This forces **arm gestures, voice movement and the head camera**
   off — your individual settings are kept and come back when you turn it off.
2. Start **xr_teleoperate first** (it claims arm control), *then* the voice pipeline. With
   teleop mode on, the voice app holds **no** arm client and **no** `LocoClient`, so it can't
   interfere — while the **mic, speaker and head LED keep working** (the robot still talks).

The head camera is left for the teleoperator's video feed. If you want peek *and* teleop at
once, both must read the camera over **DDS** (multiple subscribers coexist); a direct
RealSense device-open collides with the on-robot `videohub` service.

---

## 5. Logs

* `logs/errors.log` — errors only, full tracebacks (check this first)
* `logs/g1.log` — full rotating detail
* console — live INFO

---

## 6. Troubleshooting

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| Falls back to "HostSpeaker"/"NullArmController" at startup | SDK import failed or DDS init failed | install `unitree_sdk2_python`; check `DDS_INTERFACE`; see `errors.log` |
| Test tone is garbled / wrong pitch | PCM not 16k mono s16le | keep `TTS_OUTPUT_FORMAT=pcm_16000`; tone test uses 16k already |
| No sound at all from robot | wrong DDS iface, or volume 0 | fix `DDS_INTERFACE`; raise `ROBOT_SPEAKER_VOLUME` |
| Speech ok but arms never move | robot not in Main mode / arm FSM | R1+X + stand; or set `ARM_ENTER_FSM=true` (commands LocoClient.Start) |
| Arms wave once then freeze mid-reply | `TALK_GESTURES_ENABLED=false` | set it true (default); the talk loop gestures the whole reply |
| Long pause before the robot talks | non-streaming, or slow network | keep `STREAMING_ENABLED=true`; check internet (STT/LLM/TTS are cloud) |
| Standby costs add up / slow trigger | STT wake engine | set `WAKE_ENGINE=openwakeword` + `pip install openwakeword` (English/phrase-only) |
| Wrong gesture fires | firmware action-id differs | run `g1_list_actions.py`, update ids in `robot/arm_gestures.py` |
| Robot triggers itself / echoes | speaker feeding the mic | mic is flushed between turns; move USB mic away from speaker; lower volume |
| ElevenLabs 401/403 on PCM | plan can't output PCM | use a Creator+ key, or set `TTS_OUTPUT_FORMAT=mp3_44100_128` + `pip install miniaudio` |
| Wake word never triggers | mic too quiet / phrase mismatch | lower `SILENCE_RMS_THRESHOLD`; add phrases to `WAKE_WORDS` |
| "Peek" says "I couldn't get a clear look" | Jetson helper down / wrong URL / camera on PC1 | start `jetson_camera_server.py` on the Jetson; check `CAMERA_SNAPSHOT_URL`; `curl` it; run the helper on PC1 if that's where the RealSense is |
| Robot ignores short/garbled phrases | noise filter (by design) | it treats noise/too-short speech as silence; lower `NOISE_MIN_CHARS` or trim `NOISE_BLOCKLIST` if it's too aggressive |
| Robot "forgets" people every restart | long-term memory off (default) | set `LONG_TERM_MEMORY_ENABLED=true`; only teams/supervisors persist — visitors expire by design |

---

## 7. Safety

* First gesture tests with the robot **on a gantry** or with damping (`L1+A`/`L2+B`)
  in hand. Arm-only — never enable walking/Running mode for this app.
* The DDS bus is unauthenticated — keep the robot LAN air-gapped from the internet
  (host gets internet on a separate NIC only).
