"""Anota o documento do card na tarefa do Redmine (API REST: PUT /issues/<id>.json)."""

from __future__ import annotations

import logging
import os

import httpx

from core.exceptions import RedmineError
from core.settings import RedmineSettings

logger = logging.getLogger(__name__)


class RedmineClient:
    def __init__(self, settings: RedmineSettings, transport: httpx.BaseTransport | None = None) -> None:
        self.settings = settings
        self.transport = transport  # injetável nos testes

    def _credentials(self) -> tuple[str, str]:
        url = (os.environ.get("REDMINE_URL") or self.settings.url or "").strip().rstrip("/")
        key = os.environ.get("REDMINE_API_KEY", "").strip()
        missing = [name for name, value in (("REDMINE_URL", url), ("REDMINE_API_KEY", key)) if not value]
        if missing:
            raise RedmineError(f"Redmine não configurado: defina {' e '.join(missing)} no .env.")
        return url, key

    def add_note(self, issue_id: str, notes: str) -> str:
        """Adiciona `notes` ao histórico da tarefa. Retorna a URL da tarefa."""
        url, key = self._credentials()
        issue_url = f"{url}/issues/{issue_id}"
        try:
            with httpx.Client(
                timeout=self.settings.timeout_seconds, verify=self.settings.verify_ssl, transport=self.transport
            ) as client:
                response = client.put(
                    f"{issue_url}.json",
                    json={"issue": {"notes": notes}},
                    headers={"X-Redmine-API-Key": key},
                )
        except httpx.TimeoutException as exc:
            raise RedmineError(f"Timeout ao falar com o Redmine ({url}).") from exc
        except httpx.HTTPError as exc:
            raise RedmineError(f"Falha de conexão com o Redmine ({url}): {exc}") from exc

        if response.status_code in (200, 204):
            logger.info("Nota adicionada na tarefa %s do Redmine", issue_id)
            return issue_url
        if response.status_code == 404:
            raise RedmineError(f"Tarefa {issue_id} não encontrada no Redmine (ou sem acesso a ela).")
        if response.status_code in (401, 403):
            raise RedmineError(
                "Redmine recusou a chave de API (REDMINE_API_KEY) ou o usuário não pode editar a tarefa "
                f"(HTTP {response.status_code}). Confira também se a API REST está habilitada no Redmine."
            )
        if response.status_code == 422:
            try:
                errors = "; ".join(response.json().get("errors", []))
            except ValueError:
                errors = response.text[:300]
            raise RedmineError(f"Redmine rejeitou a nota: {errors}")
        raise RedmineError(f"Erro do Redmine (HTTP {response.status_code}): {response.text[:300]}")
