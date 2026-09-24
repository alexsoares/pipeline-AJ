"""Orquestrador: executa os passos 1 a 3 em sequência e monta o relatório do passo 4.

A implementação do card não faz parte da pipeline: ela é feita depois, no Claude Code,
já no branch preparado aqui.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Any, TypeVar

from core.classifier import RequestClassifier
from core.exceptions import PipelineError
from core.git_manager import GitManager
from core.models import PipelineInput, PipelineReport, TokenUsage
from core.settings import Settings

logger = logging.getLogger(__name__)
T = TypeVar("T")

STEPS: dict[int, str] = {
    1: "status do Git",
    2: "classificação da solicitação",
    3: "preparação do branch",
    4: "resposta consolidada",
}

# Recebe (número do passo, estado), onde estado é "running", "done" ou "failed".
StepCallback = Callable[[int, str], None]


class StepFailed(PipelineError):
    """Envolve o erro original com o número e o nome do passo que falhou."""

    def __init__(self, step: int, cause: PipelineError) -> None:
        super().__init__(f"Falha no passo {step} ({STEPS[step]}): {cause}")
        self.step = step
        self.cause = cause


class Pipeline:
    def __init__(
        self,
        settings: Settings,
        classifier: RequestClassifier | None = None,
        on_step: StepCallback | None = None,
    ) -> None:
        self.settings = settings
        self.classifier = classifier or RequestClassifier(settings.classifier)
        self.on_step = on_step

    def _notify(self, number: int, state: str) -> None:
        if not self.on_step:
            return
        try:
            self.on_step(number, state)
        except Exception:  # noqa: BLE001 - quem observa o progresso não pode derrubar a pipeline
            logger.exception("Falha no callback de progresso do passo %d", number)

    def _step(self, number: int, action: Callable[[], T]) -> T:
        logger.info("Passo %d: %s", number, STEPS[number])
        self._notify(number, "running")
        try:
            result = action()
        except PipelineError as exc:
            logger.error("Passo %d (%s) falhou: %s", number, STEPS[number], exc)
            self._notify(number, "failed")
            raise StepFailed(number, exc) from exc
        except Exception:
            self._notify(number, "failed")
            raise
        self._notify(number, "done")
        return result

    def run(self, data: PipelineInput) -> PipelineReport:
        started = time.perf_counter()
        report = PipelineReport(input=data)
        git = GitManager(data.repo_path, self.settings.git.command_timeout_seconds)

        try:
            report.initial_status = self._step(1, git.status)
            if report.initial_status.dirty_files:
                logger.warning("Working tree com %d alteração(ões) pendente(s).", len(report.initial_status.dirty_files))

            report.classification = self._step(2, lambda: self.classifier.classify(data.request))

            branch_name = f"{report.classification.classification}/card-{data.card_number}"
            report.branch = self._step(
                3, lambda: git.ensure_work_branch(branch_name, self.settings.git.protected_branches)
            )
        finally:
            report.elapsed_seconds = time.perf_counter() - started

        self._notify(4, "done")
        return report


def format_report(report: PipelineReport) -> str:
    """Passo 4: resposta consolidada para o chat."""
    usage = report.total_usage
    branch = report.branch
    branch_info = f"{branch.name} ({'criado' if branch.created else 'existente'})" if branch else "-"

    lines = [
        f"==================== AGENTE AJ | Card {report.input.card_number} ====================",
        f"Repositório   : {report.input.repo_path}",
        f"Classificação : {report.classification.classification if report.classification else '-'}",
        f"Branch        : {branch_info}",
        "",
        "--- Próximo passo ---",
        "Abra o Claude Code no repositório e implemente o card nesse branch.",
        "",
        "--- Métricas ---",
        f"Tempo total       : {report.elapsed_seconds:.2f} s",
        f"Tokens de entrada : {usage.total_input}",
        f"Tokens de saída   : {usage.output_tokens}",
        f"Tokens totais     : {usage.total}",
    ]
    return "\n".join(lines)


def _usage_to_dict(usage: TokenUsage | None) -> dict[str, int] | None:
    if usage is None:
        return None
    return {
        "input": usage.total_input,
        "output": usage.output_tokens,
        "total": usage.total,
        "input_uncached": usage.input_tokens,
        "cache_creation": usage.cache_creation_input_tokens,
        "cache_read": usage.cache_read_input_tokens,
    }


def report_to_dict(report: PipelineReport) -> dict[str, Any]:
    """Passo 4 em formato estruturado (JSON) para a interface web."""
    return {
        "card": report.input.card_number,
        "repo": str(report.input.repo_path),
        "request": report.input.request,
        "classification": report.classification.classification if report.classification else None,
        "branch": {"name": report.branch.name, "created": report.branch.created} if report.branch else None,
        "elapsed_seconds": round(report.elapsed_seconds, 2),
        "tokens": {"total": _usage_to_dict(report.total_usage)},
        "text": format_report(report),
    }
