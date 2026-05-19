import io
import pytest
from src.downloader import (
    classify_url,
    is_media_url,
    is_media_response,
    parse_vtt,
    parse_json3,
    select_caption,
    _stream_to_file,
)


class TestSelectCaption:
    def test_prefers_manual_over_automatic(self):
        info = {
            "subtitles": {"en": [{"ext": "vtt", "url": "MANUAL"}]},
            "automatic_captions": {"en": [{"ext": "json3", "url": "AUTO"}]},
        }
        url, ext, is_auto = select_caption(info, "en")
        assert url == "MANUAL"
        assert is_auto is False

    def test_prefers_requested_language(self):
        info = {"subtitles": {
            "en": [{"ext": "vtt", "url": "EN"}],
            "fr": [{"ext": "vtt", "url": "FR"}],
        }}
        url, _, _ = select_caption(info, "fr")
        assert url == "FR"

    def test_falls_back_to_first_language(self):
        info = {"subtitles": {"de": [{"ext": "vtt", "url": "DE"}]}}
        url, _, _ = select_caption(info, "en")
        assert url == "DE"

    def test_automatic_prefers_json3(self):
        info = {"automatic_captions": {"en": [
            {"ext": "vtt", "url": "VTT"},
            {"ext": "json3", "url": "JSON3"},
        ]}}
        url, ext, is_auto = select_caption(info, "en")
        assert ext == "json3"
        assert is_auto is True

    def test_manual_prefers_vtt(self):
        info = {"subtitles": {"en": [
            {"ext": "srt", "url": "SRT"},
            {"ext": "vtt", "url": "VTT"},
        ]}}
        url, ext, _ = select_caption(info, "en")
        assert ext == "vtt"

    def test_no_captions_returns_none(self):
        assert select_caption({}, "en") is None

    def test_empty_caption_dicts_return_none(self):
        assert select_caption({"subtitles": {}, "automatic_captions": {}}, "en") is None


class TestStreamToFile:
    def test_writes_content(self, tmp_path):
        out = tmp_path / "f.bin"
        src = io.BytesIO(b"hello world")
        _stream_to_file(src, out, max_bytes=1024)
        assert out.read_bytes() == b"hello world"

    def test_raises_when_exceeds_cap(self, tmp_path):
        out = tmp_path / "big.bin"
        src = io.BytesIO(b"x" * 5000)
        with pytest.raises(ValueError):
            _stream_to_file(src, out, max_bytes=1000)


class TestParseVtt:
    def test_basic_cue(self):
        vtt = "WEBVTT\n\n00:00:01.000 --> 00:00:03.500\nHello world\n"
        segs = parse_vtt(vtt)
        assert len(segs) == 1
        assert segs[0].text == "Hello world"
        assert segs[0].start == 1.0
        assert segs[0].end == 3.5

    def test_multi_line_cue_joined(self):
        vtt = "WEBVTT\n\n00:00:00.000 --> 00:00:02.000\nline one\nline two\n"
        segs = parse_vtt(vtt)
        assert segs[0].text == "line one line two"

    def test_strips_inline_tags(self):
        vtt = "WEBVTT\n\n00:00:00.000 --> 00:00:02.000\n<c>Hello</c> <00:00:01.000>world\n"
        segs = parse_vtt(vtt)
        assert segs[0].text == "Hello world"

    def test_skips_note_and_header_blocks(self):
        vtt = "WEBVTT\n\nNOTE this is a comment\n\n00:00:01.000 --> 00:00:02.000\nReal text\n"
        segs = parse_vtt(vtt)
        assert len(segs) == 1
        assert segs[0].text == "Real text"

    def test_comma_millisecond_separator(self):
        # Some SRT-ish exports use comma
        vtt = "WEBVTT\n\n00:00:01,250 --> 00:00:02,750\nComma ts\n"
        segs = parse_vtt(vtt)
        assert segs[0].start == 1.25
        assert segs[0].end == 2.75

    def test_empty_returns_empty(self):
        assert parse_vtt("WEBVTT\n\n") == []


