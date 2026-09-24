"""Orquestrador: executa os passos em sequência e monta o relatório final.

A implementação do card (passo 4) é opcional e decidida a cada execução: ou a pipeline
roda o Claude Code em modo headless no branch preparado, ou apenas orienta o usuário
a abri-lo depois.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from typing import Any, TypeVar

from core.classifier import RequestClassifier
from core.exceptions import GitError, PipelineCancelled, PipelineError
from core.git_manager import GitManager
from core.models import GitStatus, PipelineInput, PipelineReport, TokenUsage
from core.settings import Settings
from integration.claude_runner import ClaudeCodeRunner

logger = logging.getLogger(__name__)
T = TypeVar("T")

STEPS: dict[int, str] = {
    1: "status do Git",
    2: "classificação da solicitação",
    3: "preparação do branch",
    4: "implementação no Claude Code",
    5: "resposta consolidada",
}
IMPLEMENT_STEP = 4

# Recebe (número do passo, estado): "running", "done", "failed", "skipped" ou "cancelled".
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
        runner: ClaudeCodeRunner | None = None,
        on_step: StepCallback | None = None,
    ) -> None:
        self.settings = settings
        self.classifier = classifier or RequestClassifier(settings.classifier)
        self.runner = runner or ClaudeCodeRunner(settings.claude_code)
        self.on_step = on_step
        self._cancelled = threading.Event()

    def cancel(self) -> None:
        """Pede o cancelamento: interrompe o Claude Code, se estiver rodando, e impede os próximos passos."""
        self._cancelled.set()
        self.runner.cancel()

    def _notify(self, number: int, state: str) -> None:
        if not self.on_step:
            return
        try:
            self.on_step(number, state)
        except Exception:  # noqa: BLE001 - quem observa o progresso não pode derrubar a pipeline
            logger.exception("Falha no callback de progresso do passo %d", number)

    def _step(self, number: int, action: Callable[[], T]) -> T:
        if self._cancelled.is_set():
            self._notify(number, "cancelled")
            raise PipelineCancelled("Execução cancelada pelo usuário.")
        logger.info("Passo %d: %s", number, STEPS[number])
        self._notify(number, "running")
        try:
            result = action()
        except PipelineCancelled:
            logger.warning("Passo %d (%s) cancelado pelo usuário.", number, STEPS[number])
            self._notify(number, "cancelled")
            raise
        except PipelineError as exc:
            logger.error("Passo %d (%s) falhou: %s", number, STEPS[number], exc)
            self._notify(number, "failed")
            raise StepFailed(number, exc) from exc
        except Exception:
            self._notify(number, "failed")
            raise
        self._notify(number, "done")
        return result

    def run(self, data: PipelineInput, implement: bool = False) -> PipelineReport:
        started = time.perf_counter()
        report = PipelineReport(input=data, implement=implement)
        git = GitManager(data.repo_path, self.settings.git.command_timeout_seconds)

        def check_status() -> GitStatus:
            status = git.status()
            if status.dirty_files and implement:
                # Com o tree limpo, tudo o que o git status mostrar depois do passo 4 é do Claude Code.
                listed = "\n".join(f"  {line}" for line in status.dirty_files[:10])
                more = f"\n  … e mais {len(status.dirty_files) - 10}" if len(status.dirty_files) > 10 else ""
                raise GitError(
                    f"Há {len(status.dirty_files)} alteração(ões) pendente(s) no repositório:\n{listed}{more}\n"
                    "Faça commit ou stash antes de implementar com o Claude Code."
                )
            if status.dirty_files:
                logger.warning("Working tree com %d alteração(ões) pendente(s).", len(status.dirty_files))
            return status

        try:
            report.initial_status = self._step(1, check_status)

            report.classification = self._step(2, lambda: self.classifier.classify(data.request))

            branch_name = f"{report.classification.classification}/card-{data.card_number}"
            report.branch = self._step(
                3, lambda: git.ensure_work_branch(branch_name, self.settings.git.protected_branches)
            )

            if implement:
                classification = report.classification.classification
                report.claude = self._step(
                    IMPLEMENT_STEP,
                    lambda: self.runner.run(data.repo_path, data.card_number, classification, data.request),
                )
                report.changed_files = git.changed_files()
            else:
                self._notify(IMPLEMENT_STEP, "skipped")
            self._step(5, lambda: None)
        finally:
            report.elapsed_seconds = time.perf_counter() - started
        return report


def format_report(report: PipelineReport) -> str:
    """Passo 5: resposta consolidada para o chat."""
    usage = report.total_usage
    branch = report.branch
    branch_info = f"{branch.name} ({'criado' if branch.created else 'existente'})" if branch else "-"

    lines = [
        f"==================== AGENTE AJ | Card {report.input.card_number} ====================",
        f"Repositório   : {report.input.repo_path}",
        f"Classificação : {report.classification.classification if report.classification else '-'}",
        f"Branch        : {branch_info}",
        "",
    ]
    if report.claude:
        files = [f"  {line}" for line in report.changed_files] or ["  (nenhum arquivo alterado)"]
        lines += [
            "--- Resumo do Claude Code ---",
            report.claude.output or "(sem resumo)",
            "",
            "--- Arquivos alterados (git status) ---",
            *files,
            "",
            "--- Próximo passo ---",
            "Revise as alterações no branch e faça o commit.",
        ]
    else:
        lines += [
            "--- Próximo passo ---",
            "Abra o Claude Code no repositório e implemente o card nesse branch.",
        ]
    lines += [
        "",
        "--- Métricas ---",
        f"Tempo total       : {report.elapsed_seconds:.2f} s",
        f"Tokens de entrada : {usage.total_input}",
        f"Tokens de saída   : {usage.output_tokens}",
        f"Tokens totais     : {usage.total}",
    ]
    if report.claude and report.claude.cost_usd is not None:
        lines.append(f"Custo Claude Code : US$ {report.claude.cost_usd:.4f}")
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
    """Passo 5 em formato estruturado (JSON) para a interface web."""
    claude = report.claude
    return {
        "card": report.input.card_number,
        "repo": str(report.input.repo_path),
        "request": report.input.request,
        "classification": report.classification.classification if report.classification else None,
        "branch": {"name": report.branch.name, "created": report.branch.created} if report.branch else None,
        "implement": report.implement,
        "claude": {
            "output": claude.output,
            "duration_seconds": round(claude.duration_seconds, 2),
            "cost_usd": claude.cost_usd,
        } if claude else None,
        "changed_files": report.changed_files,
        "elapsed_seconds": round(report.elapsed_seconds, 2),
        "tokens": {
            "total": _usage_to_dict(report.total_usage),
            "classifier": _usage_to_dict(report.classification.usage if report.classification else None),
            "claude": _usage_to_dict(claude.usage if claude else None),
        },
        "text": format_report(report),
    }
