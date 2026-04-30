from __future__ import annotations

import logging
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import urlparse

from src.models import DownloadResult

log = logging.getLogger(__name__)

_YOUTUBE_DOMAINS = {"youtube.com", "www.youtube.com", "youtu.be", "m.youtube.com"}
_VIMEO_DOMAINS = {"vimeo.com", "www.vimeo.com"}
_SOCIAL_DOMAINS = {
    "twitter.com", "www.twitter.com",
    "x.com", "www.x.com",
    "instagram.com", "www.instagram.com",
    "tiktok.com", "www.tiktok.com",
    "facebook.com", "www.facebook.com",
    "fb.watch",
}
_MEDIA_EXTENSIONS = {".mp4", ".webm", ".m3u8", ".mkv", ".avi", ".mov", ".mp3", ".m4a", ".flac", ".ogg"}


def classify_url(url: str) -> str:
    parsed = urlparse(url)
    domain = parsed.netloc.lower()

    if domain in _YOUTUBE_DOMAINS:
        return "youtube"
    if domain in _VIMEO_DOMAINS:
        return "vimeo"
    if domain in _SOCIAL_DOMAINS:
        return "social"
    if is_media_url(url):
        return "direct"
    return "unknown"


def is_media_url(url: str) -> bool:
    path = urlparse(url).path.lower()
    return any(path.endswith(ext) for ext in _MEDIA_EXTENSIONS)


class VideoDownloader:
    def __init__(self, output_dir: str | None = None, cookies_file: str | None = None):
        self.output_dir = Path(output_dir) if output_dir else Path(tempfile.gettempdir())
        self.cookies_file = cookies_file

    def download(self, url: str) -> DownloadResult:
        strategies = [self._try_ytdlp, self._try_direct_fetch]
        last_error: Exception | None = None

        for strategy in strategies:
            try:
                result = strategy(url)
                if result is not None:
                    return result
            except Exception as exc:
                log.warning("Strategy %s failed for %s: %s", strategy.__name__, url, exc)
                last_error = exc

        raise RuntimeError(
            f"All download strategies failed for {url}"
        ) from last_error

    def _try_ytdlp(self, url: str) -> DownloadResult | None:
        import yt_dlp

        opts: dict = {
            "format": "bestaudio/best",
            "outtmpl": str(self.output_dir / "%(id)s.%(ext)s"),
            "quiet": True,
            "no_warnings": True,
        }
        if self.cookies_file:
            opts["cookiefile"] = self.cookies_file

        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)

        # Check for existing captions — skip transcription entirely if available
        captions = info.get("subtitles") or info.get("automatic_captions") or {}
        path = ydl.prepare_filename(info)

        return DownloadResult(path=path, metadata=info, captions=captions)

    def _try_direct_fetch(self, url: str) -> DownloadResult | None:
        if not is_media_url(url):
            return None

        import urllib.request

        suffix = Path(urlparse(url).path).suffix or ".mp4"
        out = self.output_dir / f"direct_{abs(hash(url))}{suffix}"

        urllib.request.urlretrieve(url, str(out))
        return DownloadResult(path=str(out))
