"""Histórico das execuções e conclusões em SQLite (tokens, custo e resultado por card).

Gravar o histórico nunca pode derrubar a pipeline: qualquer falha aqui vai só para o log.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from core.settings import PROJECT_ROOT, HistorySettings

if TYPE_CHECKING:
    from core.conclusion import ConclusionReport
    from core.models import PipelineReport

logger = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS executions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,                -- pipeline | conclusao
    started_at TEXT NOT NULL,
    finished_at TEXT NOT NULL,
    card TEXT NOT NULL,
    repo TEXT NOT NULL,
    status TEXT NOT NULL,              -- succeeded | failed | cancelled | partial (conclusão com falhas)
    classification TEXT,
    classification_source TEXT,
    branch TEXT,
    implement INTEGER NOT NULL DEFAULT 0,
    elapsed_seconds REAL,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    cost_usd REAL,
    error TEXT,
    details TEXT                       -- JSON com o que é específico do tipo
);
CREATE INDEX IF NOT EXISTS executions_card ON executions (card);
CREATE INDEX IF NOT EXISTS executions_started ON executions (started_at);
"""


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


class History:
    def __init__(self, path: Path) -> None:
        self.path = path

    @classmethod
    def from_settings(cls, settings: HistorySettings) -> History | None:
        if not settings.enabled:
            return None
        path = Path(settings.path)
        return cls(path if path.is_absolute() else PROJECT_ROOT / path)

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Uma conexão por operação: a web grava de várias threads.
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.executescript(SCHEMA)
        return conn

    def _insert(self, row: dict[str, Any]) -> None:
        try:
            with closing(self._connect()) as conn, conn:
                columns = ", ".join(row)
                conn.execute(f"INSERT INTO executions ({columns}) VALUES ({', '.join('?' * len(row))})",
                             tuple(row.values()))
        except (sqlite3.Error, OSError):
            logger.exception("Não foi possível gravar o histórico em %s", self.path)

    # --- Gravação -----------------------------------------------------------------------------

    def record_run(
        self, report: PipelineReport, started_at: str, status: str, error: str | None = None
    ) -> None:
        usage = report.total_usage
        claude = report.claude
        self._insert({
            "kind": "pipeline",
            "started_at": started_at,
            "finished_at": _now(),
            "card": report.input.card_number,
            "repo": str(report.input.repo_path),
            "status": status,
            "classification": report.classification.classification if report.classification else None,
            "classification_source": report.classification.source if report.classification else None,
            "branch": report.branch.name if report.branch else None,
            "implement": int(report.implement),
            "elapsed_seconds": round(report.elapsed_seconds, 2),
            "input_tokens": usage.total_input,
            "output_tokens": usage.output_tokens,
            "cost_usd": claude.cost_usd if claude else None,
            "error": error,
            "details": json.dumps({
                "issue": report.issue.subject if report.issue else None,
                "changed_files": report.changed_files,
            }, ensure_ascii=False),
        })

    def record_conclusion(
        self,
        card: str,
        repo: Path,
        started_at: str,
        report: ConclusionReport | None,
        error: str | None = None,
    ) -> None:
        if report is None:
            status = "failed"
        else:
            status = "partial" if report.errors else "succeeded"
            error = "; ".join(report.errors) or None
        self._insert({
            "kind": "conclusao",
            "started_at": started_at,
            "finished_at": _now(),
            "card": card,
            "repo": str(repo),
            "status": status,
            "classification": report.classification if report else None,
            "branch": report.branch if report else None,
            "error": error,
            "details": json.dumps({
                "commit": report.commit,
                "merge_request": report.merge_request_url,
                "redmine_note": report.redmine_url,
                "status": report.status_applied,
                "hours": report.hours_logged,
            } if report else {}, ensure_ascii=False),
        })

    # --- Consulta -----------------------------------------------------------------------------

    def recent(self, limit: int = 50, card: str | None = None) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        query = "SELECT * FROM executions"
        params: tuple[Any, ...] = ()
        if card:
            query += " WHERE card = ?"
            params = (card,)
        query += " ORDER BY id DESC LIMIT ?"
        with closing(self._connect()) as conn:
            rows = conn.execute(query, (*params, limit)).fetchall()
        return [{**dict(row), "details": json.loads(row["details"] or "{}")} for row in rows]

    def monthly_summary(self, months: int = 6) -> list[dict[str, Any]]:
        """Totais por mês (execuções da pipeline): quantidade, tokens e custo do Claude Code."""
        if not self.path.exists():
            return []
        with closing(self._connect()) as conn:
            rows = conn.execute(
                """
                SELECT substr(started_at, 1, 7) AS month,
                       COUNT(*) AS runs,
                       SUM(status = 'succeeded') AS succeeded,
                       SUM(implement) AS implemented,
                       SUM(input_tokens) AS input_tokens,
                       SUM(output_tokens) AS output_tokens,
                       ROUND(COALESCE(SUM(cost_usd), 0), 4) AS cost_usd
                FROM executions
                WHERE kind = 'pipeline'
                GROUP BY month
                ORDER BY month DESC
                LIMIT ?
                """,
                (months,),
            ).fetchall()
        return [dict(row) for row in rows]


def format_history(items: list[dict[str, Any]], summary: list[dict[str, Any]]) -> str:
    """Texto do histórico para a CLI."""
    if not items:
        return "Nenhuma execução registrada no histórico."
    lines = ["==================== AGENTE AJ | Histórico ====================",
             f"{'Início':19}  {'Tipo':9}  {'Card':>7}  {'Status':9}  {'Classif.':8}  {'Tokens':>8}  {'Custo':>8}"]
    for item in items:
        tokens = item["input_tokens"] + item["output_tokens"]
        cost = f"{item['cost_usd']:.4f}" if item["cost_usd"] is not None else "-"
        lines.append(
            f"{item['started_at']:19}  {item['kind']:9}  {item['card']:>7}  {item['status']:9}  "
            f"{item['classification'] or '-':8}  {tokens:>8}  {cost:>8}"
        )
    if summary:
        lines += ["", "--- Totais por mês (pipeline) ---",
                  f"{'Mês':7}  {'Execuções':>9}  {'Sucesso':>7}  {'Com CC':>6}  {'Tokens':>10}  {'Custo US$':>9}"]
        for month in summary:
            tokens = (month["input_tokens"] or 0) + (month["output_tokens"] or 0)
            lines.append(
                f"{month['month']:7}  {month['runs']:>9}  {month['succeeded']:>7}  {month['implemented']:>6}  "
                f"{tokens:>10}  {month['cost_usd']:>9.4f}"
            )
    return "\n".join(lines)
