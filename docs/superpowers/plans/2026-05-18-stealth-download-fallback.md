# Stealth Download Fallback Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a third `VideoDownloader` strategy that uses Scrapling's `StealthyFetcher` to defeat anti-bot pages, harvest cookies for a yt-dlp retry, then sniff the raw media URL.

**Architecture:** One new `_try_stealth` method appended to the existing strategy chain. A single browser launch captures cookies + media URLs in one session via a `page_action` hook. Stage A retries yt-dlp with captured cookies; Stage B feeds the best sniffed media URL to yt-dlp. DRM is detected from network signals and reported, never bypassed. Pure helpers do the parsing/classification and are unit-tested without a browser; the browser call sits behind a `_run_stealth_fetch` seam so orchestration is testable via monkeypatch.

**Tech Stack:** Python 3.11+, `scrapling` (StealthyFetcher, Playwright/camoufox), `yt-dlp`, pytest.

---

## File Structure

- Modify: `src/downloader.py` — add constants, `_StealthError`, `_StealthCapture`, pure helpers (`is_media_response`, `pick_best_media_url`, `cookies_to_netscape`, `classify_stealth_failure`), `_make_capture_hook`, `_ytdlp_opts`, `cookiefile_override` on `_try_ytdlp`, `_run_stealth_fetch`, `_try_stealth`, and the chain wiring in `download()`.
- Modify: `tests/test_downloader.py` — append unit + orchestration tests.
- Modify: `requirements.txt` — add `scrapling`.
- Modify: `README.md` — document `scrapling install` and the stealth fallback.

All new symbols live in `src/downloader.py` (follows the existing pattern: pure functions module-level, browser/yt-dlp lazily imported inside methods).

---

### Task 1: `is_media_response` helper + media constants

**Files:**
- Modify: `src/downloader.py`
- Test: `tests/test_downloader.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_downloader.py`:

```python
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
```

Add `is_media_response` to the existing `from src.downloader import (...)` block at the top of the file.

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_downloader.py::TestIsMediaResponse -v`
Expected: collection error / ImportError — cannot import name `is_media_response`.

- [ ] **Step 3: Write minimal implementation**

In `src/downloader.py`, after the existing module constants (near `_VTT_TS`):

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_downloader.py::TestIsMediaResponse -v`
Expected: 7 passed.

- [ ] **Step 5: Commit**

```bash
git add src/downloader.py tests/test_downloader.py
git commit -m "feat(downloader): add is_media_response helper"
```

---

### Task 2: `pick_best_media_url` helper

**Files:**
- Modify: `src/downloader.py`
- Test: `tests/test_downloader.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_downloader.py`:

```python
class TestPickBestMediaUrl:
    def test_prefers_m3u8_over_mp4(self):
        assert pick_best_media_url(["https://a/v.mp4", "https://a/p.m3u8"]) == "https://a/p.m3u8"

    def test_prefers_mp4_over_webm(self):
        assert pick_best_media_url(["https://a/c.webm", "https://a/v.mp4"]) == "https://a/v.mp4"

    def test_mpd_deprioritized_below_mp4(self):
        assert pick_best_media_url(["https://a/m.mpd", "https://a/v.mp4"]) == "https://a/v.mp4"

    def test_mpd_chosen_if_only_option(self):
        assert pick_best_media_url(["https://a/m.mpd"]) == "https://a/m.mpd"

    def test_empty_returns_none(self):
        assert pick_best_media_url([]) is None

    def test_no_media_extension_returns_none(self):
        assert pick_best_media_url(["https://a/page.html"]) is None
```

