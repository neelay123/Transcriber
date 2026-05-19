# Stealth Download Fallback — Design

Date: 2026-05-18
Status: Approved (brainstorming), pending implementation plan

## Problem

`VideoDownloader.download()` has two strategies: `_try_ytdlp` (probe →
captions / media) and `_try_direct_fetch` (bare media URL). Both fail on
anti-bot-protected pages (Cloudflare Turnstile), JS-rendered pages where the
media URL is obfuscated, and sites yt-dlp has no extractor for. There is no
headless-browser fallback. This adds a third strategy using Scrapling's
`StealthyFetcher` to defeat anti-bot, harvest cookies, and sniff the real
media URL.

## Locked Decisions

1. **Role:** both, in order — cookie-capture → yt-dlp retry first, then
   network media-URL sniffing.
2. **Activation:** auto, always on. `scrapling` becomes a required
   dependency. Tried as the 3rd strategy whenever the first two fail.
3. **Failure:** hard fail with a clear, classified error
   (`drm-protected` | `cloudflare-blocked` | `no-media-found` |
   `browser-failed`). DRM is explicitly unsupported. Per-call browser
   timeout caps runtime.
4. **Approach:** A — single stealth strategy, internal two-stage. One
   browser launch per call; cookies + media URLs captured in the same
   session because browser spin-up + Cloudflare solve is the cost
   bottleneck.

## Architecture & Integration

New 3rd strategy in `src/downloader.py`. Strategy chain:

```
download(url, preferred_lang)
  → _try_ytdlp        (probe → captions / media)
  → _try_direct_fetch (bare media URL)
  → _try_stealth      (NEW — browser, anti-bot)
  → RuntimeError(all 3 exhausted, classified reason)
```

`_try_stealth(url, preferred_lang) -> DownloadResult | None` has the same
signature and return contract as its siblings (returns a `DownloadResult`
with `path` **or** `caption_segments`, or `None`). It slots into the
existing `for strategy in strategies` loop with no orchestration change.

`scrapling` is added to `requirements.txt` (always-on). It is imported
lazily *inside* `_try_stealth`, matching the existing lazy `import yt_dlp`
pattern — module import stays cheap and unit tests run without browser
binaries.

## Components

New in `src/downloader.py`:

| Component | Type | Purpose |
|---|---|---|
| `_try_stealth(url, preferred_lang)` | method | Orchestrates fetch → Stage A → Stage B |
| `_StealthCapture` | dataclass | `cookies: list[dict]`, `media_urls: list[str]`, `final_url: str`, `drm_detected: bool` |
| `_make_capture_hook(capture)` | factory | Returns a `page_action` fn: registers `page.on("response")`, snapshots `page.context.cookies()`, sets the DRM flag |

Pure helpers (unit-testable, no browser):

- `is_media_response(url: str, content_type: str) -> bool` — media-URL
  regex (`.mp4`, `.m3u8`, `.webm`, `.mpd`, `.m4a`) OR content-type prefix
  (`video/`, `audio/`, `application/vnd.apple.mpegurl`,
  `application/x-mpegURL`, `application/dash+xml`).
- `pick_best_media_url(urls: list[str]) -> str | None` — priority
  `m3u8 > mp4 > webm`; `.mpd` (DASH, commonly DRM) deprioritized; empty
  list → `None`.
- `cookies_to_netscape(cookies: list[dict], domain: str) -> str` —
  Playwright cookie dicts → Netscape `cookies.txt` text. Empty list →
  header-only file.
- `classify_stealth_failure(capture, ytdlp_err) -> str` — returns one of
  three values: `"drm-protected"` (`capture.drm_detected` is True);
  `"cloudflare-blocked"` (no media URLs captured AND `capture.final_url`
  still on a challenge path / page title contains "Just a moment" /
  Cloudflare `cf-` cookie present); `"no-media-found"` (default).
  `"browser-failed"` is NOT produced here — it is raised directly when
  `fetch()` itself throws (see Data Flow), bypassing classification.

