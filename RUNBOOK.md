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
# inside a conda/venv (reuse the teleop env if it has unitree_sdk2_python)
python -m pip install -r requirements.txt

# Robot DDS SDK (not on PyPI) — same as teleop:
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
(`IDLE_AFTER_SILENT_TURNS`) it returns to standby until the next "Hi Robot".

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
| Wrong gesture fires | firmware action-id differs | run `g1_list_actions.py`, update ids in `robot/arm_gestures.py` |
| Robot triggers itself / echoes | speaker feeding the mic | mic is flushed between turns; move USB mic away from speaker; lower volume |
| ElevenLabs 401/403 on PCM | plan can't output PCM | use a Creator+ key, or set `TTS_OUTPUT_FORMAT=mp3_44100_128` + `pip install miniaudio` |
| Wake word never triggers | mic too quiet / phrase mismatch | lower `SILENCE_RMS_THRESHOLD`; add phrases to `WAKE_WORDS` |

---

## 7. Safety

* First gesture tests with the robot **on a gantry** or with damping (`L1+A`/`L2+B`)
  in hand. Arm-only — never enable walking/Running mode for this app.
* The DDS bus is unauthenticated — keep the robot LAN air-gapped from the internet
  (host gets internet on a separate NIC only).
