import wave

from src.models import AudioChunk, TranscriptSegment, TranscriptionResult
from src.transcriber import (
    _load_audio_sample,
    adjust_timestamps,
    is_near_duplicate,
    merge_chunks,
)


def seg(text, start, end, confidence=0.9):
    return TranscriptSegment(text=text, start=start, end=end, confidence=confidence)


def chunk(start, end, path="test.wav"):
    return AudioChunk(path=path, start=start, end=end)


def result(segments, ck):
    return TranscriptionResult(segments=segments, chunk=ck, confidence=0.9)


def _write_silence_wav(path, seconds, rate=16000):
    with wave.open(str(path), "w") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00\x00" * (rate * seconds))


class TestLoadAudioSample:
    def test_returns_float32_mono_array(self, tmp_path):
        wav = tmp_path / "s.wav"
        _write_silence_wav(wav, seconds=5)
        arr = _load_audio_sample(str(wav), seconds=30)
        assert arr.dtype.name == "float32"
        assert arr.ndim == 1

    def test_truncates_to_requested_seconds(self, tmp_path):
        wav = tmp_path / "long.wav"
        _write_silence_wav(wav, seconds=40)
        arr = _load_audio_sample(str(wav), seconds=30)
        assert len(arr) == 30 * 16000

    def test_shorter_than_window_returns_all(self, tmp_path):
        wav = tmp_path / "short.wav"
        _write_silence_wav(wav, seconds=5)
        arr = _load_audio_sample(str(wav), seconds=30)
        assert len(arr) == 5 * 16000


class TestAdjustTimestamps:
    def test_adds_offset_to_start_and_end(self):
        segments = [seg("Hello", 0.0, 1.0), seg("World", 1.5, 2.5)]
        adjusted = adjust_timestamps(segments, offset=10.0)
        assert adjusted[0].start == 10.0
        assert adjusted[0].end == 11.0
        assert adjusted[1].start == 11.5
        assert adjusted[1].end == 12.5

    def test_zero_offset_leaves_timestamps_unchanged(self):
        segments = [seg("Hello", 5.0, 6.0)]
        adjusted = adjust_timestamps(segments, offset=0.0)
        assert adjusted[0].start == 5.0
        assert adjusted[0].end == 6.0

    def test_does_not_mutate_original_segments(self):
        original = seg("Hello", 0.0, 1.0)
        adjust_timestamps([original], offset=5.0)
        assert original.start == 0.0
        assert original.end == 1.0

    def test_preserves_text_and_confidence(self):
        segments = [seg("Test text", 0.0, 1.0, confidence=0.75)]
        adjusted = adjust_timestamps(segments, offset=3.0)
        assert adjusted[0].text == "Test text"
        assert adjusted[0].confidence == 0.75

    def test_empty_list_returns_empty(self):
        assert adjust_timestamps([], offset=10.0) == []


class TestIsNearDuplicate:
    def test_exact_match_is_duplicate(self):
        s = seg("Hello world", 8.0, 9.0)
        recent = [seg("Hello world", 8.0, 9.0)]
        assert is_near_duplicate(s, recent)

    def test_different_text_not_duplicate(self):
        s = seg("New content here", 10.0, 11.0)
        recent = [seg("Hello world", 8.0, 9.0)]
        assert not is_near_duplicate(s, recent)

    def test_minor_punctuation_difference_is_duplicate(self):
        s = seg("Hello, world!", 8.0, 9.0)
        recent = [seg("Hello world", 8.0, 9.0)]
        assert is_near_duplicate(s, recent)

    def test_empty_recent_not_duplicate(self):
        s = seg("Hello world", 8.0, 9.0)
        assert not is_near_duplicate(s, [])

    def test_checks_all_recent_not_just_last(self):
        s = seg("Hello world", 8.0, 9.0)
        recent = [
            seg("Something else", 6.0, 7.0),
            seg("Hello world", 7.5, 8.5),
        ]
        assert is_near_duplicate(s, recent)

    def test_case_insensitive_match(self):
        s = seg("HELLO WORLD", 8.0, 9.0)
        recent = [seg("hello world", 8.0, 9.0)]
        assert is_near_duplicate(s, recent)

    def test_short_distinct_strings_not_duplicate(self):
        # "yes"/"yep" share a char but are different words — must NOT dedupe
        assert not is_near_duplicate(seg("yes", 8.0, 9.0), [seg("yep", 8.0, 9.0)])

    def test_short_identical_strings_are_duplicate(self):
        # Exact short repeats in overlap should still dedupe via token-set
        assert is_near_duplicate(seg("Yes.", 8.0, 9.0), [seg("yes", 8.0, 9.0)])

    def test_token_set_equality_ignores_punctuation_and_order(self):
        s = seg("world, hello!", 8.0, 9.0)
        assert is_near_duplicate(s, [seg("Hello world", 8.0, 9.0)])

    def test_short_unrelated_words_not_duplicate(self):
        assert not is_near_duplicate(seg("um", 8.0, 9.0), [seg("ah", 8.0, 9.0)])


