"""Conclusão do card: retorno à pipeline depois da implementação (manual ou pelo Claude Code).

Confere que o repositório está no branch do card, levanta o que mudou desde a base do branch,
gera o CARD-<n>.md e, se pedido, faz o commit padronizado e anota o mesmo texto na tarefa <n> do Redmine.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from core.documentation import CardDocument, write_card_document
from core.exceptions import GitError, RedmineError
from core.git_manager import GitManager
from core.models import VALID_CLASSIFICATIONS, RedmineIssue, compose_request
from core.settings import Settings
from integration.redmine import RedmineClient, redmine_configured

logger = logging.getLogger(__name__)

COMMIT_SUBJECT_MAX = 72


@dataclass
class ConclusionReport:
    card: str
    repo_path: Path
    classification: str
    branch: str
    base: str
    changed_files: list[str] = field(default_factory=list)
    document_path: Path | None = None
    commit: str | None = None
    commit_requested: bool = False
    redmine_requested: bool = False
    redmine_url: str | None = None
    # Falha no Redmine não desfaz documento nem commit: fica registrada aqui para o usuário tentar de novo.
    redmine_error: str | None = None


def _fetch_issue(client: RedmineClient, card: str) -> RedmineIssue | None:
    try:
        return client.get_issue(card)
    except RedmineError as exc:
        # Sem a tarefa, a conclusão segue com o texto informado pelo usuário.
        logger.warning("Não foi possível ler a tarefa %s no Redmine: %s", card, exc)
        return None


def commit_message(classification: str, card: str, request: str) -> str:
    subject = (request.strip().splitlines() or [""])[0].strip() or "conclusão do card"
    prefix = f"{classification}(card-{card}): "
    if len(prefix) + len(subject) > COMMIT_SUBJECT_MAX:
        subject = subject[: COMMIT_SUBJECT_MAX - len(prefix) - 1].rstrip() + "…"
    return prefix + subject


def conclude(
    settings: Settings,
    repo_path: Path,
    card: str,
    request: str = "",
    *,
    commit: bool = False,
    claude_output: str = "",
    redmine: bool = False,
    redmine_client: RedmineClient | None = None,
) -> ConclusionReport:
    git = GitManager(repo_path, settings.git.command_timeout_seconds)
    git.ensure_repository()

    branch = git.current_branch()
    match = re.fullmatch(rf"({'|'.join(VALID_CLASSIFICATIONS)})/card-{re.escape(card)}", branch)
    if not match:
        raise GitError(
            f"O repositório está no branch '{branch}', não no branch do card {card} "
            f"(<{'|'.join(VALID_CLASSIFICATIONS)}>/card-{card}). Faça checkout dele antes de concluir."
        )
    classification = match.group(1)

    client = redmine_client or RedmineClient(settings.redmine)
    issue = _fetch_issue(client, card) if redmine_configured(settings.redmine) else None

    base_name, base_commit = git.base_commit(settings.git.protected_branches)
    doc_name = settings.documentation.filename_template.format(card=card)
    # O próprio documento do card não entra na lista de alterações dele.
    changed = [line for line in git.changes_since(base_commit) if not line.endswith(f" {doc_name}")]

    report = ConclusionReport(
        card=card,
        repo_path=repo_path,
        classification=classification,
        branch=branch,
        base=base_name,
        changed_files=changed,
        commit_requested=commit,
        redmine_requested=redmine,
    )
    report.document_path = write_card_document(
        repo_path,
        settings.documentation.filename_template,
        CardDocument(
            card=card,
            classification=classification,
            branch=branch,
            base=base_name,
            request=compose_request(issue, request),
            changed_files=changed,
            diff_stat=git.diff_stat(base_commit),
            claude_output=claude_output,
        ),
    )

    if commit:
        # O título da tarefa é o melhor assunto de commit; sem ele, a 1ª linha do que o usuário digitou.
        report.commit = git.commit_all(commit_message(classification, card, issue.subject if issue else request))
        logger.info("Commit do card %s: %s", card, report.commit or "nada a commitar")

    if redmine:
        try:
            report.redmine_url = client.add_note(card, report.document_path.read_text(encoding="utf-8"))
        except RedmineError as exc:
            logger.error("Falha ao anotar a tarefa %s no Redmine: %s", card, exc)
            report.redmine_error = str(exc)
    return report


def format_conclusion(report: ConclusionReport) -> str:
    files = [f"  {line}" for line in report.changed_files] or ["  (nenhum arquivo alterado)"]
    if not report.commit_requested:
        commit = "não solicitado"
    else:
        commit = report.commit or "nada a commitar"
    if not report.redmine_requested:
        redmine = "não solicitado"
    else:
        redmine = f"nota adicionada em {report.redmine_url}" if report.redmine_url else f"FALHOU: {report.redmine_error}"
    return "\n".join([
        f"=============== AGENTE AJ | Conclusão do card {report.card} ===============",
        f"Repositório   : {report.repo_path}",
        f"Classificação : {report.classification}",
        f"Branch        : {report.branch} (base: {report.base})",
        f"Documento     : {report.document_path}",
        f"Commit        : {commit}",
        f"Redmine       : {redmine}",
        "",
        f"--- Alterações desde {report.base} ---",
        *files,
    ])


def conclusion_to_dict(report: ConclusionReport) -> dict[str, Any]:
    return {
        "card": report.card,
        "repo": str(report.repo_path),
        "classification": report.classification,
        "branch": report.branch,
        "base": report.base,
        "changed_files": report.changed_files,
        "document_path": str(report.document_path) if report.document_path else None,
        "commit_requested": report.commit_requested,
        "commit": report.commit,
        "redmine_requested": report.redmine_requested,
        "redmine_url": report.redmine_url,
        "redmine_error": report.redmine_error,
        "text": format_conclusion(report),
    }
