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

from core.models import ClassificationResult, RedmineIssue, TokenUsage
from core.settings import ClaudeCodeSettings, HistorySettings, Settings

GIT_IDENTITY = ["-c", "user.name=Teste", "-c", "user.email=teste@example.com"]


def git(repo: Path, *args: str, check: bool = True) -> str:
    return subprocess.run(
        ["git", *GIT_IDENTITY, *args], cwd=repo, check=check, capture_output=True, text=True
    ).stdout.strip()


@pytest.fixture(autouse=True)
def sem_servicos_do_ambiente(monkeypatch):
    """Nenhum teste fala com o Redmine ou o GitLab reais: quem precisa configura as variáveis e injeta o transporte."""
    for name in ("REDMINE_URL", "REDMINE_API_KEY", "GITLAB_URL", "GITLAB_TOKEN"):
        monkeypatch.delenv(name, raising=False)


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


@pytest.fixture
def remote_setup(tmp_path, make_repo):
    """Repositório `local` clonado de um remoto bare, mais um `outro` clone para empurrar commits novos ao remoto.

    Retorna (local, outro, push), onde push(branch, arquivo) cria um commit no `outro` e envia ao remoto.
    """
    seed = make_repo("seed")
    bare = tmp_path / "remoto.git"
    git(tmp_path, "clone", "-q", "--bare", str(seed), str(bare))
    local, other = tmp_path / "local", tmp_path / "outro"
    for clone in (local, other):
        git(tmp_path, "clone", "-q", str(bare), str(clone))

    def push(branch: str, filename: str) -> None:
        if git(other, "rev-parse", "--abbrev-ref", "HEAD") != branch:
            if subprocess.run(["git", "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"], cwd=other).returncode:
                git(other, "checkout", "-q", "-b", branch)
            else:
                git(other, "checkout", "-q", branch)
        (other / filename).write_text("x")
        git(other, "add", "-A")
        git(other, "commit", "-q", "-m", f"add {filename}")
        git(other, "push", "-q", "origin", branch)

    return local, other, push


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
    """Gera um executável que imita `claude -p --output-format stream-json`.

    Registra os argumentos recebidos em `<binário>.args.json`, imprime `events` (um JSON por linha), cria `touch`
    no cwd (o repositório), espera `sleep` segundos (com um processo filho, como um comando do Claude Code) e
    imprime `stdout` (por padrão, o evento final `result`).
    """

    def _make(
        payload: dict | None = None,
        *,
        stdout: str | None = None,
        exit_code: int = 0,
        touch: str | None = "novo.txt",
        sleep: float = 0,
        name: str = "fake-claude",
        events: list[dict] | None = None,
    ) -> Path:
        binary = tmp_path / name
        out = stdout if stdout is not None else json.dumps({"type": "result", **(payload or DEFAULT_PAYLOAD)})
        before = "".join(json.dumps(event) + "\n" for event in events or [])
        binary.write_text(
            textwrap.dedent(f"""\
                #!{sys.executable}
                import json, pathlib, subprocess, sys
                pathlib.Path({str(binary) + ".args.json"!r}).write_text(json.dumps(sys.argv[1:]))
                sys.stdout.write({before!r})
                sys.stdout.flush()
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
def history_path(tmp_path) -> Path:
    return tmp_path / "history.db"


@pytest.fixture
def settings_for(history_path):
    """Settings padrão apontando o Claude Code para um binário falso e o histórico para um arquivo temporário."""

    def _make(binary: Path | str = "claude", **claude_overrides) -> Settings:
        return replace(
            Settings(),
            claude_code=ClaudeCodeSettings(binary=str(binary), **claude_overrides),
            history=HistorySettings(path=str(history_path)),
        )

    return _make


def make_issue(tracker: str = "Evolução", **overrides) -> RedmineIssue:
    data = {
        "id": "1425",
        "subject": "Adicionar health check",
        "description": "Criar o endpoint /health.",
        "tracker": tracker,
        "status": "Analisar",
        "project": "DCCA - SIM",
        "url": "https://redmine.exemplo/issues/1425",
        "custom_fields": {"critérios de aceitação": "Responder 200."},
    }
    return RedmineIssue(**{**data, **overrides})


class FakeRedmine:
    """Substitui o RedmineClient: devolve `issue` (ou levanta `error`) e registra as atualizações.

    `note_error` falha a atualização (nota/status); `workflow_blocks` simula o Redmine ignorando o status;
    `time_error` falha o lançamento de horas.
    """

    def __init__(self, issue: RedmineIssue | None = None, error: Exception | None = None, note_error=None,
                 workflow_blocks: bool = False, time_error=None):
        self.issue = issue or make_issue()
        self.error = error
        self.note_error = note_error
        self.workflow_blocks = workflow_blocks
        self.time_error = time_error
        self.fetched: list[str] = []
        self.updates: list[tuple[str, str | None, str | None]] = []
        self.time_entries: list[tuple[str, float, str, str]] = []

    @property
    def notes(self) -> list[tuple[str, str]]:
        return [(issue_id, notes) for issue_id, notes, _ in self.updates if notes]

    def get_issue(self, issue_id: str) -> RedmineIssue:
        self.fetched.append(issue_id)
        if self.error:
            raise self.error
        return self.issue

    def update_issue(self, issue_id: str, notes: str | None = None, status: str | None = None):
        if self.note_error:
            raise self.note_error
        self.updates.append((issue_id, notes, status))
        current = None
        if status:
            current = self.issue.status if self.workflow_blocks else status
        return f"https://redmine.exemplo/issues/{issue_id}", current

    def add_note(self, issue_id: str, notes: str) -> str:
        return self.update_issue(issue_id, notes=notes)[0]

    def log_time(self, issue_id: str, hours: float, activity: str, comments: str = "") -> None:
        if self.time_error:
            raise self.time_error
        self.time_entries.append((issue_id, hours, activity, comments))


class FakeGitLab:
    """Substitui o GitLabClient: registra o MR pedido e devolve uma URL (ou levanta `error`)."""

    def __init__(self, error: Exception | None = None):
        self.error = error
        self.requests: list[dict] = []

    def project_for_remote(self, remote_url: str) -> str:
        return "grupo/projeto"

    def create_merge_request(self, project, source, target, title, description) -> str:
        if self.error:
            raise self.error
        self.requests.append(
            {"project": project, "source": source, "target": target, "title": title, "description": description}
        )
        return "https://ginner.exemplo/grupo/projeto/-/merge_requests/1"


@pytest.fixture
def redmine_env(monkeypatch):
    """Redmine "configurado" (as chamadas reais continuam proibidas: os testes injetam um substituto)."""
    monkeypatch.setenv("REDMINE_URL", "https://redmine.exemplo/")
    monkeypatch.setenv("REDMINE_API_KEY", "chave-teste")


@pytest.fixture
def gitlab_env(monkeypatch):
    monkeypatch.setenv("GITLAB_URL", "https://ginner.exemplo")
    monkeypatch.setenv("GITLAB_TOKEN", "token-teste")
