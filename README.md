# Transcriber

Agentic video transcription system. Give it a URL, get back a transcript.

Supports YouTube, Vimeo, Twitter/X, Instagram, TikTok, and direct media links (`.mp4`, `.webm`, `.m3u8`, etc). Falls back to existing captions when available, otherwise downloads, chunks, and transcribes locally via [faster-whisper](https://github.com/SYSTRAN/faster-whisper).

## Requirements

- Python 3.11+
- [FFmpeg](https://ffmpeg.org/download.html) on `PATH`
- CUDA-capable GPU recommended (CPU works, slower)

## Installation

```bash
pip install -r requirements-dev.txt
```

Copy and configure the environment file:

```bash
cp .env.example .env   # or edit .env directly
```

## Usage

```python
from src.agent import TranscriptionAgent

agent = TranscriptionAgent(
    model_size="large-v3",   # Whisper model
    device="auto",           # "cuda", "cpu", or "auto"
    compute_type="auto",     # "float16", "int8", or "auto"
    output_dir="./output",   # where downloads land
    cookies_file=None,       # path to cookies.txt for gated content
)

result = agent.transcribe(
    url="https://www.youtube.com/watch?v=example",
    language=None,           # None = auto-detect
    output_format="text",    # "text" | "srt" | "vtt" | "json"
)

print(result)
agent.cleanup()  # delete temp files
```

### Output formats

| Format | Use case |
|--------|----------|
| `text` | Plain transcript, space-joined |
| `srt`  | SubRip subtitles for video players |
| `vtt`  | WebVTT for web players |
| `json` | Structured with `text`, `start`, `end` per segment |

## Configuration

All options can be set via `.env` (loaded by your shell or a library like `python-dotenv`):

| Variable | Default | Description |
|----------|---------|-------------|
| `WHISPER_MODEL_SIZE` | `large-v3` | Model size: `tiny`, `base`, `small`, `medium`, `large-v3` |
| `WHISPER_DEVICE` | `auto` | `cuda`, `cpu`, or `auto` |
| `WHISPER_COMPUTE_TYPE` | `auto` | `float16`, `int8_float16`, `int8`, or `auto` |
| `OUTPUT_DIR` | system temp | Where downloads and audio chunks are written |
| `COOKIES_FILE` | _(none)_ | Path to Netscape cookies file for age-gated content |
| `LOG_LEVEL` | `INFO` | Python logging level |

## Architecture

```
URL → classify → download (yt-dlp) → extract audio (FFmpeg)
    → chunk on silence boundaries   → transcribe chunks (faster-whisper)
    → merge + deduplicate overlap   → format output
```

Key design decisions:
- **Silence-based chunking** — splits at quiet boundaries near every 10 minutes, not at fixed intervals, to avoid cutting mid-word.
- **Overlap deduplication** — each chunk overlaps the next by 2s; near-duplicate segments in the overlap zone are removed via fuzzy string matching.
- **Caption shortcut** — if yt-dlp finds existing subtitles, the transcription step is skipped entirely.

## Development

```bash
# Run tests
python -m pytest -v

# Run a specific module
python -m pytest tests/test_transcriber.py -v
```

All core logic (chunking, merging, deduplication, URL classification) is covered by unit tests and runs without FFmpeg, yt-dlp, or a GPU.

## Supported sites

yt-dlp supports [1000+ sites](https://github.com/yt-dlp/yt-dlp/blob/master/supportedsites.md). For sites yt-dlp cannot handle, the downloader falls back to direct URL fetch (works for bare `.mp4`/`.webm` links). Headless browser extraction is not yet implemented.

## Known limitations

- DRM-protected content cannot be downloaded.
- Speaker diarization (`pyannote-audio`) is not yet wired up.
- LLM post-processing (punctuation cleanup, chapter detection) is not yet wired up.