Add `pick_best_media_url` to the top-of-file import block.

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_downloader.py::TestPickBestMediaUrl -v`
Expected: ImportError — cannot import name `pick_best_media_url`.

- [ ] **Step 3: Write minimal implementation**

In `src/downloader.py`, below `is_media_response`:

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_downloader.py::TestPickBestMediaUrl -v`
Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add src/downloader.py tests/test_downloader.py
git commit -m "feat(downloader): add pick_best_media_url helper"
```

---

### Task 3: `cookies_to_netscape` helper

**Files:**
- Modify: `src/downloader.py`
- Test: `tests/test_downloader.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_downloader.py`:

```python
class TestCookiesToNetscape:
    def test_header_present(self):
        out = cookies_to_netscape([], "example.com")
        assert out.splitlines()[0] == "# Netscape HTTP Cookie File"

    def test_empty_is_header_only(self):
        assert cookies_to_netscape([], "example.com").strip() == "# Netscape HTTP Cookie File"

    def test_cookie_line_fields(self):
        cookies = [{
            "name": "sid", "value": "abc", "domain": ".example.com",
            "path": "/", "secure": True, "expires": 1893456000,
        }]
        line = cookies_to_netscape(cookies, "example.com").splitlines()[1]
        parts = line.split("\t")
        assert parts == [".example.com", "TRUE", "/", "TRUE", "1893456000", "sid", "abc"]

    def test_missing_domain_uses_fallback(self):
        line = cookies_to_netscape([{"name": "a", "value": "b"}], "fallback.com").splitlines()[1]
        assert line.split("\t")[0] == "fallback.com"

    def test_negative_expiry_clamped_to_zero(self):
        line = cookies_to_netscape(
            [{"name": "a", "value": "b", "domain": "x.com", "expires": -1}], "x.com"
        ).splitlines()[1]
        assert line.split("\t")[4] == "0"
```

Add `cookies_to_netscape` to the top-of-file import block.

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_downloader.py::TestCookiesToNetscape -v`
Expected: ImportError — cannot import name `cookies_to_netscape`.

- [ ] **Step 3: Write minimal implementation**

In `src/downloader.py`, below `pick_best_media_url`:

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_downloader.py::TestCookiesToNetscape -v`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add src/downloader.py tests/test_downloader.py
git commit -m "feat(downloader): add cookies_to_netscape helper"
```

---

### Task 4: `_StealthError` + `classify_stealth_failure`

**Files:**
- Modify: `src/downloader.py`
- Test: `tests/test_downloader.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_downloader.py`:

```python
class TestClassifyStealthFailure:
    def test_drm_flag_wins(self):
        cap = _StealthCapture(drm_detected=True, final_url="https://x.com")
        assert classify_stealth_failure(cap, None) == "drm-protected"

    def test_cloudflare_when_blocked_and_no_media(self):
        cap = _StealthCapture(final_url="https://x.com/cdn-cgi/challenge-platform/x")
        assert classify_stealth_failure(cap, None) == "cloudflare-blocked"

    def test_cloudflare_just_a_moment_title_marker(self):
        cap = _StealthCapture(final_url="https://x.com/?__cf_chl=just a moment")
        assert classify_stealth_failure(cap, None) == "cloudflare-blocked"

    def test_no_media_default(self):
        cap = _StealthCapture(final_url="https://x.com/video", media_urls=[])
        assert classify_stealth_failure(cap, None) == "no-media-found"

    def test_has_media_is_not_cloudflare(self):
        cap = _StealthCapture(
            final_url="https://x.com/cdn-cgi/challenge", media_urls=["https://x/v.mp4"]
        )
        assert classify_stealth_failure(cap, None) == "no-media-found"

    def test_stealth_error_is_exception(self):
        assert issubclass(_StealthError, Exception)
```

Add `_StealthError`, `_StealthCapture`, `classify_stealth_failure` to the top-of-file import block.

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_downloader.py::TestClassifyStealthFailure -v`
Expected: ImportError — cannot import name `_StealthError`.

- [ ] **Step 3: Write minimal implementation**

In `src/downloader.py`. Add `from dataclasses import dataclass, field` to the imports if not already present (it is used by `models`, but this module needs its own import — add `from dataclasses import dataclass, field` near the top). Then:

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_downloader.py::TestClassifyStealthFailure -v`
Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add src/downloader.py tests/test_downloader.py
git commit -m "feat(downloader): add _StealthError, _StealthCapture, classify_stealth_failure"
```

---

### Task 5: `_make_capture_hook` (page_action hook)

**Files:**
- Modify: `src/downloader.py`
- Test: `tests/test_downloader.py`

The hook registers a `response` listener, reloads the page so all media
requests fire with the listener attached (responses during the initial
navigation precede `page_action`), then snapshots cookies and final URL.
Tested with a fake page that records the listener and lets the test emit
fake responses.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_downloader.py`:

