"""Passo 5: gera o Markdown do card na raiz do repositório."""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

from core.exceptions import DocumentationError
from core.models import PipelineReport

logger = logging.getLogger(__name__)


def _render(report: PipelineReport, diff_stat: str) -> str:
    usage = report.claude.usage if report.claude else None
    files = "\n".join(f"- `{line}`" for line in report.changed_files) or "_Nenhum arquivo alterado._"

    lines = [
        f"# Card {report.input.card_number}",
        "",
        f"- **Data:** {datetime.now():%Y-%m-%d %H:%M:%S}",
        f"- **Classificação:** {report.classification.classification if report.classification else '-'}",
        f"- **Branch:** {report.branch.name if report.branch else '-'}",
        "",
        "## Solicitação",
        "",
        report.input.request,
        "",
        "## Arquivos alterados",
        "",
        files,
        "",
    ]
    if diff_stat:
        lines += ["## Resumo do diff", "", "```", diff_stat, "```", ""]
    lines += [
        "## Resumo do Claude Code",
        "",
        report.claude.output if report.claude else "_Sem saída._",
        "",
    ]
    if usage:
        lines += [
            "## Consumo do Claude Code",
            "",
            f"- Tokens de entrada: {usage.total_input}",
            f"- Tokens de saída: {usage.output_tokens}",
            "",
        ]
    return "\n".join(lines)


def write_card_document(report: PipelineReport, filename_template: str, diff_stat: str = "") -> Path:
    path = report.input.repo_path / filename_template.format(card=report.input.card_number)
    try:
        path.write_text(_render(report, diff_stat), encoding="utf-8")
    except OSError as exc:
        raise DocumentationError(f"Não foi possível gravar {path}: {exc}") from exc
    logger.info("Documento do card gerado em %s", path)
    return path
