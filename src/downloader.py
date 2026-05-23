from __future__ import annotations

import json
import logging
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

from src.models import DownloadResult, TranscriptSegment

log = logging.getLogger(__name__)

_VTT_TS = re.compile(r"(\d{2}):(\d{2}):(\d{2})[.,](\d{3})")
_INLINE_TAG = re.compile(r"<[^>]+>")
_MEDIA_URL_RE = re.compile(r"\.(mp4|m3u8|webm|mpd|m4a)(\?|$)", re.IGNORECASE)
_MEDIA_CT_PREFIXES = ("video/", "audio/")
_MEDIA_CT_EXACT = {
    "application/vnd.apple.mpegurl",
    "application/x-mpegurl",
    "application/dash+xml",
}


def is_media_response(url: str, content_type: str) -> bool:
    if _MEDIA_URL_RE.search(url or ""):
        return True
    ct = (content_type or "").split(";")[0].strip().lower()
    return ct in _MEDIA_CT_EXACT or ct.startswith(_MEDIA_CT_PREFIXES)


_MEDIA_PRIORITY = ("m3u8", "mp4", "webm", "mpd")


def pick_best_media_url(urls: list[str]) -> str | None:
    def rank(u: str) -> int:
        m = re.search(r"\.(m3u8|mp4|webm|mpd)(\?|$)", u, re.IGNORECASE)
        ext = m.group(1).lower() if m else ""
        return _MEDIA_PRIORITY.index(ext) if ext in _MEDIA_PRIORITY else 99

    if not urls:
        return None
    best = min(urls, key=rank)
    return best if rank(best) < 99 else None


def cookies_to_netscape(cookies: list[dict], domain: str) -> str:
    lines = ["# Netscape HTTP Cookie File"]
    for c in cookies:
        d = c.get("domain") or domain
        include_sub = "TRUE" if d.startswith(".") else "FALSE"
        path = c.get("path") or "/"
        secure = "TRUE" if c.get("secure") else "FALSE"
        expiry = int(c.get("expires") or 0)
        if expiry < 0:
            expiry = 0
        lines.append(
            "\t".join(
                [d, include_sub, path, secure, str(expiry),
                 c.get("name", ""), c.get("value", "")]
            )
        )
    return "\n".join(lines) + "\n"


STEALTH_TIMEOUT_MS = 60_000
_CF_MARKERS = ("just a moment", "/cdn-cgi/challenge", "challenge-platform", "__cf_chl")


class _StealthError(Exception):
    """Stealth strategy failed; message is a classified reason string."""


@dataclass
class _StealthCapture:
    cookies: list = field(default_factory=list)
    media_urls: list = field(default_factory=list)
    final_url: str = ""
    drm_detected: bool = False


def classify_stealth_failure(capture: "_StealthCapture", ytdlp_err) -> str:
    if capture.drm_detected:
        return "drm-protected"
    final = (capture.final_url or "").lower()
    if not capture.media_urls and any(m in final for m in _CF_MARKERS):
        return "cloudflare-blocked"
    return "no-media-found"


_DRM_PATH_RE = re.compile(r"/(license|widevine|playready|wv|pr|cenc)(/|\?|$)", re.IGNORECASE)


def _make_capture_hook(capture: "_StealthCapture"):
    """Build a Scrapling page_action that captures media URLs + cookies.

    Responses during the first navigation fire before page_action runs, so
    we attach the listener then reload to observe the full media traffic.
    """

    def hook(page):
        def on_response(resp):
            url = getattr(resp, "url", "") or ""
            try:
                ct = resp.headers.get("content-type", "")
            except Exception:
                ct = ""
            if _DRM_PATH_RE.search(url):
                capture.drm_detected = True
            if is_media_response(url, ct):
                capture.media_urls.append(url)

        page.on("response", on_response)
        try:
            page.reload(wait_until="networkidle")
        except Exception as exc:
            log.warning("Stealth page.reload failed: %s", exc)
        try:
            capture.cookies = page.context.cookies()
        except Exception:
            capture.cookies = []
        capture.final_url = getattr(page, "url", "") or ""

    return hook


def parse_vtt(text: str) -> list[TranscriptSegment]:
    """Parse well-formed WebVTT (manual subtitles) into segments.

    Strips inline tags (`<c>`, `<00:00:01.000>`) and skips NOTE/STYLE/header
    blocks. Not intended for YouTube rolling auto-captions — use json3 there.
    """
    segments: list[TranscriptSegment] = []
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        if "-->" in lines[i]:
            ts = _VTT_TS.findall(lines[i])
            if len(ts) >= 2:
                start = _vtt_seconds(*ts[0])
                end = _vtt_seconds(*ts[1])
                i += 1
                buf = []
                while i < len(lines) and lines[i].strip() and "-->" not in lines[i]:
                    buf.append(lines[i])
                    i += 1
                clean = _INLINE_TAG.sub("", " ".join(buf)).strip()
                if clean:
                    segments.append(
                        TranscriptSegment(text=clean, start=start, end=end, confidence=1.0)
                    )
                continue
        i += 1
    return segments


def _vtt_seconds(h: str, m: str, s: str, ms: str) -> float:
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000.0


