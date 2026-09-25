"""Conclusão do card: retorno à pipeline depois da implementação (manual ou pelo Claude Code).

Confere que o repositório está no branch do card, levanta o que mudou desde a base do branch e gera o
CARD-<n>.md. Depois, conforme as opções escolhidas pelo usuário: commit padronizado, merge request no GitLab,
nota com o mesmo texto na tarefa <n> do Redmine, mudança de status e lançamento de horas.

Documento e commit são obrigatórios para o que vem depois; merge request e Redmine são "melhor esforço":
uma falha neles vai para `ConclusionReport.errors` sem desfazer o que já foi feito.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from core.documentation import CardDocument, write_card_document
from core.exceptions import GitError, GitLabError, RedmineError
from core.git_manager import GitManager
from core.history import History
from core.models import VALID_CLASSIFICATIONS, RedmineIssue, compose_request
from core.settings import Settings
from integration.gitlab import GitLabClient, gitlab_configured
from integration.redmine import RedmineClient, redmine_configured

logger = logging.getLogger(__name__)

COMMIT_SUBJECT_MAX = 72


@dataclass(frozen=True)
class ConclusionOptions:
    commit: bool = False
    merge_request: bool = False
    redmine_note: bool = False
    redmine_status: str | None = None  # ex.: "Homologar"
    hours: float | None = None
    activity: str | None = None  # None = redmine.time_entry_activity


@dataclass
class ConclusionReport:
    card: str
    repo_path: Path
    classification: str
    branch: str
    base: str
    options: ConclusionOptions = field(default_factory=ConclusionOptions)
    issue: RedmineIssue | None = None
    changed_files: list[str] = field(default_factory=list)
    document_path: Path | None = None
    commit: str | None = None
    merge_request_url: str | None = None
    redmine_url: str | None = None
    status_applied: str | None = None
    hours_logged: str | None = None  # ex.: "2.5 h (Codificação)"
    errors: list[str] = field(default_factory=list)


def base_candidates(settings: Settings, classification: str) -> list[str]:
    """Bases possíveis do branch do card, da mais provável para a menos."""
    git_settings = settings.git
    configured = git_settings.base_branches.get(classification)
    names = ([configured] if configured else []) + list(git_settings.protected_branches)
    candidates: list[str] = []
    for name in names:
        for ref in (f"{git_settings.remote}/{name}", name):
            if ref not in candidates:
                candidates.append(ref)
    return candidates


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


def _open_merge_request(
    settings: Settings, git: GitManager, gitlab: GitLabClient, report: ConclusionReport, base_commit: str,
    title: str, description: str,
) -> str:
    if not gitlab_configured(settings.gitlab):
        raise GitLabError("GitLab não configurado: defina GITLAB_URL e GITLAB_TOKEN no .env.")
    pending = git.changed_files()
    if pending:
        raise GitLabError(
            f"há {len(pending)} alteração(ões) não commitada(s); marque o commit para incluí-las no merge request."
        )
    if git.commits_ahead(base_commit) == 0:
        raise GitLabError(f"o branch não tem commits além de '{report.base}'; não há o que revisar.")

    remote = settings.git.remote
    project = gitlab.project_for_remote(git.remote_url(remote))
    git.push(remote, report.branch, settings.git.network_timeout_seconds)
    target = report.base.removeprefix(f"{remote}/")
    return gitlab.create_merge_request(project, report.branch, target, title, description)


def conclude(
    settings: Settings,
    repo_path: Path,
    card: str,
    request: str = "",
    options: ConclusionOptions | None = None,
    *,
    claude_output: str = "",
    redmine_client: RedmineClient | None = None,
    gitlab_client: GitLabClient | None = None,
    history: History | None = None,
) -> ConclusionReport:
    started_at = datetime.now().isoformat(timespec="seconds")
    report: ConclusionReport | None = None
    try:
        report = _conclude(
            settings, repo_path, card, request, options or ConclusionOptions(),
            claude_output, redmine_client, gitlab_client,
        )
        return report
    except Exception as exc:
        if history:
            history.record_conclusion(card, repo_path, started_at, None, str(exc))
        raise
    finally:
        if history and report:
            history.record_conclusion(card, repo_path, started_at, report)


def _conclude(
    settings: Settings,
    repo_path: Path,
    card: str,
    request: str,
    options: ConclusionOptions,
    claude_output: str,
    redmine_client: RedmineClient | None,
    gitlab_client: GitLabClient | None,
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

    redmine = redmine_client or RedmineClient(settings.redmine)
    issue = _fetch_issue(redmine, card) if redmine_configured(settings.redmine) else None

    base_name, base_commit = git.base_commit(base_candidates(settings, classification))
    doc_name = settings.documentation.filename_template.format(card=card)
    # O próprio documento do card não entra na lista de alterações dele.
    changed = [line for line in git.changes_since(base_commit) if not line.endswith(f" {doc_name}")]

    report = ConclusionReport(
        card=card, repo_path=repo_path, classification=classification, branch=branch, base=base_name,
        options=options, issue=issue, changed_files=changed,
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
    document = report.document_path.read_text(encoding="utf-8")
    # O título da tarefa é o melhor assunto de commit/MR; sem ele, a 1ª linha do que o usuário digitou.
    title = commit_message(classification, card, issue.subject if issue else request)

    if options.commit:
        report.commit = git.commit_all(title)
        logger.info("Commit do card %s: %s", card, report.commit or "nada a commitar")

    if options.merge_request:
        description = document + (f"\n\n**Tarefa no Redmine:** {issue.url}\n" if issue else "")
        try:
            report.merge_request_url = _open_merge_request(
                settings, git, gitlab_client or GitLabClient(settings.gitlab), report, base_commit, title, description
            )
        except (GitError, GitLabError) as exc:
            logger.error("Falha no merge request do card %s: %s", card, exc)
            report.errors.append(f"Merge request: {exc}")

    if options.redmine_note or options.redmine_status:
        notes = None
        if options.redmine_note:
            notes = document + (f"\n\n**Merge request:** {report.merge_request_url}\n" if report.merge_request_url else "")
        try:
            report.redmine_url, current = redmine.update_issue(card, notes=notes, status=options.redmine_status)
            if options.redmine_status:
                if current and current.casefold() == options.redmine_status.strip().casefold():
                    report.status_applied = current
                else:
                    report.errors.append(
                        f"Redmine: o status '{options.redmine_status}' não foi aplicado (a tarefa continua em "
                        f"'{current}'); o fluxo de trabalho não permite essa transição para o seu usuário."
                    )
        except RedmineError as exc:
            logger.error("Falha ao atualizar a tarefa %s no Redmine: %s", card, exc)
            report.errors.append(f"Redmine: {exc}")

    if options.hours:
        activity = options.activity or settings.redmine.time_entry_activity
        try:
            redmine.log_time(card, options.hours, activity, comments=title)
            report.hours_logged = f"{options.hours:g} h ({activity})"
        except RedmineError as exc:
            logger.error("Falha ao lançar horas na tarefa %s: %s", card, exc)
            report.errors.append(f"Horas no Redmine: {exc}")
    return report


def _outcome(requested: bool, value: str | None, done_label: str = "") -> str:
    if not requested:
        return "não solicitado"
    return f"{done_label}{value}" if value else "FALHOU (veja abaixo)"


def format_conclusion(report: ConclusionReport) -> str:
    options = report.options
    files = [f"  {line}" for line in report.changed_files] or ["  (nenhum arquivo alterado)"]
    commit = "não solicitado" if not options.commit else (report.commit or "nada a commitar")
    lines = [
        f"=============== AGENTE AJ | Conclusão do card {report.card} ===============",
        f"Repositório   : {report.repo_path}",
    ]
    if report.issue:
        lines.append(f"Tarefa        : #{report.issue.id} {report.issue.subject} ({report.issue.url})")
    lines += [
        f"Classificação : {report.classification}",
        f"Branch        : {report.branch} (base: {report.base})",
        f"Documento     : {report.document_path}",
        f"Commit        : {commit}",
        f"Merge request : {_outcome(options.merge_request, report.merge_request_url)}",
        f"Nota Redmine  : {_outcome(options.redmine_note, report.redmine_url, 'adicionada em ')}",
        f"Status Redmine: {_outcome(bool(options.redmine_status), report.status_applied)}",
        f"Horas Redmine : {_outcome(bool(options.hours), report.hours_logged)}",
        "",
        f"--- Alterações desde {report.base} ---",
        *files,
    ]
    if report.errors:
        lines += ["", "--- Falhas (documento e commit foram mantidos) ---", *(f"  {e}" for e in report.errors)]
    return "\n".join(lines)


def conclusion_to_dict(report: ConclusionReport) -> dict[str, Any]:
    options = report.options
    return {
        "card": report.card,
        "repo": str(report.repo_path),
        "issue": {"id": report.issue.id, "subject": report.issue.subject, "url": report.issue.url}
        if report.issue else None,
        "classification": report.classification,
        "branch": report.branch,
        "base": report.base,
        "changed_files": report.changed_files,
        "document_path": str(report.document_path) if report.document_path else None,
        "requested": {
            "commit": options.commit,
            "merge_request": options.merge_request,
            "redmine_note": options.redmine_note,
            "redmine_status": options.redmine_status,
            "hours": options.hours,
        },
        "commit": report.commit,
        "merge_request_url": report.merge_request_url,
        "redmine_url": report.redmine_url,
        "status_applied": report.status_applied,
        "hours_logged": report.hours_logged,
        "errors": report.errors,
        "text": format_conclusion(report),
    }
