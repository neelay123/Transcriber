from src.models import TranscriptSegment
from src.agent import _srt_ts, _vtt_ts, _format_segments, _to_srt


def seg(text, start, end):
    return TranscriptSegment(text=text, start=start, end=end, confidence=0.9)


class TestSrtTimestamp:
    def test_zero(self):
        assert _srt_ts(0.0) == "00:00:00,000"

    def test_one_minute(self):
        assert _srt_ts(60.0) == "00:01:00,000"

    def test_one_hour(self):
        assert _srt_ts(3600.0) == "01:00:00,000"

    def test_compound(self):
        assert _srt_ts(3661.5) == "01:01:01,500"

    def test_millisecond_rounding_up(self):
        # 0.9995s → 1000ms (rounded), not 999ms (truncated)
        assert _srt_ts(0.9995) == "00:00:01,000"

    def test_sub_millisecond_float_noise(self):
        # 2.0000001 must not become 00:00:01,999 via float truncation
        assert _srt_ts(2.0000001) == "00:00:02,000"


class TestVttTimestamp:
    def test_uses_dot_separator(self):
        assert _vtt_ts(3661.5) == "01:01:01.500"

    def test_zero(self):
        assert _vtt_ts(0.0) == "00:00:00.000"


class TestFormatSegments:
    def test_text_joins_with_spaces(self):
        out = _format_segments([seg("Hello", 0, 1), seg("world", 1, 2)], "text")
        assert out == "Hello world"

    def test_json_has_start_end_text(self):
        import json
        out = _format_segments([seg("Hi", 0.5, 1.5)], "json")
        data = json.loads(out)
        assert data == [{"text": "Hi", "start": 0.5, "end": 1.5}]

    def test_srt_block_structure(self):
        out = _to_srt([seg("Line one", 0.0, 1.0)])
        assert "1\n00:00:00,000 --> 00:00:01,000\nLine one" in out

    def test_vtt_has_header(self):
        out = _format_segments([seg("Hi", 0.0, 1.0)], "vtt")
        assert out.startswith("WEBVTT")