DRM detection (`_StealthCapture.drm_detected`, set inside the hook) is
driven by concrete sniffed-network signals: a request URL path containing
`license`, `widevine`, or `playready`; OR a captured `.mpd` manifest whose
body/headers indicate `cenc` / encrypted ContentProtection. No EME
JavaScript introspection — network signals only, kept deterministic and
testable.

Refactor: add an optional `cookiefile_override: str | None = None`
parameter to `_try_ytdlp` so Stage A can pass a transient cookiefile
without mutating `self.cookies_file`. No yt-dlp logic is duplicated.

## Data Flow

```
_try_stealth(url, lang):
  cap = _StealthCapture()
  try:
      StealthyFetcher.fetch(url, headless=True, network_idle=True,
          solve_cloudflare=True, timeout=STEALTH_TIMEOUT_MS,
          page_action=_make_capture_hook(cap))
  except Exception as e:
      raise _StealthError("browser-failed") from e

  ytdlp_err = None
  # Stage A — cookie-augmented yt-dlp retry (cheap, mature extractors)
  if cap.cookies:
      cf = <write cookies_to_netscape under self.output_dir>
      try:
          res = self._try_ytdlp(url, lang, cookiefile_override=cf)
          if res: return res
      except Exception as e:
          ytdlp_err = e

  # Stage B — sniffed raw media URL (JS/obfuscated case)
  if not cap.drm_detected:
      best = pick_best_media_url(cap.media_urls)
      if best:
          res = self._try_ytdlp(best, lang)   # yt-dlp on raw URL → HLS/DASH mux
          if res: return res

  raise _StealthError(classify_stealth_failure(cap, ytdlp_err))
```

One browser launch. DRM detected → Stage B skipped entirely.
`_StealthError` is a plain `Exception` subclass; it propagates through the
existing `download()` except-loop, is recorded as `last_error`, and its
classified message appears in the final `RuntimeError`.

`STEALTH_TIMEOUT_MS` — module constant, default 60000 (browser is slow);
passed as the `fetch()` timeout, caps the per-call runtime.

## Error Handling

- `scrapling` import / browser-binary failure → caught by the `download()`
  loop, logged, strategy skipped (defensive even though it is a required
  dep).
- `fetch()` timeout / browser crash → `_StealthError("browser-failed")`.
- Cloudflare unsolved → no media + unusable cookies →
  `"cloudflare-blocked"`.
- DRM detected (license endpoint path / `cenc` / encrypted `.mpd`
  ContentProtection in sniffed network — see Components) →
  `"drm-protected"`. Explicitly unsupported; the error message states
  this and includes a brief legal note.
- No media, no usable cookies → `"no-media-found"`.
- Final `RuntimeError` lists all three strategies plus the stealth reason.
- The transient cookie file is written under `self.output_dir` and removed
  by the existing agent workdir wipe — no new cleanup path.

## Testing

**Unit (TDD, no browser):**

- `is_media_response` — mp4 / m3u8 / mpd / webm URLs and matching
  content-types → True; html / json → False.
- `pick_best_media_url` — priority order; empty → None; `.mpd`
  deprioritized below mp4.
- `cookies_to_netscape` — Playwright dict → valid Netscape line (domain,
  flag, path, secure, expiry, name, value); empty list → header-only.
- `classify_stealth_failure` — each branch.
- Orchestration test: monkeypatch `StealthyFetcher.fetch` to inject a
  fake populated `_StealthCapture`; assert Stage A / Stage B routing and
  DRM-skip without launching a browser.

**Integration (not unit-tested, consistent with the rest of the
codebase):** real `StealthyFetcher` browser fetch + `page_action` hook —
manual / optional, skipped when browser binaries are absent.

**Dependencies:** `scrapling` added to `requirements.txt`;
`scrapling install` (browser binaries) documented in README; CI note that
the integration path is browser-heavy and gated.

## Out of Scope

- DRM bypass (legally and technically unsupported; detected and reported,
  not circumvented).
- Soft/partial-result returns — failure is always a hard, classified
  error.
- A second browser launch / separate sniffing strategy (rejected
  Approach B).
