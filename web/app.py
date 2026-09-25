"""Interface web local da pipeline do Agente AJ.

Uso:
    python -m web.app              # http://127.0.0.1:8000
    python -m web.app --port 9000

O servidor executa Git, a API da Anthropic (classificador) e, se o usuário pedir, o Claude Code a partir da
máquina local, por isso escuta apenas em 127.0.0.1 por padrão. Só uma execução roda por vez em cada repositório: duas
pipelines trocando de branch ao mesmo tempo no mesmo repositório se atrapalhariam. Repositórios diferentes rodam em
paralelo.
"""

from __future__ import annotations

import argparse
import logging
import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from flask import Flask, jsonify, request, send_from_directory

from core.conclusion import ConclusionOptions, ConclusionReport, conclude, conclusion_to_dict
from core.history import History
from core.exceptions import GitError, MissingInputError, PipelineCancelled, PipelineError
from core.git_manager import GitManager
from core.logging_config import setup_logging
from core.models import PipelineInput
from core.pipeline import MISSING_REQUEST, STEPS, Pipeline, StepFailed, report_to_dict
from core.settings import Settings, load_settings
from core.validator import validate_input
from integration.gitlab import gitlab_configured
from integration.redmine import redmine_configured

logger = logging.getLogger(__name__)
STATIC_DIR = Path(__file__).resolve().parent / "static"


PROGRESS_KEPT = 200


class PipelineBusyError(Exception):
    """Já existe uma execução em andamento no mesmo repositório."""


@dataclass
class Job:
    id: str
    card: str
    request: str
    repo: str = ""
    implement: bool = False
    status: str = "running"  # running | succeeded | failed | cancelled
    cancel_requested: bool = False
    steps: dict[int, str] = field(default_factory=dict)
    progress: list[str] = field(default_factory=list)  # frases do Claude Code, as mais recentes no fim
    failed_step: int | None = None
    error: str | None = None
    result: dict[str, Any] | None = None
    pipeline: Pipeline | None = field(default=None, repr=False)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "card": self.card,
            "request": self.request,
            "repo": self.repo,
            "implement": self.implement,
            "status": self.status,
            "cancel_requested": self.cancel_requested,
            "steps": {str(n): state for n, state in self.steps.items()},
            "progress": self.progress[-PROGRESS_KEPT:],
            "failed_step": self.failed_step,
            "error": self.error,
            "result": self.result,
        }


class JobManager:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.history = History.from_settings(settings.history)
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        self._active: dict[Path, str] = {}  # raiz do repositório -> id da execução

    def _repo_key(self, path: Path) -> Path:
        # Subdiretórios do mesmo repositório compartilham o bloqueio. Se não for um repo, o passo 1 é que vai reclamar.
        try:
            return GitManager(path, self.settings.git.command_timeout_seconds).toplevel()
        except GitError:
            return path

    def start(self, data: PipelineInput, implement: bool = False) -> Job:
        key = self._repo_key(data.repo_path)
        with self._lock:
            if key in self._active:
                raise PipelineBusyError(self._active[key])
            job = Job(
                id=uuid.uuid4().hex[:12],
                card=data.card_number,
                request=data.request,
                repo=str(key),
                implement=implement,
            )

            def on_step(number: int, state: str) -> None:
                job.steps[number] = state

            def on_progress(message: str) -> None:
                job.progress.append(message)
                del job.progress[:-PROGRESS_KEPT]

            job.pipeline = Pipeline(self.settings, on_step=on_step, on_progress=on_progress, history=self.history)
            self._jobs[job.id] = job
            self._active[key] = job.id

        threading.Thread(target=self._run, args=(job, data, key), daemon=True, name=f"pipeline-{job.id}").start()
        return job

    def get(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)

    def conclude(
        self, data: PipelineInput, options: ConclusionOptions, claude_output: str = ""
    ) -> ConclusionReport:
        """Conclui o card na hora (é rápido), ocupando o repositório para nenhuma execução trocar de branch no meio."""
        key = self._repo_key(data.repo_path)
        with self._lock:
            if key in self._active:
                raise PipelineBusyError(self._active[key])
            self._active[key] = "conclusão"
        try:
            return conclude(
                self.settings, data.repo_path, data.card_number, data.request, options,
                claude_output=claude_output, history=self.history,
            )
        finally:
            with self._lock:
                self._active.pop(key, None)

    def cancel(self, job: Job) -> None:
        job.cancel_requested = True
        if job.pipeline:
            job.pipeline.cancel()

    def _run(self, job: Job, data: PipelineInput, key: Path) -> None:
        try:
            report = job.pipeline.run(data, implement=job.implement)
            job.result = report_to_dict(report)
            job.status = "succeeded"
        except PipelineCancelled as exc:
            job.error = str(exc)
            job.status = "cancelled"
        except StepFailed as exc:
            job.failed_step = exc.step
            job.error = str(exc)
            job.status = "failed"
        except PipelineError as exc:
            job.error = str(exc)
            job.status = "failed"
        except Exception:  # noqa: BLE001 - erro inesperado vai para o log, não para a tela
            logger.exception("Erro inesperado na execução %s", job.id)
            job.error = "Erro inesperado na pipeline. Detalhes no log."
            job.status = "failed"
        finally:
            job.pipeline = None
            with self._lock:
                self._active.pop(key, None)


