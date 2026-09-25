"""Carrega config/settings.yaml em dataclasses tipadas, com valores padrão."""

from __future__ import annotations

from dataclasses import dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

from core.exceptions import ConfigError

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SETTINGS_PATH = PROJECT_ROOT / "config" / "settings.yaml"
DOTENV_PATH = PROJECT_ROOT / ".env"


@dataclass(frozen=True)
class GitSettings:
    protected_branches: tuple[str, ...] = ("main", "master")
    command_timeout_seconds: int = 30
    remote: str = "origin"
    fetch: bool = True  # atualiza a base (git fetch) antes de criar o branch do card
    network_timeout_seconds: int = 120  # fetch e push
    # Classificação -> branch base (ex.: feature: develop). Vazio = o branch protegido atual.
    base_branches: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ClassifierSettings:
    model: str = "claude-haiku-4-5"
    max_tokens: int = 16
    timeout_seconds: int = 30


@dataclass(frozen=True)
class ClaudeCodeSettings:
    binary: str = "claude"
    model: str | None = None
    permission_mode: str = "acceptEdits"
    timeout_seconds: int = 1800


@dataclass(frozen=True)
class DocumentationSettings:
    filename_template: str = "CARD-{card}.md"


def _default_tracker_classification() -> dict[str, str]:
    # Só tipos sem ambiguidade; "Correção" fica com o LLM (é usado tanto para bug urgente quanto para ajuste simples).
    return {
        "Nova funcionalidade": "feature",
        "Evolução": "feature",
        "Story": "feature",
        "Incidente": "hotfix",
        "Violação de segurança": "hotfix",
        "GMUD-Software": "release",
    }


@dataclass(frozen=True)
class RedmineSettings:
    url: str | None = None  # a variável REDMINE_URL tem precedência; a chave vem só de REDMINE_API_KEY
    timeout_seconds: int = 30
    verify_ssl: bool = True
    # Tipo (rastreador) da tarefa -> classificação; tipos fora do mapa são classificados pelo LLM.
    tracker_classification: dict[str, str] = field(default_factory=_default_tracker_classification)
    # Campos personalizados da tarefa que entram na solicitação (nome exato, sem diferenciar maiúsculas).
    custom_fields: tuple[str, ...] = ("critérios de aceitação",)
    # Status sugerido ao concluir o card e atividade padrão do lançamento de horas (nomes como no Redmine).
    conclusion_status: str = "Homologar"
    time_entry_activity: str = "Codificação"


@dataclass(frozen=True)
class GitLabSettings:
    url: str | None = None  # a variável GITLAB_URL tem precedência; o token vem só de GITLAB_TOKEN
    timeout_seconds: int = 30
    verify_ssl: bool = True
    merge_request_default: bool = False  # valor inicial da opção "abrir merge request" (CLI e web)
    remove_source_branch: bool = False  # marca "apagar o branch do card após o merge" no MR


@dataclass(frozen=True)
class HistorySettings:
    enabled: bool = True
    path: str = "data/history.db"  # relativo à raiz do projeto


@dataclass(frozen=True)
class LoggingSettings:
    level: str = "INFO"
    file: str = "logs/pipeline.log"


@dataclass(frozen=True)
class Settings:
    git: GitSettings = field(default_factory=GitSettings)
    classifier: ClassifierSettings = field(default_factory=ClassifierSettings)
    claude_code: ClaudeCodeSettings = field(default_factory=ClaudeCodeSettings)
    documentation: DocumentationSettings = field(default_factory=DocumentationSettings)
    redmine: RedmineSettings = field(default_factory=RedmineSettings)
    gitlab: GitLabSettings = field(default_factory=GitLabSettings)
    history: HistorySettings = field(default_factory=HistorySettings)
    logging: LoggingSettings = field(default_factory=LoggingSettings)


def _build(cls: type, data: dict[str, Any] | None, section: str):
    data = data or {}
    if not isinstance(data, dict):
        raise ConfigError(f"Seção '{section}' do settings.yaml deve ser um mapeamento.")
    known = {f.name: f for f in fields(cls)}
    unknown = set(data) - set(known)
    if unknown:
        raise ConfigError(f"Chaves desconhecidas em '{section}': {', '.join(sorted(unknown))}")

    kwargs: dict[str, Any] = {}
    for name, value in data.items():
        default = known[name].default_factory() if callable(known[name].default_factory) else None
        if is_dataclass(default):
            kwargs[name] = _build(type(default), value, f"{section}.{name}" if section else name)
        elif isinstance(value, list):
            kwargs[name] = tuple(value)
        else:
            kwargs[name] = value
    return cls(**kwargs)


def load_settings(path: Path = DEFAULT_SETTINGS_PATH) -> Settings:
    # Credenciais vêm do .env da raiz do projeto; variáveis já exportadas no shell têm precedência.
    load_dotenv(DOTENV_PATH, override=False)
    if not path.exists():
        return Settings()
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"settings.yaml inválido: {exc}") from exc
    return _build(Settings, raw, "")