_MANUAL_EXT_PREF = ("vtt", "srt")
_AUTO_EXT_PREF = ("json3", "srv3", "vtt")
_MAX_DIRECT_BYTES = 5 * 1024 * 1024 * 1024  # 5 GB cap on direct downloads


def select_caption(
    info: dict, preferred_lang: str | None
) -> tuple[str, str, bool] | None:
    """Pick the best caption track.

    Returns (url, ext, is_auto) or None. Manual subtitles are preferred over
    auto-generated; within a track, format preference favors a parseable
    format (vtt for manual, json3 for auto).
    """
    for source_key, is_auto, ext_pref in (
        ("subtitles", False, _MANUAL_EXT_PREF),
        ("automatic_captions", True, _AUTO_EXT_PREF),
    ):
        source = info.get(source_key) or {}
        if not source:
            continue
        lang = preferred_lang if preferred_lang in source else next(iter(source))
        entries = source[lang]
        if not entries:
            continue
        chosen = min(
            entries,
            key=lambda e: ext_pref.index(e["ext"]) if e["ext"] in ext_pref else 99,
        )
        return chosen["url"], chosen["ext"], is_auto
    return None


def _stream_to_file(resp, out_path, max_bytes: int = _MAX_DIRECT_BYTES) -> None:
    """Stream a file-like response to disk, aborting if it exceeds max_bytes."""
    written = 0
    with open(out_path, "wb") as f:
        while True:
            chunk = resp.read(1024 * 1024)
            if not chunk:
                break
            written += len(chunk)
            if written > max_bytes:
                f.close()
                Path(out_path).unlink(missing_ok=True)
                raise ValueError(
                    f"Download exceeds {max_bytes} byte cap (aborted at {written})"
                )
            f.write(chunk)


def parse_json3(data: dict) -> list[TranscriptSegment]:
    """Parse YouTube json3 caption format into segments."""
    segments: list[TranscriptSegment] = []
    for event in data.get("events", []):
        segs = event.get("segs")
        if not segs:
            continue
        text = "".join(s.get("utf8", "") for s in segs).strip()
        if not text:
            continue
        start = event.get("tStartMs", 0) / 1000.0
        dur = event.get("dDurationMs", 0) / 1000.0
        segments.append(
            TranscriptSegment(text=text, start=start, end=start + dur, confidence=1.0)
        )
    return segments

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

    def download(
        self, url: str, preferred_lang: str | None = None
    ) -> DownloadResult:
        strategies = [self._try_ytdlp, self._try_direct_fetch]
        last_error: Exception | None = None

        for strategy in strategies:
            try:
                result = strategy(url, preferred_lang)
                if result is not None:
                    return result
            except Exception as exc:
                log.warning("Strategy %s failed for %s: %s", strategy.__name__, url, exc)
                last_error = exc

        raise RuntimeError(
            f"All download strategies failed for {url}: {last_error}"
        ) from last_error

    def _ytdlp_opts(self, cookiefile: str | None) -> dict:
        opts: dict = {
            "format": "bestaudio/best",
            "outtmpl": str(self.output_dir / "%(id)s.%(ext)s"),
            "quiet": True,
            "no_warnings": True,
        }
        cf = cookiefile or self.cookies_file
        if cf:
            opts["cookiefile"] = cf
        return opts

    def _try_ytdlp(
        self, url: str, preferred_lang: str | None = None,
        cookiefile_override: str | None = None,
    ) -> DownloadResult | None:
        import yt_dlp

        opts = self._ytdlp_opts(cookiefile_override)

        with yt_dlp.YoutubeDL(opts) as ydl:
            # Probe first (no media download) so the caption shortcut can
            # skip a potentially huge video download entirely.
            info = ydl.extract_info(url, download=False)

            caption = select_caption(info, preferred_lang)
            if caption is not None:
                cap_url, ext, _is_auto = caption
                raw = self._fetch_text(cap_url)
                segments = (
                    parse_json3(json.loads(raw))
                    if ext == "json3"
                    else parse_vtt(raw)
                )
                if segments:
                    log.info("Using existing captions (%s, %s)", ext, len(segments))
                    return DownloadResult(caption_segments=segments, metadata=info)

            # No usable captions — download the media for transcription.
            info = ydl.extract_info(url, download=True)
            path = ydl.prepare_filename(info)

        return DownloadResult(path=path, metadata=info)

    def _try_direct_fetch(
        self, url: str, preferred_lang: str | None = None
    ) -> DownloadResult | None:
        if not is_media_url(url):
            return None

        import urllib.request

        suffix = Path(urlparse(url).path).suffix or ".mp4"
        out = self.output_dir / f"direct_{abs(hash(url))}{suffix}"

        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            declared = int(resp.headers.get("content-length", 0))
            if declared > _MAX_DIRECT_BYTES:
                raise ValueError(
                    f"Remote file {declared} bytes exceeds {_MAX_DIRECT_BYTES} cap"
                )
            _stream_to_file(resp, out)
        return DownloadResult(path=str(out))

    @staticmethod
    def _fetch_text(url: str) -> str:
        import urllib.request

        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.read().decode("utf-8", errors="replace")