def _conclusion_options(body: dict[str, Any]) -> ConclusionOptions:
    """Opções da conclusão vindas do JSON; só `true` liga uma opção."""
    hours = body.get("hours")
    if hours in (None, "", 0):
        hours = None
    else:
        try:
            hours = float(hours)
        except (TypeError, ValueError) as exc:
            raise ValueError("Horas inválidas: informe um número (ex.: 1.5).") from exc
        if hours <= 0:
            raise ValueError("Horas devem ser maiores que zero.")
    status = str(body.get("status") or "").strip() or None
    return ConclusionOptions(
        commit=body.get("commit") is True,
        merge_request=body.get("merge_request") is True,
        redmine_note=body.get("redmine") is True,
        redmine_status=status,
        hours=hours,
        activity=str(body.get("activity") or "").strip() or None,
    )


def create_app(settings: Settings | None = None) -> Flask:
    settings = settings or load_settings()
    jobs = JobManager(settings)
    app = Flask(__name__, static_folder=None)

    @app.get("/")
    def index():
        return send_from_directory(STATIC_DIR, "index.html")

    @app.get("/api/config")
    def config():
        return jsonify({
            "redmine": redmine_configured(settings.redmine),
            "gitlab": gitlab_configured(settings.gitlab),
            "merge_request_default": settings.gitlab.merge_request_default,
            "conclusion_status": settings.redmine.conclusion_status,
            "time_entry_activity": settings.redmine.time_entry_activity,
        })

    @app.get("/api/history")
    def history():
        if not jobs.history:
            return jsonify({"enabled": False, "items": [], "summary": []})
        limit = min(max(request.args.get("limit", default=50, type=int) or 50, 1), 500)
        card = (request.args.get("card") or "").strip().lstrip("#") or None
        return jsonify({
            "enabled": True,
            "items": jobs.history.recent(limit, card=card),
            "summary": [] if card else jobs.history.monthly_summary(),
        })

    @app.get("/api/steps")
    def steps():
        return jsonify([{"number": n, "name": name} for n, name in STEPS.items()])

    @app.post("/api/runs")
    def create_run():
        body = request.get_json(silent=True) or {}
        try:
            data = validate_input(
                str(body.get("message", "")),
                repo=str(body.get("repo") or "") or None,
                card=str(body.get("card") or "") or None,
                require_request=False,
            )
        except MissingInputError as exc:
            return jsonify({"error": str(exc)}), 400
        if not data.request and not redmine_configured(settings.redmine):
            return jsonify({"error": MISSING_REQUEST}), 400

        try:
            job = jobs.start(data, implement=body.get("implement") is True)
        except PipelineBusyError:
            return jsonify({"error": "Já existe uma execução em andamento neste repositório. Aguarde ou cancele."}), 409
        return jsonify(job.to_dict()), 202

    @app.post("/api/conclusions")
    def create_conclusion():
        body = request.get_json(silent=True) or {}
        try:
            data = validate_input(
                str(body.get("message", "")),
                repo=str(body.get("repo") or "") or None,
                card=str(body.get("card") or "") or None,
                require_request=False,
            )
        except MissingInputError as exc:
            return jsonify({"error": str(exc)}), 400

        try:
            options = _conclusion_options(body)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400

        # Se vier de uma execução com implementação, o resumo do Claude Code entra no documento do card.
        job = jobs.get(str(body.get("job_id") or ""))
        claude = (job.result or {}).get("claude") if job else None
        try:
            report = jobs.conclude(data, options, claude_output=(claude or {}).get("output", ""))
        except PipelineBusyError:
            return jsonify({"error": "Há uma execução em andamento neste repositório. Aguarde ou cancele."}), 409
        except PipelineError as exc:
            return jsonify({"error": str(exc)}), 422
        except Exception:  # noqa: BLE001 - erro inesperado vai para o log, não para a tela
            logger.exception("Erro inesperado na conclusão do card %s", data.card_number)
            return jsonify({"error": "Erro inesperado na conclusão. Detalhes no log."}), 500
        return jsonify(conclusion_to_dict(report))

    @app.post("/api/runs/<job_id>/cancel")
    def cancel_run(job_id: str):
        job = jobs.get(job_id)
        if not job:
            return jsonify({"error": "Execução não encontrada."}), 404
        if job.status != "running":
            return jsonify({"error": "A execução já terminou."}), 409
        jobs.cancel(job)
        return jsonify(job.to_dict()), 202

    @app.get("/api/runs/<job_id>")
    def get_run(job_id: str):
        job = jobs.get(job_id)
        if not job:
            return jsonify({"error": "Execução não encontrada."}), 404
        return jsonify(job.to_dict())

    return app


def main() -> None:
    parser = argparse.ArgumentParser(description="Interface web do Agente AJ")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    settings = load_settings()
    setup_logging(settings.logging)
    if args.host not in ("127.0.0.1", "localhost", "::1"):
        logger.warning(
            "Servidor exposto em %s: qualquer pessoa na rede poderá executar a pipeline (Git) nesta máquina.", args.host
        )
    create_app(settings).run(host=args.host, port=args.port, threaded=True)


if __name__ == "__main__":
    main()
