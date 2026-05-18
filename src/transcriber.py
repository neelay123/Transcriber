from __future__ import annotations

import re
import threading
from difflib import SequenceMatcher

from src.models import AudioChunk, TranscriptSegment, TranscriptionResult

_DUPLICATE_THRESHOLD = 0.8
_MIN_RATIO_LEN = 5  # below this, SequenceMatcher ratio is unreliable
_OVERLAP_EPSILON = 0.5  # seconds of slack when building the dedup lookback window


def _word_tokens(text: str) -> set[str]:
    return set(re.findall(r"\w+", text.lower()))


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
    a = segment.text.lower().strip()
    if not a:
        return False
    a_tokens = _word_tokens(a)

    for candidate in recent:
        b = candidate.text.lower().strip()
        # Token-set equality: catches reorder + punctuation, safe for short text
        if a_tokens and a_tokens == _word_tokens(b):
            return True
        # Fuzzy ratio: only trust it when both strings are long enough
        if len(a) >= _MIN_RATIO_LEN and len(b) >= _MIN_RATIO_LEN:
            if SequenceMatcher(None, a, b).ratio() >= threshold:
                return True
    return False


def merge_chunks(results: list[TranscriptionResult]) -> list[TranscriptSegment]:
    if not results:
        return []

    # Defensive: callers may pass chunks out of order.
    results = sorted(results, key=lambda r: r.chunk.start)

    merged: list[TranscriptSegment] = []

    for i, result in enumerate(results):
        # Overlap region = anything before the furthest end of ANY preceding
        # chunk, not just chunk i-1 (which may be empty / shorter).
        prev_end = max((r.chunk.end for r in results[:i]), default=0.0)
        for segment in result.segments:
            if i > 0 and segment.start < prev_end:
                window = [
                    m for m in reversed(merged)
                    if m.end >= segment.start - _OVERLAP_EPSILON
                ][:10]
                if window and is_near_duplicate(segment, window):
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
        # No FFmpeg re-encode: audio is already 16 kHz mono PCM post-extract.
        sample = _load_audio_sample(chunk.path, seconds=30)
        with self._lock:
            _, info = self.model.transcribe(sample, language=None)
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
    ) -> list[TranscriptSegment]:
        if not language:
            language = self.detect_language(chunks[0])

        # Sequential by design: the model lock serializes calls on a single GPU,
        # so a thread pool here would add no parallelism. merge_chunks() sorts
        # defensively, so input order does not matter.
        results = [self.transcribe_chunk(chunk, language) for chunk in chunks]
        return merge_chunks(results)


def _load_audio_sample(path: str, seconds: int = 30, rate: int = 16000):
    """Read up to `seconds` of audio as a float32 mono numpy array.

    Avoids an FFmpeg re-encode for language detection — the input is already
    16 kHz mono PCM after AudioProcessor.extract_audio().
    """
    import soundfile as sf

    arr, _ = sf.read(path, frames=seconds * rate, dtype="float32", always_2d=False)
    if arr.ndim > 1:  # safety: collapse to mono if a stereo file slips through
        arr = arr.mean(axis=1)
    return arr
