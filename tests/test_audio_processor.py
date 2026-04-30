import pytest
from src.models import Silence
from src.audio_processor import find_chunk_boundaries


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
