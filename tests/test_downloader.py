import pytest
from src.downloader import classify_url, is_media_url


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
