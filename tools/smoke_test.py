"""Cloud-path smoke test — no robot, no microphone.

Type a line; the script runs it through Think (KB + LLM) and Respond (ElevenLabs
TTS) and plays the answer on your PC speakers. Use this to confirm the API keys
and the whole reasoning/speech path work before deploying to the robot.

    python tools/smoke_test.py
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx  # noqa: E402

from ai.knowledge_base import KnowledgeBase  # noqa: E402
from ai.llm import LLMEngine  # noqa: E402
from ai.stt import OpenAITranscriber  # noqa: E402
from ai.tts import ElevenLabsTTS  # noqa: E402
from app.conversation import ConversationManager  # noqa: E402
from app.logging_setup import setup_logging  # noqa: E402
from app.pipeline import ConversationPipeline  # noqa: E402
from app.state import Transcription, detect_language  # noqa: E402
from audio.sink import HostSpeaker  # noqa: E402
from config import settings  # noqa: E402
from robot.interfaces import NullArmController  # noqa: E402


async def amain() -> None:
    setup_logging(settings.LOG_DIR, "INFO")
    http = httpx.AsyncClient()
    try:
        pipeline = ConversationPipeline(
            transcriber=OpenAITranscriber(http),
            llm=LLMEngine(http),
            tts=ElevenLabsTTS(http),
            kb=KnowledgeBase(settings.KNOWLEDGE_DIR),
            conversation=ConversationManager(),
            arm=NullArmController(),
            sink=HostSpeaker(),
        )
        print("\nType something for the robot (blank line / Ctrl+C to quit).\n")
        loop = asyncio.get_running_loop()
        while True:
            text = (await loop.run_in_executor(None, input, "you> ")).strip()
            if not text:
                break
            transcription = Transcription(text=text, language=detect_language(text))
            reply = await pipeline.think(transcription)
            print(f"robot> [{reply.emotion.value}] {reply.text}")
            await pipeline.respond(reply)
    finally:
        await http.aclose()


if __name__ == "__main__":
    try:
        asyncio.run(amain())
    except KeyboardInterrupt:
        pass
