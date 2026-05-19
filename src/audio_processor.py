from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

from src.models import AudioChunk, Silence


def find_chunk_boundaries(
    silence_points: list[Silence],
    duration: float,
    target_length: float,
    overlap: float = 2.0,
    silence_tolerance: float = 0.2,
) -> list[tuple[float, float]]:
    """
    Split audio into chunks near target_length, preferring silence boundaries.

    silence_tolerance: fraction of target_length within which a silence qualifies
    as a candidate split point (e.g. 0.2 → silences within ±20% of ideal).
    """
    if duration <= target_length:
        return [(0.0, duration)]

    import math

    n_chunks = math.ceil(duration / target_length)
    window = target_length * silence_tolerance

    split_points: list[float] = []
    for i in range(1, n_chunks):
        ideal = i * target_length
        nearby = [s for s in silence_points if abs(s.start - ideal) < window]
        split = min(nearby, key=lambda s: abs(s.start - ideal)).start if nearby else ideal
        split_points.append(split)

    boundaries: list[tuple[float, float]] = []
    chunk_start = 0.0
    for split in split_points:
        boundaries.append((chunk_start, split))
        chunk_start = max(0.0, split - overlap)
    boundaries.append((chunk_start, duration))

    return boundaries


def _extracted_path(src: Path) -> Path:
    """Output path for extracted audio. Suffix avoids overwriting a .wav input (I7)."""
    return src.with_name(f"{src.stem}_extracted.wav")


def _chunk_path(audio_path: Path, idx: int) -> Path:
    """Index-based chunk filename — never collides on rounded timestamps (I6)."""
    return audio_path.with_stem(f"{audio_path.stem}_chunk{idx:04d}")


def _safe_arg(path: str) -> str:
    """Prevent a leading-dash filename being parsed as an FFmpeg option (I1)."""
    s = str(path)
    if s.startswith("-"):
        return "./" + s
    return s


class AudioProcessor:
    def __init__(self, target_chunk_length: int = 600, overlap: float = 2.0):
        self.target_chunk_length = target_chunk_length
        self.overlap = overlap

    def prepare_audio(self, video_path: str) -> list[AudioChunk]:
        audio_path = self.extract_audio(video_path)
        audio_path = self.normalize_audio(audio_path)
        duration = _get_duration(audio_path)

        if duration <= self.target_chunk_length:
            return [AudioChunk(path=str(audio_path), start=0.0, end=duration)]

        return self.chunk_on_silence(audio_path, duration)

    def extract_audio(self, video_path: str) -> Path:
        src = Path(video_path)
        out = _extracted_path(src)
        subprocess.run(
            [
                "ffmpeg", "-y", "-i", _safe_arg(src),
                "-ac", "1",
                "-ar", "16000",
                "-c:a", "pcm_s16le",
                _safe_arg(out),
            ],
            check=True,
            capture_output=True,
        )
        return out

    def normalize_audio(self, audio_path: Path) -> Path:
        out = audio_path.with_stem(audio_path.stem + "_norm")
        subprocess.run(
            [
                "ffmpeg", "-y", "-i", _safe_arg(audio_path),
                "-af", "loudnorm",
                _safe_arg(out),
            ],
            check=True,
            capture_output=True,
        )
        return out

    def chunk_on_silence(self, audio_path: Path, duration: float) -> list[AudioChunk]:
        silence_points = _detect_silences(audio_path)
        boundaries = find_chunk_boundaries(
            silence_points=silence_points,
            duration=duration,
            target_length=self.target_chunk_length,
            overlap=self.overlap,
        )

        chunks = []
        for idx, (start, end) in enumerate(boundaries):
            chunk_path = _slice_audio(audio_path, start, end, idx)
            chunks.append(AudioChunk(path=str(chunk_path), start=start, end=end))
        return chunks


def _get_duration(audio_path: Path) -> float:
    result = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(audio_path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return float(result.stdout.strip())


def _detect_silences(audio_path: Path, min_duration: float = 0.5) -> list[Silence]:
    # check=True: a silent failure here would degrade chunking to hard splits
    # with no warning. ffmpeg with `-f null -` exits 0 normally.
    result = subprocess.run(
        [
            "ffmpeg", "-i", _safe_arg(audio_path),
            "-af", f"silencedetect=noise=-30dB:d={min_duration}",
            "-f", "null", "-",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    # ffmpeg writes silencedetect to stderr
    return _parse_silence_output(result.stderr)


def _parse_silence_output(stderr: str) -> list[Silence]:
    import re

    silences = []
    start = None
    for line in stderr.splitlines():
        if m := re.search(r"silence_start: ([0-9.]+)", line):
            start = float(m.group(1))
        elif m := re.search(r"silence_end: ([0-9.]+)", line):
            if start is not None:
                silences.append(Silence(start=start, end=float(m.group(1))))
                start = None
    return silences


def _slice_audio(audio_path: Path, start: float, end: float, idx: int) -> Path:
    out = _chunk_path(audio_path, idx)
    subprocess.run(
        [
            "ffmpeg", "-y",
            "-i", _safe_arg(audio_path),
            "-ss", str(start),
            "-to", str(end),
            "-c", "copy",
            _safe_arg(out),
        ],
        check=True,
        capture_output=True,
    )
    return out