```python
class _FakeResp:
    def __init__(self, url, content_type=""):
        self.url = url
        self.headers = {"content-type": content_type}


class _FakePage:
    def __init__(self, cookies, url):
        self._cb = None
        self._cookies = cookies
        self.url = url
        self.reloaded = False

        class _Ctx:
            def __init__(self, c): self._c = c
            def cookies(self): return self._c

        self.context = _Ctx(cookies)

    def on(self, event, cb):
        assert event == "response"
        self._cb = cb

    def reload(self, **kwargs):
        self.reloaded = True

    def emit(self, resp):
        self._cb(resp)


class TestMakeCaptureHook:
    def test_collects_media_urls(self):
        cap = _StealthCapture()
        page = _FakePage(cookies=[{"name": "a", "value": "b"}], url="https://x.com/v")
        _make_capture_hook(cap)(page)
        page.emit(_FakeResp("https://x.com/stream.m3u8"))
        page.emit(_FakeResp("https://x.com/page", "text/html"))
        assert cap.media_urls == ["https://x.com/stream.m3u8"]

    def test_sets_drm_on_license_url(self):
        cap = _StealthCapture()
        page = _FakePage(cookies=[], url="https://x.com")
        _make_capture_hook(cap)(page)
        page.emit(_FakeResp("https://x.com/license/widevine"))
        assert cap.drm_detected is True

    def test_snapshots_cookies_and_url(self):
        cap = _StealthCapture()
        page = _FakePage(cookies=[{"name": "s", "value": "1"}], url="https://final.com/x")
        _make_capture_hook(cap)(page)
        assert cap.cookies == [{"name": "s", "value": "1"}]
        assert cap.final_url == "https://final.com/x"

    def test_reloads_to_capture_load_traffic(self):
        cap = _StealthCapture()
        page = _FakePage(cookies=[], url="https://x.com")
        _make_capture_hook(cap)(page)
        assert page.reloaded is True
```

Add `_make_capture_hook` to the top-of-file import block.

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_downloader.py::TestMakeCaptureHook -v`
Expected: ImportError — cannot import name `_make_capture_hook`.

- [ ] **Step 3: Write minimal implementation**

In `src/downloader.py`, below `classify_stealth_failure`:

```python
_DRM_PATH_RE = re.compile(r"(license|widevine|playready|/wv/|/pr/|cenc)", re.IGNORECASE)


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
        except Exception:
            pass
        try:
            capture.cookies = page.context.cookies()
        except Exception:
            capture.cookies = []
        capture.final_url = getattr(page, "url", "") or ""

    return hook
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_downloader.py::TestMakeCaptureHook -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add src/downloader.py tests/test_downloader.py
git commit -m "feat(downloader): add _make_capture_hook page_action"
```

---

### Task 6: `_ytdlp_opts` + `cookiefile_override` on `_try_ytdlp`

**Files:**
- Modify: `src/downloader.py` (`VideoDownloader._try_ytdlp`)
- Test: `tests/test_downloader.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_downloader.py`:

```python
class TestYtdlpOpts:
    def test_override_beats_instance_cookiefile(self, tmp_path):
        from src.downloader import VideoDownloader
        d = VideoDownloader(output_dir=str(tmp_path), cookies_file="/inst/c.txt")
        opts = d._ytdlp_opts("/override/c.txt")
        assert opts["cookiefile"] == "/override/c.txt"

    def test_falls_back_to_instance_cookiefile(self, tmp_path):
        from src.downloader import VideoDownloader
        d = VideoDownloader(output_dir=str(tmp_path), cookies_file="/inst/c.txt")
        opts = d._ytdlp_opts(None)
        assert opts["cookiefile"] == "/inst/c.txt"

    def test_no_cookiefile_key_when_none(self, tmp_path):
        from src.downloader import VideoDownloader
        d = VideoDownloader(output_dir=str(tmp_path))
        assert "cookiefile" not in d._ytdlp_opts(None)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_downloader.py::TestYtdlpOpts -v`
Expected: AttributeError — `VideoDownloader` has no attribute `_ytdlp_opts`.

- [ ] **Step 3: Write minimal implementation**

In `src/downloader.py`, locate `VideoDownloader._try_ytdlp`. Add a method and change the signature + opts construction. Replace the current opts block:

```python
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
```

Keep the rest of `_try_ytdlp`'s body (probe → captions → download) unchanged below the `with` line. Remove the old inline `opts = {...}` / `if self.cookies_file: opts["cookiefile"] = ...` lines that the new `_ytdlp_opts` replaces.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_downloader.py::TestYtdlpOpts -v`
Expected: 3 passed.