class TestParseJson3:
    def test_basic_events(self):
        data = {
            "events": [
                {"tStartMs": 1000, "dDurationMs": 2000, "segs": [{"utf8": "Hello "}, {"utf8": "world"}]},
            ]
        }
        segs = parse_json3(data)
        assert len(segs) == 1
        assert segs[0].text == "Hello world"
        assert segs[0].start == 1.0
        assert segs[0].end == 3.0

    def test_skips_empty_and_newline_only_events(self):
        data = {
            "events": [
                {"tStartMs": 0, "dDurationMs": 500, "segs": [{"utf8": "\n"}]},
                {"tStartMs": 500, "dDurationMs": 500, "segs": [{"utf8": "Real"}]},
                {"tStartMs": 1000, "dDurationMs": 500},  # no segs key
            ]
        }
        segs = parse_json3(data)
        assert len(segs) == 1
        assert segs[0].text == "Real"

    def test_no_events_returns_empty(self):
        assert parse_json3({}) == []


class TestClassifyUrl:
    def test_youtube_watch_url(self):
        assert classify_url("https://www.youtube.com/watch?v=dQw4w9WgXcQ") == "youtube"

    def test_youtube_short_url(self):
        assert classify_url("https://youtu.be/dQw4w9WgXcQ") == "youtube"

    def test_youtube_shorts_url(self):
        assert classify_url("https://www.youtube.com/shorts/abc123") == "youtube"

    def test_vimeo_url(self):
        assert classify_url("https://vimeo.com/123456789") == "vimeo"

    def test_direct_mp4_url(self):
        assert classify_url("https://example.com/video.mp4") == "direct"

    def test_direct_webm_url(self):
        assert classify_url("https://cdn.example.com/media/clip.webm") == "direct"

    def test_hls_m3u8_url(self):
        assert classify_url("https://stream.example.com/live/index.m3u8") == "direct"

    def test_twitter_url(self):
        assert classify_url("https://twitter.com/user/status/123456") == "social"

    def test_x_com_url(self):
        assert classify_url("https://x.com/user/status/123456") == "social"

    def test_instagram_url(self):
        assert classify_url("https://www.instagram.com/reel/abc123/") == "social"

    def test_tiktok_url(self):
        assert classify_url("https://www.tiktok.com/@user/video/123") == "social"

    def test_unknown_url(self):
        assert classify_url("https://some-random-site.com/watch?id=123") == "unknown"

    def test_unknown_blog_url(self):
        assert classify_url("https://blog.example.org/post/123") == "unknown"


class TestIsMediaUrl:
    def test_mp4_extension_is_media(self):
        assert is_media_url("https://cdn.example.com/video.mp4")

    def test_webm_extension_is_media(self):
        assert is_media_url("https://cdn.example.com/clip.webm")

    def test_m3u8_extension_is_media(self):
        assert is_media_url("https://stream.example.com/playlist.m3u8")

    def test_html_page_not_media(self):
        assert not is_media_url("https://www.youtube.com/watch?v=abc")

    def test_image_not_media(self):
        assert not is_media_url("https://example.com/thumbnail.jpg")

    def test_audio_mp3_is_media(self):
        assert is_media_url("https://example.com/podcast.mp3")


class TestIsMediaResponse:
    def test_mp4_url_is_media(self):
        assert is_media_response("https://x.com/v.mp4", "")

    def test_m3u8_url_is_media(self):
        assert is_media_response("https://x.com/p.m3u8?a=1", "")

    def test_video_content_type_is_media(self):
        assert is_media_response("https://x.com/stream", "video/mp4")

    def test_hls_content_type_is_media(self):
        assert is_media_response("https://x.com/s", "application/vnd.apple.mpegurl")

    def test_audio_content_type_is_media(self):
        assert is_media_response("https://x.com/a", "audio/mpeg; charset=utf-8")

    def test_html_is_not_media(self):
        assert not is_media_response("https://x.com/page", "text/html")

    def test_json_is_not_media(self):
        assert not is_media_response("https://x.com/api", "application/json")
