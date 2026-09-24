"""Fixtures compartilhadas: repositórios Git descartáveis, classificador falso e Claude Code falso."""

from __future__ import annotations

import json
import stat
import subprocess
import sys
import textwrap
from dataclasses import replace
from pathlib import Path

import pytest

from core.models import ClassificationResult, TokenUsage
from core.settings import ClaudeCodeSettings, Settings

GIT_IDENTITY = ["-c", "user.name=Teste", "-c", "user.email=teste@example.com"]


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *GIT_IDENTITY, *args], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()


@pytest.fixture
def make_repo(tmp_path):
    """Cria um repositório Git com um commit vazio no branch `main`."""

    def _make(name: str = "repo", branch: str = "main") -> Path:
        repo = tmp_path / name
        repo.mkdir()
        git(repo, "init", "-q", "-b", branch)
        git(repo, "commit", "-q", "--allow-empty", "-m", "init")
        return repo

    return _make


@pytest.fixture
def repo(make_repo) -> Path:
    return make_repo()


class FakeClassifier:
    def __init__(self, label: str = "feature", error: Exception | None = None) -> None:
        self.label = label
        self.error = error
        self.requests: list[str] = []

    def classify(self, request: str) -> ClassificationResult:
        self.requests.append(request)
        if self.error:
            raise self.error
        return ClassificationResult(classification=self.label, usage=TokenUsage(input_tokens=100, output_tokens=1))


@pytest.fixture
def fake_classifier() -> FakeClassifier:
    return FakeClassifier()


DEFAULT_PAYLOAD = {
    "type": "result",
    "subtype": "success",
    "is_error": False,
    "result": "Arquivo criado.",
    "duration_ms": 1500,
    "total_cost_usd": 0.02,
    "usage": {"input_tokens": 1000, "output_tokens": 200, "cache_read_input_tokens": 500},
}


@pytest.fixture
def fake_claude(tmp_path):
    """Gera um executável que imita `claude -p --output-format json`.

    Registra os argumentos recebidos em `<binário>.args.json`, cria `touch` no cwd (o repositório),
    espera `sleep` segundos (com um processo filho, como um comando do Claude Code) e imprime `stdout`.
    """

    def _make(
        payload: dict | None = None,
        *,
        stdout: str | None = None,
        exit_code: int = 0,
        touch: str | None = "novo.txt",
        sleep: float = 0,
        name: str = "fake-claude",
    ) -> Path:
        binary = tmp_path / name
        out = stdout if stdout is not None else json.dumps(payload or DEFAULT_PAYLOAD)
        binary.write_text(
            textwrap.dedent(f"""\
                #!{sys.executable}
                import json, pathlib, subprocess, sys
                pathlib.Path({str(binary) + ".args.json"!r}).write_text(json.dumps(sys.argv[1:]))
                if {touch!r}:
                    pathlib.Path({touch!r}).write_text("x")
                if {sleep!r}:
                    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep({sleep!r})"])
                    pathlib.Path({str(binary) + ".child"!r}).write_text(str(child.pid))
                    child.wait()
                sys.stdout.write({out!r})
                sys.exit({exit_code!r})
            """)
        )
        binary.chmod(binary.stat().st_mode | stat.S_IXUSR)
        return binary

    return _make


@pytest.fixture
def settings_for():
    """Settings padrão apontando o Claude Code para um binário falso."""

    def _make(binary: Path | str = "claude", **claude_overrides) -> Settings:
        return replace(Settings(), claude_code=ClaudeCodeSettings(binary=str(binary), **claude_overrides))

    return _make
