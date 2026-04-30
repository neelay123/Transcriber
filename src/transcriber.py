from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from difflib import SequenceMatcher
from pathlib import Path

from src.models import AudioChunk, TranscriptSegment, TranscriptionResult

_DUPLICATE_THRESHOLD = 0.8


def adjust_timestamps(
    segments: list[TranscriptSegment], offset: float
) -> list[TranscriptSegment]:
    return [
        TranscriptSegment(
            text=seg.text,
            start=seg.start + offset,
            end=seg.end + offset,
            confidence=seg.confidence,
            speaker=seg.speaker,
        )
        for seg in segments
    ]


def is_near_duplicate(
    segment: TranscriptSegment,
    recent: list[TranscriptSegment],
    threshold: float = _DUPLICATE_THRESHOLD,
) -> bool:
    needle = segment.text.lower()
    for candidate in recent:
        ratio = SequenceMatcher(None, needle, candidate.text.lower()).ratio()
        if ratio >= threshold:
            return True
    return False


def merge_chunks(results: list[TranscriptionResult]) -> list[TranscriptSegment]:
    if not results:
        return []

    merged: list[TranscriptSegment] = []

    for i, result in enumerate(results):
        for segment in result.segments:
            in_overlap = i > 0 and segment.start < results[i - 1].chunk.end
            if in_overlap and is_near_duplicate(segment, merged[-5:]):
                continue
            merged.append(segment)

    merged.sort(key=lambda s: s.start)
    return merged


class Transcriber:
    def __init__(
        self,
        model_size: str = "large-v3",
        device: str = "auto",
        compute_type: str = "auto",
    ):
        from faster_whisper import WhisperModel

        self.model = WhisperModel(model_size, device=device, compute_type=compute_type)
        self._lock = threading.Lock()  # faster-whisper model is not thread-safe

    def detect_language(self, chunk: AudioChunk) -> str:
        sample_path = _sample_audio(chunk.path, duration=30)
        with self._lock:
            _, info = self.model.transcribe(str(sample_path), language=None)
        return info.language

    def transcribe_chunk(
        self, chunk: AudioChunk, language: str | None = None
    ) -> TranscriptionResult:
        with self._lock:
            raw_segments, _ = self.model.transcribe(
                str(chunk.path),
                language=language,
                word_timestamps=True,
            )
            # Consume generator inside lock — faster-whisper streams lazily
            raw_segments = list(raw_segments)

        segments = [
            TranscriptSegment(
                text=s.text.strip(),
                start=s.start + chunk.start,
                end=s.end + chunk.start,
                confidence=s.avg_logprob,
            )
            for s in raw_segments
            if s.text.strip()
        ]
        avg_conf = (
            sum(s.confidence for s in segments) / len(segments) if segments else 0.0
        )
        return TranscriptionResult(segments=segments, chunk=chunk, confidence=avg_conf)

    def transcribe_chunks(
        self,
        chunks: list[AudioChunk],
        language: str | None = None,
        max_workers: int = 2,
    ) -> list[TranscriptSegment]:
        if not language:
            language = self.detect_language(chunks[0])

        # Sequential execution — model lock makes parallelism pointless on single GPU.
        # max_workers kept as param for future multi-GPU support.
        results = [self.transcribe_chunk(chunk, language) for chunk in chunks]
        results.sort(key=lambda r: r.chunk.start)
        return merge_chunks(results)


def _sample_audio(path: str, duration: int = 30) -> Path:
    import subprocess

    src = Path(path)
    out = src.with_suffix(".sample.wav")
    subprocess.run(
        [
            "ffmpeg", "-y", "-i", str(src),
            "-t", str(duration),
            "-ac", "1", "-ar", "16000",
            str(out),
        ],
        check=True,
        capture_output=True,
    )
    return out
