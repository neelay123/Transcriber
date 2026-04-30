from dataclasses import dataclass, field


@dataclass
class AudioChunk:
    path: str
    start: float
    end: float
    overlap_with_next: float = 0.0


@dataclass
class TranscriptSegment:
    text: str
    start: float
    end: float
    confidence: float
    speaker: str | None = None


@dataclass
class TranscriptionResult:
    segments: list[TranscriptSegment]
    chunk: AudioChunk
    confidence: float


@dataclass
class Silence:
    start: float
    end: float


@dataclass
class DownloadResult:
    path: str | None
    metadata: dict = field(default_factory=dict)
    captions: dict = field(default_factory=dict)

    @property
    def has_captions(self) -> bool:
        return bool(self.captions)


@dataclass
class AgentState:
    url: str
    options: dict
    plan: dict = field(default_factory=dict)
    media_path: str | None = None
    chunks: list[AudioChunk] = field(default_factory=list)
    segments: list[TranscriptSegment] = field(default_factory=list)
    errors: list[Exception] = field(default_factory=list)
    language: str | None = None
