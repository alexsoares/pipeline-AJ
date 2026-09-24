"""Estruturas de dados trocadas entre os passos da pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

Classification = Literal["feature", "hotfix", "release"]
VALID_CLASSIFICATIONS: tuple[str, ...] = ("feature", "hotfix", "release")


@dataclass(frozen=True)
class PipelineInput:
    repo_path: Path
    card_number: str
    request: str


@dataclass
class TokenUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0

    @property
    def total_input(self) -> int:
        """Todos os tokens de entrada processados, incluindo escrita e leitura de cache."""
        return self.input_tokens + self.cache_creation_input_tokens + self.cache_read_input_tokens

    @property
    def total(self) -> int:
        return self.total_input + self.output_tokens

    def __add__(self, other: TokenUsage) -> TokenUsage:
        return TokenUsage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            cache_creation_input_tokens=self.cache_creation_input_tokens + other.cache_creation_input_tokens,
            cache_read_input_tokens=self.cache_read_input_tokens + other.cache_read_input_tokens,
        )


@dataclass(frozen=True)
class GitStatus:
    branch: str
    dirty_files: list[str]


@dataclass(frozen=True)
class BranchResult:
    name: str
    created: bool
    switched: bool


@dataclass(frozen=True)
class ClassificationResult:
    classification: Classification
    usage: TokenUsage


@dataclass(frozen=True)
class ClaudeRunResult:
    output: str
    usage: TokenUsage
    duration_seconds: float
    cost_usd: float | None = None


@dataclass
class PipelineReport:
    input: PipelineInput
    initial_status: GitStatus | None = None
    classification: ClassificationResult | None = None
    branch: BranchResult | None = None
    claude: ClaudeRunResult | None = None
    changed_files: list[str] = field(default_factory=list)
    document_path: Path | None = None
    elapsed_seconds: float = 0.0

    @property
    def total_usage(self) -> TokenUsage:
        usage = TokenUsage()
        if self.classification:
            usage += self.classification.usage
        if self.claude:
            usage += self.claude.usage
        return usage
