"""FastAPI app for the G1 Interactive control panel.

Implements process control, a live log console, the .env editor, knowledge-base and
persona editors, script running, and a transcript + cost view. The static SPA in
``static/`` calls these routes. All ``/api`` requests are gated by ``PANEL_TOKEN``
when it is set (header ``X-Panel-Token`` or ``?token=``); WebSockets check ``?token=``.
"""
from __future__ import annotations

import json
import os

from fastapi import Body, FastAPI, File, HTTPException, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import cost, dialogflow, env_file, gestures, logtail, movement, paths, speech
from .process_manager import ProcessManager
from .scripts import ScriptRunner, list_scripts, save_upload

paths.ensure_state_dir()
paths.STATIC_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="G1 Interactive Control Panel")
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
)

pm = ProcessManager()
runner = ScriptRunner()
_VALID_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR"}


def _token() -> str:
    return os.environ.get("PANEL_TOKEN") or (env_file.get(paths.ENV_FILE, "PANEL_TOKEN") or "")


@app.on_event("startup")
async def _startup() -> None:
    if not _token():
        print("[control-panel] WARNING: PANEL_TOKEN not set — the API is open to the LAN.")


@app.middleware("http")
async def _auth(request: Request, call_next):
    if request.url.path.startswith("/api"):
        token = _token()
        if token:
            sent = request.headers.get("X-Panel-Token") or request.query_params.get("token")
            if sent != token:
                return JSONResponse({"detail": "bad or missing panel token"}, status_code=401)
    return await call_next(request)


def _ws_ok(ws: WebSocket) -> bool:
    token = _token()
    if not token:
        return True
    sent = ws.query_params.get("token") or ws.headers.get("x-panel-token")
    return sent == token


# ---- static SPA ----
app.mount("/static", StaticFiles(directory=str(paths.STATIC_DIR)), name="static")


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(str(paths.STATIC_DIR / "index.html"))


@app.get("/api/health")
async def health() -> dict:
    return {"ok": True}


# ---- process control (sync defs -> run in threadpool; they block briefly) ----
@app.get("/api/status")
def status() -> dict:
    return pm.status()


@app.post("/api/start")
def start() -> dict:
    return pm.start()


@app.post("/api/stop")
def stop() -> dict:
    return pm.stop()


@app.post("/api/restart")
def restart() -> dict:
    return pm.restart()


@app.post("/api/service/install")
def service_install() -> dict:
    return pm.install_service()


# ---- logs / console ----
@app.get("/api/logs")
async def get_logs(file: str = "g1", lines: int = 200) -> dict:
    path = paths.LOG_FILES.get(file)
    if path is None:
        raise HTTPException(400, "unknown log file")
    return {"file": file, "lines": logtail.read_last(path, lines)}


@app.get("/api/loglevel")
async def get_loglevel() -> dict:
    return {"level": env_file.get(paths.ENV_FILE, "LOG_LEVEL") or "INFO"}


@app.post("/api/loglevel")
async def set_loglevel(payload: dict = Body(...)) -> dict:
    level = str(payload.get("level", "INFO")).upper()
    if level not in _VALID_LEVELS:
        raise HTTPException(400, "level must be one of DEBUG/INFO/WARNING/ERROR")
    env_file.update(paths.ENV_FILE, {"LOG_LEVEL": level})
    return {"level": level, "restart_required": True}


@app.websocket("/ws/logs")
async def ws_logs(ws: WebSocket) -> None:
    await ws.accept()
    if not _ws_ok(ws):
        await ws.close(code=1008)
        return
    name = ws.query_params.get("file", "g1")
    path = paths.LOG_FILES.get(name, paths.LOG_FILES["g1"])
    try:
        async for line in logtail.follow(path, last_n=100):
            await ws.send_json({"line": line})
    except (WebSocketDisconnect, RuntimeError):
        pass
    except Exception:
        pass