- [ ] **Step 5: Run the full downloader suite to confirm no regression**

Run: `python -m pytest tests/test_downloader.py -q`
Expected: all passed (existing + new).

- [ ] **Step 6: Commit**

```bash
git add src/downloader.py tests/test_downloader.py
git commit -m "refactor(downloader): extract _ytdlp_opts, add cookiefile_override"
```

---

### Task 7: `_try_stealth` orchestration (with `_run_stealth_fetch` seam)

**Files:**
- Modify: `src/downloader.py` (`VideoDownloader`)
- Test: `tests/test_downloader.py`

The real browser call lives in `_run_stealth_fetch`. Tests monkeypatch it
to populate the capture, and monkeypatch `_try_ytdlp` to simulate Stage A/B
outcomes — no browser, no network.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_downloader.py`:

```python
from src.models import DownloadResult


def _dl(output_dir):
    from src.downloader import VideoDownloader
    return VideoDownloader(output_dir=str(output_dir))


class TestTryStealth:
    def test_stage_a_cookies_then_ytdlp_success(self, tmp_path, monkeypatch):
        d = _dl(tmp_path)

        def fake_fetch(url, cap):
            cap.cookies = [{"name": "s", "value": "1", "domain": "x.com"}]

        monkeypatch.setattr(d, "_run_stealth_fetch", fake_fetch)
        captured = {}

        def fake_ytdlp(u, lang=None, cookiefile_override=None):
            captured["cf"] = cookiefile_override
            return DownloadResult(path="/tmp/a.m4a")

        monkeypatch.setattr(d, "_try_ytdlp", fake_ytdlp)
        res = d._try_stealth("https://x.com/v")
        assert res.path == "/tmp/a.m4a"
        assert captured["cf"] is not None  # transient cookiefile passed

    def test_stage_b_media_url_when_stage_a_fails(self, tmp_path, monkeypatch):
        d = _dl(tmp_path)

        def fake_fetch(url, cap):
            cap.media_urls = ["https://x.com/best.m3u8"]

        monkeypatch.setattr(d, "_run_stealth_fetch", fake_fetch)
        calls = []

        def fake_ytdlp(u, lang=None, cookiefile_override=None):
            calls.append(u)
            return DownloadResult(path="/tmp/v.mp4") if u.endswith(".m3u8") else None

        monkeypatch.setattr(d, "_try_ytdlp", fake_ytdlp)
        res = d._try_stealth("https://x.com/v")
        assert res.path == "/tmp/v.mp4"
        assert "https://x.com/best.m3u8" in calls

    def test_drm_skips_stage_b_and_raises(self, tmp_path, monkeypatch):
        d = _dl(tmp_path)

        def fake_fetch(url, cap):
            cap.media_urls = ["https://x.com/v.mp4"]
            cap.drm_detected = True

        monkeypatch.setattr(d, "_run_stealth_fetch", fake_fetch)
        monkeypatch.setattr(d, "_try_ytdlp", lambda *a, **k: None)
        with pytest.raises(_StealthError) as e:
            d._try_stealth("https://x.com/v")
        assert str(e.value) == "drm-protected"

    def test_no_media_raises_classified(self, tmp_path, monkeypatch):
        d = _dl(tmp_path)
        monkeypatch.setattr(d, "_run_stealth_fetch", lambda url, cap: None)
        monkeypatch.setattr(d, "_try_ytdlp", lambda *a, **k: None)
        with pytest.raises(_StealthError) as e:
            d._try_stealth("https://x.com/v")
        assert str(e.value) == "no-media-found"

    def test_fetch_failure_is_browser_failed(self, tmp_path, monkeypatch):
        d = _dl(tmp_path)

        def boom(url, cap):
            raise RuntimeError("browser crashed")

        monkeypatch.setattr(d, "_run_stealth_fetch", boom)
        with pytest.raises(_StealthError) as e:
            d._try_stealth("https://x.com/v")
        assert str(e.value) == "browser-failed"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_downloader.py::TestTryStealth -v`
Expected: AttributeError — `VideoDownloader` has no attribute `_try_stealth`.

- [ ] **Step 3: Write minimal implementation**

In `src/downloader.py`, add to `VideoDownloader` (after `_try_direct_fetch`). Ensure `from urllib.parse import urlparse` is imported (it already is in this module):

```python
    def _run_stealth_fetch(self, url: str, capture: "_StealthCapture") -> None:
        from scrapling.fetchers import StealthyFetcher

        StealthyFetcher.fetch(
            url,
            headless=True,
            network_idle=True,
            solve_cloudflare=True,
            timeout=STEALTH_TIMEOUT_MS,
            page_action=_make_capture_hook(capture),
        )

    def _try_stealth(
        self, url: str, preferred_lang: str | None = None
    ) -> DownloadResult | None:
        cap = _StealthCapture()
        try:
            self._run_stealth_fetch(url, cap)
        except Exception as exc:
            raise _StealthError("browser-failed") from exc

        ytdlp_err = None

        # Stage A — cookie-augmented yt-dlp retry
        if cap.cookies:
            cf = self.output_dir / f"stealth_cookies_{abs(hash(url))}.txt"
            cf.write_text(
                cookies_to_netscape(cap.cookies, urlparse(url).netloc),
                encoding="utf-8",
            )
            try:
                res = self._try_ytdlp(
                    url, preferred_lang, cookiefile_override=str(cf)
                )
                if res is not None:
                    return res
            except Exception as exc:
                ytdlp_err = exc

        # Stage B — sniffed raw media URL
        if not cap.drm_detected:
            best = pick_best_media_url(cap.media_urls)
            if best:
                try:
                    res = self._try_ytdlp(best, preferred_lang)
                    if res is not None:
                        return res
                except Exception as exc:
                    ytdlp_err = exc

        raise _StealthError(classify_stealth_failure(cap, ytdlp_err))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_downloader.py::TestTryStealth -v`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add src/downloader.py tests/test_downloader.py
git commit -m "feat(downloader): add _try_stealth two-stage orchestration"
```

