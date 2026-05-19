from __future__ import annotations

import logging
import os
import shutil
import tempfile
from pathlib import Path

from src.audio_processor import AudioProcessor
from src.downloader import VideoDownloader, classify_url
from src.models import AgentState, TranscriptSegment
from src.transcriber import Transcriber

try:
    from dotenv import load_dotenv

    load_dotenv()  # load .env once; does not override existing env vars
except ImportError:  # python-dotenv optional at runtime
    pass

log = logging.getLogger(__name__)


def _config_from_env(
    model_size: str | None = None,
    device: str | None = None,
    compute_type: str | None = None,
    output_dir: str | None = None,
    cookies_file: str | None = None,
) -> dict:
    """Resolve config: explicit arg > env var > default. Empty env = unset."""

    def env(key: str) -> str | None:
        v = os.environ.get(key)
        return v if v else None

    return {
        "model_size": model_size or env("WHISPER_MODEL_SIZE") or "large-v3",
        "device": device or env("WHISPER_DEVICE") or "auto",
        "compute_type": compute_type or env("WHISPER_COMPUTE_TYPE") or "auto",
        "output_dir": output_dir or env("OUTPUT_DIR"),
        "cookies_file": cookies_file or env("COOKIES_FILE"),
    }


class TranscriptionAgent:
    def __init__(
        self,
        model_size: str | None = None,
        device: str | None = None,
        compute_type: str | None = None,
        output_dir: str | None = None,
        cookies_file: str | None = None,
    ):
        cfg = _config_from_env(
            model_size, device, compute_type, output_dir, cookies_file
        )
        self._work_dir = Path(
            cfg["output_dir"] or tempfile.mkdtemp(prefix="transcriber_")
        )
        self._downloader = VideoDownloader(
            output_dir=str(self._work_dir), cookies_file=cfg["cookies_file"]
        )
        self._audio = AudioProcessor()
        self._model_cfg = dict(
            model_size=cfg["model_size"],
            device=cfg["device"],
            compute_type=cfg["compute_type"],
        )
        self._transcriber_instance: Transcriber | None = None

    @property
    def _transcriber(self) -> Transcriber:
        # Lazy: defer the heavy WhisperModel load until actually transcribing.
        if self._transcriber_instance is None:
            self._transcriber_instance = Transcriber(**self._model_cfg)
        return self._transcriber_instance

    def __enter__(self) -> "TranscriptionAgent":
        return self

    def __exit__(self, *exc) -> None:
        self.cleanup()

    def transcribe(
        self,
        url: str,
        language: str | None = None,
        output_format: str = "text",
    ) -> str:
        state = AgentState(url=url, options={"language": language, "output_format": output_format})

        log.info("Classifying URL: %s", url)
        url_type = classify_url(url)
        state.plan = {"url_type": url_type}

        log.info("Downloading (%s)...", url_type)
        download = self._downloader.download(url, preferred_lang=language)

        # Shortcut: pre-existing captions skip transcription entirely.
        if download.has_captions:
            log.info("Using existing captions (%d segments)", len(download.caption_segments))
            return _format_segments(download.caption_segments, output_format)

        state.media_path = download.path

        log.info("Preparing audio...")
        chunks = self._audio.prepare_audio(state.media_path)
        state.chunks = chunks

        log.info("Transcribing %d chunk(s)...", len(chunks))
        segments = self._transcriber.transcribe_chunks(chunks, language=language)
        state.segments = segments

        log.info("Done. %d segments.", len(segments))
        return _format_segments(segments, output_format)

    def cleanup(self) -> None:
        shutil.rmtree(self._work_dir, ignore_errors=True)


def _format_segments(segments: list[TranscriptSegment], fmt: str) -> str:
    if fmt == "srt":
        return _to_srt(segments)
    if fmt == "vtt":
        return _to_vtt(segments)
    if fmt == "json":
        import json
        return json.dumps(
            [{"text": s.text, "start": s.start, "end": s.end} for s in segments],
            indent=2,
        )
    # Default: plain text
    return " ".join(s.text for s in segments).strip()


def _to_srt(segments: list[TranscriptSegment]) -> str:
    lines = []
    for i, seg in enumerate(segments, start=1):
        lines.append(str(i))
        lines.append(f"{_srt_ts(seg.start)} --> {_srt_ts(seg.end)}")
        lines.append(seg.text)
        lines.append("")
    return "\n".join(lines)


def _to_vtt(segments: list[TranscriptSegment]) -> str:
    lines = ["WEBVTT", ""]
    for seg in segments:
        lines.append(f"{_vtt_ts(seg.start)} --> {_vtt_ts(seg.end)}")
        lines.append(seg.text)
        lines.append("")
    return "\n".join(lines)


def _srt_ts(seconds: float) -> str:
    # Decompose from integer milliseconds so float noise can't cause
    # off-by-one (e.g. 0.9995 → 1000ms, not truncated to 999ms).
    ms_total = round(seconds * 1000)
    h, ms_total = divmod(ms_total, 3_600_000)
    m, ms_total = divmod(ms_total, 60_000)
    s, ms = divmod(ms_total, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _vtt_ts(seconds: float) -> str:
    return _srt_ts(seconds).replace(",", ".")
