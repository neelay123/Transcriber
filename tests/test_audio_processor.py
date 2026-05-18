from pathlib import Path

import pytest
from src.models import Silence
from src.audio_processor import (
    find_chunk_boundaries,
    _extracted_path,
    _chunk_path,
    _safe_arg,
)


class TestExtractedPath:
    def test_non_wav_input_gets_wav(self):
        assert _extracted_path(Path("/tmp/video.mp4")).name == "video_extracted.wav"

    def test_wav_input_not_overwritten(self):
        # I7: input already .wav must not collide with its own output
        out = _extracted_path(Path("/tmp/audio.wav"))
        assert out.name == "audio_extracted.wav"
        assert out != Path("/tmp/audio.wav")

    def test_keeps_parent_dir(self):
        assert _extracted_path(Path("/work/x.webm")).parent == Path("/work")


class TestChunkPath:
    def test_index_based_naming(self):
        p = _chunk_path(Path("/tmp/a.wav"), 0)
        assert p.name == "a_chunk0000.wav"

    def test_distinct_indices_distinct_paths(self):
        # I6: boundaries that round to the same time must not collide
        a = _chunk_path(Path("/tmp/a.wav"), 3)
        b = _chunk_path(Path("/tmp/a.wav"), 4)
        assert a != b
        assert a.name == "a_chunk0003.wav"


class TestSafeArg:
    def test_leading_dash_path_is_prefixed(self):
        # I1: a filename starting with '-' must not be parsed as an ffmpeg flag
        assert _safe_arg("-evil.mp4").startswith("./")

    def test_normal_path_unchanged(self):
        assert _safe_arg("/tmp/normal.mp4") == "/tmp/normal.mp4"

    def test_relative_path_unchanged(self):
        assert _safe_arg("video.mp4") == "video.mp4"


class TestFindChunkBoundaries:
    def test_short_audio_returns_single_chunk(self):
        boundaries = find_chunk_boundaries(
            silence_points=[], duration=300.0, target_length=600.0, overlap=2.0
        )
        assert boundaries == [(0.0, 300.0)]

    def test_audio_exactly_at_target_returns_single_chunk(self):
        boundaries = find_chunk_boundaries(
            silence_points=[], duration=600.0, target_length=600.0, overlap=2.0
        )
        assert boundaries == [(0.0, 600.0)]

    def test_long_audio_splits_at_silence_near_target(self):
        # 20 min audio, silence at ~10 min
        silence_points = [Silence(start=595.0, end=598.0)]
        boundaries = find_chunk_boundaries(
            silence_points=silence_points, duration=1200.0, target_length=600.0, overlap=2.0
        )
        assert len(boundaries) == 2
        assert boundaries[0] == (0.0, 595.0)

    def test_second_chunk_starts_with_overlap(self):
        silence_points = [Silence(start=595.0, end=598.0)]
        boundaries = find_chunk_boundaries(
            silence_points=silence_points, duration=1200.0, target_length=600.0, overlap=2.0
        )
        # Second chunk starts 2s before silence (overlap)
        assert boundaries[1][0] == pytest.approx(593.0)

    def test_final_chunk_ends_at_duration(self):
        silence_points = [Silence(start=595.0, end=598.0)]
        boundaries = find_chunk_boundaries(
            silence_points=silence_points, duration=1200.0, target_length=600.0, overlap=2.0
        )
        assert boundaries[-1][1] == pytest.approx(1200.0)

    def test_splits_at_closest_silence_to_target(self):
        # Two silences: one at 500s and one at 590s — should prefer 590s (closer to 600)
        silence_points = [
            Silence(start=500.0, end=502.0),
            Silence(start=590.0, end=592.0),
        ]
        boundaries = find_chunk_boundaries(
            silence_points=silence_points, duration=1200.0, target_length=600.0, overlap=2.0
        )
        assert boundaries[0][1] == 590.0

    def test_no_silence_splits_at_target(self):
        # No silence points — fall back to hard split at target_length
        boundaries = find_chunk_boundaries(
            silence_points=[], duration=1200.0, target_length=600.0, overlap=2.0
        )
        assert len(boundaries) == 2
        assert boundaries[0] == (0.0, 600.0)
        assert boundaries[1][0] == pytest.approx(598.0)  # 600 - overlap

    def test_three_chunks_for_triple_length_audio(self):
        silence_points = [
            Silence(start=598.0, end=601.0),
            Silence(start=1198.0, end=1201.0),
        ]
        boundaries = find_chunk_boundaries(
            silence_points=silence_points, duration=1800.0, target_length=600.0, overlap=2.0
        )
        assert len(boundaries) == 3

    def test_chunk_start_never_negative(self):
        silence_points = [Silence(start=1.0, end=2.0)]
        boundaries = find_chunk_boundaries(
            silence_points=silence_points, duration=1200.0, target_length=600.0, overlap=2.0
        )
        for start, _ in boundaries:
            assert start >= 0.0