---

### Task 8: Wire into `download()` chain + deps + README

**Files:**
- Modify: `src/downloader.py` (`VideoDownloader.download`)
- Modify: `requirements.txt`
- Modify: `README.md`
- Test: `tests/test_downloader.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_downloader.py`:

```python
class TestStealthInChain:
    def test_stealth_is_third_strategy(self, tmp_path):
        d = _dl(tmp_path)
        names = [s.__name__ for s in d._strategies()]
        assert names == ["_try_ytdlp", "_try_direct_fetch", "_try_stealth"]

    def test_stealth_reason_surfaces_in_runtime_error(self, tmp_path, monkeypatch):
        d = _dl(tmp_path)
        monkeypatch.setattr(d, "_try_ytdlp", lambda *a, **k: None)
        monkeypatch.setattr(d, "_try_direct_fetch", lambda *a, **k: None)

        def fake_fetch(url, cap):
            cap.final_url = "https://x.com/cdn-cgi/challenge-platform"

        monkeypatch.setattr(d, "_run_stealth_fetch", fake_fetch)
        monkeypatch.setattr(d, "_try_ytdlp", lambda *a, **k: None)
        with pytest.raises(RuntimeError) as e:
            d.download("https://x.com/v")
        assert "cloudflare-blocked" in str(e.value)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_downloader.py::TestStealthInChain -v`
