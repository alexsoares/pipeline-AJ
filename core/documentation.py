"""Gera o Markdown do card (CARD-<n>.md) na raiz do repositório."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from core.exceptions import DocumentationError

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CardDocument:
    card: str
    classification: str
    branch: str
    base: str
    request: str = ""
    changed_files: list[str] = field(default_factory=list)
    diff_stat: str = ""
    claude_output: str = ""


def render(doc: CardDocument) -> str:
    files = "\n".join(f"- `{line}`" for line in doc.changed_files) or "_Nenhum arquivo alterado._"
    lines = [
        f"# Card {doc.card}",
        "",
        f"- **Data:** {datetime.now():%Y-%m-%d %H:%M:%S}",
        f"- **Classificação:** {doc.classification}",
        f"- **Branch:** `{doc.branch}` (base: `{doc.base}`)",
        "",
        "## Solicitação",
        "",
        doc.request or "_Não informada._",
        "",
        "## Arquivos alterados",
        "",
        files,
        "",
    ]
    if doc.diff_stat:
        lines += ["## Resumo do diff", "", "```", doc.diff_stat, "```", ""]
    if doc.claude_output:
        lines += ["## Resumo do Claude Code", "", doc.claude_output, ""]
    return "\n".join(lines)


def write_card_document(repo_path: Path, filename_template: str, doc: CardDocument) -> Path:
    path = repo_path / filename_template.format(card=doc.card)
    try:
        path.write_text(render(doc), encoding="utf-8")
    except OSError as exc:
        raise DocumentationError(f"Não foi possível gravar {path}: {exc}") from exc
    logger.info("Documento do card gerado em %s", path)
    return path
