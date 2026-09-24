"""Interface web local da pipeline do Agente AJ.

Uso:
    python -m web.app              # http://127.0.0.1:8000
    python -m web.app --port 9000

O servidor executa Git e a API da Anthropic (classificador) a partir da máquina local, por isso escuta apenas
em 127.0.0.1 por padrão. Só uma execução roda por vez: duas pipelines trocando
de branch ao mesmo tempo no mesmo repositório se atrapalhariam.
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

from core.exceptions import MissingInputError, PipelineError
from core.logging_config import setup_logging
from core.models import PipelineInput
from core.pipeline import STEPS, Pipeline, StepFailed, report_to_dict
from core.settings import Settings, load_settings
from core.validator import validate_input

logger = logging.getLogger(__name__)
STATIC_DIR = Path(__file__).resolve().parent / "static"


class PipelineBusyError(Exception):
    """Já existe uma execução em andamento."""


@dataclass
class Job:
    id: str
    card: str
    request: str
    status: str = "running"  # running | succeeded | failed
    steps: dict[int, str] = field(default_factory=dict)
    failed_step: int | None = None
    error: str | None = None
    result: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "card": self.card,
            "request": self.request,
            "status": self.status,
            "steps": {str(n): state for n, state in self.steps.items()},
            "failed_step": self.failed_step,
            "error": self.error,
            "result": self.result,
        }


class JobManager:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        self._active: str | None = None

    def start(self, data: PipelineInput) -> Job:
        with self._lock:
            if self._active:
                raise PipelineBusyError(self._active)
            job = Job(id=uuid.uuid4().hex[:12], card=data.card_number, request=data.request)
            self._jobs[job.id] = job
            self._active = job.id

        threading.Thread(target=self._run, args=(job, data), daemon=True, name=f"pipeline-{job.id}").start()
        return job

    def get(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)

    def _run(self, job: Job, data: PipelineInput) -> None:
        def on_step(number: int, state: str) -> None:
            job.steps[number] = state

        try:
            report = Pipeline(self.settings, on_step=on_step).run(data)
            job.result = report_to_dict(report)
            job.status = "succeeded"
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
            with self._lock:
                self._active = None


def create_app(settings: Settings | None = None) -> Flask:
    settings = settings or load_settings()
    jobs = JobManager(settings)
    app = Flask(__name__, static_folder=None)

    @app.get("/")
    def index():
        return send_from_directory(STATIC_DIR, "index.html")

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
            )
        except MissingInputError as exc:
            return jsonify({"error": str(exc)}), 400

        try:
            job = jobs.start(data)
        except PipelineBusyError:
            return jsonify({"error": "Já existe uma execução em andamento. Aguarde ela terminar."}), 409
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