Expected: AttributeError — `VideoDownloader` has no attribute `_strategies`.

- [ ] **Step 3: Write minimal implementation**

In `src/downloader.py`, locate `VideoDownloader.download`. Replace its strategy list with a `_strategies()` method and use it:

```python
    def _strategies(self):
        return [self._try_ytdlp, self._try_direct_fetch, self._try_stealth]

    def download(
        self, url: str, preferred_lang: str | None = None
    ) -> DownloadResult:
        last_error: Exception | None = None
        for strategy in self._strategies():
            try:
                result = strategy(url, preferred_lang)
                if result is not None:
                    return result
            except Exception as exc:
                log.warning(
                    "Strategy %s failed for %s: %s",
                    strategy.__name__, url, exc,
                )
                last_error = exc
        raise RuntimeError(
            f"All download strategies failed for {url}: {last_error}"
        ) from last_error
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_downloader.py::TestStealthInChain -v`
Expected: 2 passed.

- [ ] **Step 5: Add dependency**

In `requirements.txt`, add below `yt-dlp`:

```
scrapling>=0.2.9
```

- [ ] **Step 6: Document in README**

In `README.md`, under the Installation section, after the `pip install` line, add:

```markdown
The stealth download fallback uses Scrapling's headless browser. After
installing requirements, fetch its browser binaries once:

```bash
scrapling install
```
```

And in the "Supported sites" section, replace the sentence
"Headless browser extraction is not yet implemented." with:

```markdown
For sites yt-dlp cannot handle, a third strategy uses Scrapling's
`StealthyFetcher` (headless browser) to defeat anti-bot pages, harvest
cookies for a yt-dlp retry, then sniff the raw media URL. DRM-protected
content is detected and reported, never bypassed.
```

- [ ] **Step 7: Run the full suite**

Run: `python -m pytest -q`
Expected: all passed.

- [ ] **Step 8: Commit**

```bash
git add src/downloader.py tests/test_downloader.py requirements.txt README.md
git commit -m "feat(downloader): wire stealth fallback into chain, add scrapling dep + docs"
```

---

## Self-Review

**Spec coverage:**
- 3rd strategy in chain → Task 8 (`_strategies`, `download`).
- Lazy scrapling import → Task 7 (`_run_stealth_fetch`).
- Two-stage (cookies→yt-dlp, then media sniff) → Task 7 (`_try_stealth`).
- `_StealthCapture` / `_make_capture_hook` → Tasks 4, 5.
- Pure helpers (`is_media_response`, `pick_best_media_url`, `cookies_to_netscape`, `classify_stealth_failure`) → Tasks 1–4.
- `cookiefile_override` on `_try_ytdlp` → Task 6.
- DRM detection via network signals, skips Stage B → Tasks 5, 7.
- Hard fail with classified reason in `RuntimeError` → Tasks 7, 8.
- Transient cookie file under `output_dir`, cleaned by existing workdir wipe → Task 7 (writes under `self.output_dir`; no new cleanup path, matches spec).
- `STEALTH_TIMEOUT_MS` cap → Task 4 (const), Task 7 (passed to fetch).
- Orchestration tested via monkeypatch, browser integration not unit-tested → Task 7 design.
- Deps + README → Task 8.

All spec sections map to a task. No gaps.

**Placeholder scan:** No TBD/TODO/"handle edge cases". Every code step has full code; every test step has full test bodies.

**Type consistency:** `_StealthCapture` fields (`cookies`, `media_urls`, `final_url`, `drm_detected`) used identically across Tasks 4–8. `_try_ytdlp` signature `(url, preferred_lang=None, cookiefile_override=None)` consistent in Tasks 6 and 7. `_run_stealth_fetch(url, capture)` and `_try_stealth(url, preferred_lang=None)` consistent in Task 7 and the Task 8 chain call `strategy(url, preferred_lang)`. `classify_stealth_failure(capture, ytdlp_err)` arity consistent. No mismatches.