class TestMergeChunks:
    def test_single_chunk_all_segments_returned(self):
        ck = chunk(0.0, 10.0)
        r = result([seg("Hello", 0.0, 1.5), seg("World", 2.0, 3.0)], ck)
        merged = merge_chunks([r])
        assert len(merged) == 2
        assert merged[0].text == "Hello"
        assert merged[1].text == "World"

    def test_non_overlapping_chunks_concatenated(self):
        ck1 = chunk(0.0, 10.0, "a.wav")
        ck2 = chunk(10.0, 20.0, "b.wav")
        r1 = result([seg("First", 0.0, 1.0), seg("Second", 2.0, 3.0)], ck1)
        r2 = result([seg("Third", 10.0, 11.0)], ck2)
        merged = merge_chunks([r1, r2])
        assert len(merged) == 3
        assert [s.text for s in merged] == ["First", "Second", "Third"]

    def test_duplicate_in_overlap_region_removed(self):
        ck1 = chunk(0.0, 10.0, "a.wav")
        ck2 = chunk(8.0, 20.0, "b.wav")
        r1 = result([
            seg("Hello world", 0.0, 1.5),
            seg("Overlap sentence", 8.5, 10.0),
        ], ck1)
        r2 = result([
            seg("Overlap sentence", 8.5, 10.0),  # exact duplicate in overlap
            seg("New content", 10.5, 12.0),
        ], ck2)
        merged = merge_chunks([r1, r2])
        assert len(merged) == 3
        assert [s.text for s in merged].count("Overlap sentence") == 1
        assert merged[-1].text == "New content"

    def test_non_duplicate_in_overlap_region_kept(self):
        ck1 = chunk(0.0, 10.0, "a.wav")
        ck2 = chunk(8.0, 20.0, "b.wav")
        r1 = result([seg("First sentence", 0.0, 2.0)], ck1)
        r2 = result([
            seg("Different sentence", 8.5, 10.0),  # in overlap but NOT duplicate
            seg("Third sentence", 10.5, 12.0),
        ], ck2)
        merged = merge_chunks([r1, r2])
        assert len(merged) == 3

    def test_merged_segments_ordered_by_start_time(self):
        ck1 = chunk(0.0, 10.0, "a.wav")
        ck2 = chunk(8.0, 20.0, "b.wav")
        r1 = result([seg("A", 0.0, 1.0), seg("C", 9.0, 10.0)], ck1)
        r2 = result([seg("D", 11.0, 12.0)], ck2)
        merged = merge_chunks([r1, r2])
        starts = [s.start for s in merged]
        assert starts == sorted(starts)

    def test_empty_results_returns_empty(self):
        assert merge_chunks([]) == []

    def test_near_duplicate_in_overlap_removed(self):
        ck1 = chunk(0.0, 10.0, "a.wav")
        ck2 = chunk(8.0, 20.0, "b.wav")
        r1 = result([seg("Hello world", 8.5, 10.0)], ck1)
        r2 = result([
            seg("Hello, world!", 8.5, 10.0),  # near-duplicate with punctuation
            seg("Next line", 10.5, 11.5),
        ], ck2)
        merged = merge_chunks([r1, r2])
        overlap_texts = [s.text for s in merged if s.start < 10.0]
        assert len(overlap_texts) == 1

    def test_unordered_results_sorted_defensively(self):
        # Caller passes results out of chunk order — merge must still work
        ck1 = chunk(0.0, 10.0, "a.wav")
        ck2 = chunk(10.0, 20.0, "b.wav")
        r1 = result([seg("First", 0.0, 1.0)], ck1)
        r2 = result([seg("Second", 11.0, 12.0)], ck2)
        merged = merge_chunks([r2, r1])  # reversed
        assert [s.text for s in merged] == ["First", "Second"]

    def test_empty_preceding_chunk_does_not_break_overlap_dedup(self):
        # chunk[1] is empty and its end (8.4) is BEFORE the duplicate at 8.5;
        # the real overlapping content is in chunk[0] (ends at 10.0).
        # prev_end must be max over all preceding chunks, not just chunk[i-1].
        ck0 = chunk(0.0, 10.0, "a.wav")
        ck1 = chunk(8.0, 8.4, "b.wav")  # empty chunk, ends before 8.5
        ck2 = chunk(8.0, 20.0, "c.wav")
        r0 = result([seg("Overlap line", 8.5, 10.0)], ck0)
        r1 = result([], ck1)
        r2 = result([
            seg("Overlap line", 8.5, 10.0),  # duplicate of r0's segment
            seg("Fresh line", 13.0, 14.0),
        ], ck2)
        merged = merge_chunks([r0, r1, r2])
        assert [s.text for s in merged].count("Overlap line") == 1
        assert "Fresh line" in [s.text for s in merged]

    def test_repeated_phrase_outside_overlap_region_kept(self):
        # Legit repeat far past the overlap zone must NOT be deduped
        ck1 = chunk(0.0, 10.0, "a.wav")
        ck2 = chunk(8.0, 30.0, "b.wav")
        r1 = result([seg("the end", 1.0, 2.0)], ck1)
        r2 = result([seg("the end", 25.0, 26.0)], ck2)  # 25s — well outside overlap
        merged = merge_chunks([r1, r2])
        assert [s.text for s in merged].count("the end") == 2
