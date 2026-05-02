"""
TTS engine — wraps OpenAI Text-to-Speech API for narration generation.
Generates .mp3 audio files from narration text, one per tutorial step.
"""

import asyncio
import logging
from pathlib import Path
from typing import Optional

from openai import AsyncOpenAI

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "tts-1"
DEFAULT_VOICE = "alloy"
WORDS_PER_MINUTE = 150


class TTSEngine:
    """Generates narration audio using OpenAI TTS API."""

    def __init__(
        self,
        api_key: str,
        output_dir: Path,
        model: str = DEFAULT_MODEL,
        voice: str = DEFAULT_VOICE,
    ):
        self._client = AsyncOpenAI(api_key=api_key)
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.model = model
        self.default_voice = voice

    async def generate_audio(
        self, text: str, filename_prefix: str, voice: Optional[str] = None
    ) -> Path:
        output_path = self.output_dir / f"{filename_prefix}.mp3"
        selected_voice = voice or self.default_voice
        logger.info(
            f"Generating TTS audio: {filename_prefix} ({len(text)} chars, voice={selected_voice})"
        )
        response = await self._client.audio.speech.create(
            model=self.model, voice=selected_voice, input=text
        )
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            None,
            response.stream_to_file,
            str(output_path),
        )
        logger.info(f"TTS audio saved to {output_path}")
        return output_path

    @staticmethod
    def estimate_duration(text: str) -> float:
        if not text.strip():
            return 0.0
        word_count = len(text.split())
        return (word_count / WORDS_PER_MINUTE) * 60