# ---- environment (.env) ----
@app.get("/api/env")
async def get_env() -> dict:
    return {"groups": env_file.grouped(paths.ENV_FILE)}


@app.post("/api/env")
async def set_env(payload: dict = Body(...)) -> dict:
    updates = payload.get("updates") or {}
    # Empty value = "leave unchanged" (so masked secrets aren't blanked).
    clean = {k: str(v) for k, v in updates.items() if v is not None and str(v) != ""}
    if clean:
        env_file.update(paths.ENV_FILE, clean)
    return {"ok": True, "restart_required": bool(clean)}


# ---- knowledge base ----
@app.get("/api/kb")
async def kb_list() -> dict:
    files = []
    if paths.KNOWLEDGE_DIR.exists():
        for p in sorted(paths.KNOWLEDGE_DIR.glob("*")):
            if p.is_file() and p.suffix.lower() in (".md", ".txt"):
                st = p.stat()
                files.append({"name": p.name, "bytes": st.st_size, "modified": st.st_mtime})
    return {"files": files}


def _kb_path(name: str):
    try:
        return paths.safe_child(paths.KNOWLEDGE_DIR, name, (".md", ".txt"))
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@app.get("/api/kb/{name}")
async def kb_get(name: str) -> dict:
    p = _kb_path(name)
    if not p.exists():
        raise HTTPException(404, "not found")
    return {"name": name, "content": p.read_text(encoding="utf-8", errors="replace")}


@app.put("/api/kb/{name}")
async def kb_put(name: str, payload: dict = Body(...)) -> dict:
    p = _kb_path(name)
    paths.KNOWLEDGE_DIR.mkdir(parents=True, exist_ok=True)
    p.write_text(payload.get("content", ""), encoding="utf-8")
    return {"ok": True}


@app.post("/api/kb")
async def kb_create(payload: dict = Body(...)) -> dict:
    name = (payload.get("name") or "").strip()
    if not name:
        raise HTTPException(400, "name required")
    if not (name.endswith(".md") or name.endswith(".txt")):
        name += ".md"
    p = _kb_path(name)
    paths.KNOWLEDGE_DIR.mkdir(parents=True, exist_ok=True)
    if p.exists():
        raise HTTPException(409, "already exists")
    p.write_text(payload.get("content", ""), encoding="utf-8")
    return {"ok": True, "name": p.name}


@app.delete("/api/kb/{name}")
async def kb_delete(name: str) -> dict:
    p = _kb_path(name)
    if p.exists():
        p.unlink()
    return {"ok": True}


# ---- instructions / persona ----
@app.get("/api/persona")
async def persona_get() -> dict:
    p = paths.PERSONA_FILE
    return {"content": p.read_text(encoding="utf-8", errors="replace") if p.exists() else ""}


@app.put("/api/persona")
async def persona_put(payload: dict = Body(...)) -> dict:
    paths.PROMPTS_DIR.mkdir(parents=True, exist_ok=True)
    paths.PERSONA_FILE.write_text(payload.get("content", ""), encoding="utf-8")
    return {"ok": True, "restart_required": True}


# ---- gestures (friendly picker over TALK_GESTURE_IDS / WAKE_GESTURE_ID) ----
@app.get("/api/gestures")
async def get_gestures() -> dict:
    return gestures.get_config()


@app.post("/api/gestures")
async def set_gestures(payload: dict = Body(...)) -> dict:
    gestures.set_config(payload.get("talk_ids"), payload.get("wake_id"))
    return {"ok": True, "restart_required": True}


# ---- movement (experimental voice locomotion) ----
@app.get("/api/movement")
async def get_movement() -> dict:
    return movement.get_config()


@app.post("/api/movement")
async def set_movement(payload: dict = Body(...)) -> dict:
    changed = movement.set_config(
        enabled=payload.get("enabled"),
        speed=payload.get("speed"),
        yaw=payload.get("yaw"),
        duration_s=payload.get("duration_s"),
    )
    return {"ok": True, "restart_required": bool(changed)}


# ---- speech (streaming + chunking latency toggles) ----
@app.get("/api/speech")
async def get_speech() -> dict:
    return speech.get_config()


@app.post("/api/speech")
async def set_speech(payload: dict = Body(...)) -> dict:
    changed = speech.set_config(
        streaming=payload.get("streaming"),
        chunking=payload.get("chunking"),
        chunk_max_chars=payload.get("chunk_max_chars"),
        stt_backend=payload.get("stt_backend"),
    )
    return {"ok": True, "restart_required": bool(changed)}


# ---- dialogflow (answer-first toggle + live test) ----
@app.get("/api/dialogflow")
async def get_dialogflow() -> dict:
    return dialogflow.get_config()


@app.post("/api/dialogflow")
async def set_dialogflow(payload: dict = Body(...)) -> dict:
    changed = dialogflow.set_config(
        enabled=payload.get("enabled"),
        confidence=payload.get("confidence"),
        project=payload.get("project"),
        location=payload.get("location"),
        agent_id=payload.get("agent_id"),
        key_path=payload.get("key_path"),
    )
    return {"ok": True, "restart_required": bool(changed)}


@app.post("/api/dialogflow/test")
def test_dialogflow(payload: dict = Body(...)) -> dict:
    # Sync def -> Starlette runs it in the threadpool; detect_intent is a blocking gRPC
    # round-trip and must NOT run on the event loop (it would freeze the whole panel).
    return dialogflow.test_query(payload.get("query", ""))


# ---- scripts ----
@app.get("/api/scripts")
async def scripts_list() -> dict:
    return {"scripts": list_scripts()}


@app.post("/api/scripts/run")
async def scripts_run(payload: dict = Body(...)) -> dict:
    name = payload.get("name", "")
    directory = payload.get("dir", "tools")
    try:
        run_id = await runner.start(name, directory)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except FileNotFoundError:
        raise HTTPException(404, "script not found")
    return {"run_id": run_id}


@app.post("/api/scripts")
async def scripts_upload(file: UploadFile = File(...)) -> dict:
    data = await file.read()
    try:
        name = save_upload(file.filename or "uploaded.py", data)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {"ok": True, "name": name}


@app.websocket("/ws/script/{run_id}")
async def ws_script(ws: WebSocket, run_id: str) -> None:
    await ws.accept()
    if not _ws_ok(ws):
        await ws.close(code=1008)
        return
    try:
        async for kind, value in runner.stream(run_id):
            if kind == "line":
                await ws.send_json({"line": value})
            else:
                await ws.send_json({"done": True, "code": value})
    except (WebSocketDisconnect, RuntimeError):
        pass
    except Exception:
        pass


# ---- transcript + cost ----
@app.get("/api/transcript")
async def transcript(lines: int = 50) -> dict:
    turns = []
    for line in logtail.read_last(paths.EVENTS_FILE, lines):
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except Exception:
            continue
        if ev.get("type") == "turn":
            turns.append(ev)
    return {"turns": turns}


@app.websocket("/ws/transcript")
async def ws_transcript(ws: WebSocket) -> None:
    await ws.accept()
    if not _ws_ok(ws):
        await ws.close(code=1008)
        return
    try:
        async for line in logtail.follow(paths.EVENTS_FILE, last_n=20):
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except Exception:
                continue
            if ev.get("type") == "turn":
                await ws.send_json(ev)
    except (WebSocketDisconnect, RuntimeError):
        pass
    except Exception:
        pass


@app.get("/api/cost")
async def get_cost() -> dict:
    return cost.compute()


@app.get("/api/prices")
async def get_prices() -> dict:
    return {"prices": cost.load_prices()}


@app.post("/api/prices")
async def set_prices(payload: dict = Body(...)) -> dict:
    return {"ok": True, "prices": cost.save_prices(payload.get("prices") or {})}
