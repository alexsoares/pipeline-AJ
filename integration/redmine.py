"""Cliente da API REST do Redmine: lê a tarefa do card e anota o documento do card nela."""

from __future__ import annotations

import logging
import os
import httpx

from core.exceptions import RedmineError
from core.models import RedmineIssue
from core.settings import RedmineSettings

logger = logging.getLogger(__name__)


def _credentials(settings: RedmineSettings) -> tuple[str, str]:
    url = (os.environ.get("REDMINE_URL") or settings.url or "").strip().rstrip("/")
    return url, os.environ.get("REDMINE_API_KEY", "").strip()


def redmine_configured(settings: RedmineSettings) -> bool:
    return all(_credentials(settings))


class RedmineClient:
    def __init__(self, settings: RedmineSettings, transport: httpx.BaseTransport | None = None) -> None:
        self.settings = settings
        self.transport = transport  # injetável nos testes

    def _credentials(self) -> tuple[str, str]:
        url, key = _credentials(self.settings)
        missing = [name for name, value in (("REDMINE_URL", url), ("REDMINE_API_KEY", key)) if not value]
        if missing:
            raise RedmineError(f"Redmine não configurado: defina {' e '.join(missing)} no .env.")
        return url, key

    def _request(self, method: str, path: str, issue_id: str = "", **kwargs) -> tuple[str, httpx.Response]:
        """Chama `<url><path>`; retorna (URL da tarefa `issue_id`, resposta)."""
        url, key = self._credentials()
        try:
            with httpx.Client(
                timeout=self.settings.timeout_seconds, verify=self.settings.verify_ssl, transport=self.transport
            ) as client:
                response = client.request(method, f"{url}{path}", headers={"X-Redmine-API-Key": key}, **kwargs)
        except httpx.TimeoutException as exc:
            raise RedmineError(f"Timeout ao falar com o Redmine ({url}).") from exc
        except httpx.HTTPError as exc:
            raise RedmineError(f"Falha de conexão com o Redmine ({url}): {exc}") from exc

        if response.status_code in (200, 201, 204):
            return f"{url}/issues/{issue_id}", response
        if response.status_code == 404:
            raise RedmineError(f"Tarefa {issue_id} não encontrada no Redmine (ou sem acesso a ela).")
        if response.status_code in (401, 403):
            raise RedmineError(
                "Redmine recusou a chave de API (REDMINE_API_KEY) ou o usuário não tem permissão na tarefa "
                f"(HTTP {response.status_code}). Confira também se a API REST está habilitada no Redmine."
            )
        if response.status_code == 422:
            try:
                errors = "; ".join(response.json().get("errors", []))
            except ValueError:
                errors = response.text[:300]
            raise RedmineError(f"Redmine rejeitou a requisição: {errors}")
        raise RedmineError(f"Erro do Redmine (HTTP {response.status_code}): {response.text[:300]}")

    def get_issue(self, issue_id: str) -> RedmineIssue:
        issue_url, response = self._request("GET", f"/issues/{issue_id}.json", issue_id)
        try:
            issue = response.json()["issue"]
        except (ValueError, KeyError) as exc:
            raise RedmineError(f"Resposta inesperada do Redmine para a tarefa {issue_id}.") from exc

        wanted = {name.casefold(): name for name in self.settings.custom_fields}
        found = {
            (cf.get("name") or "").casefold(): str(cf.get("value") or "").strip()
            for cf in issue.get("custom_fields", [])
        }
        custom = {name: found[key] for key, name in wanted.items() if found.get(key)}

        logger.info("Tarefa %s lida do Redmine: %s", issue_id, issue.get("subject", ""))
        return RedmineIssue(
            id=str(issue.get("id", issue_id)),
            subject=str(issue.get("subject") or "").strip(),
            description=str(issue.get("description") or ""),
            tracker=(issue.get("tracker") or {}).get("name", ""),
            status=(issue.get("status") or {}).get("name", ""),
            project=(issue.get("project") or {}).get("name", ""),
            url=issue_url,
            custom_fields=custom,
        )

    def add_note(self, issue_id: str, notes: str) -> str:
        """Adiciona `notes` ao histórico da tarefa. Retorna a URL da tarefa."""
        return self.update_issue(issue_id, notes=notes)[0]

    def _named_id(self, path: str, key: str, name: str, what: str) -> int:
        _, response = self._request("GET", path)
        options = response.json().get(key, [])
        for option in options:
            if str(option.get("name", "")).strip().casefold() == name.strip().casefold():
                return int(option["id"])
        names = ", ".join(str(o.get("name")) for o in options)
        raise RedmineError(f"{what} '{name}' não existe no Redmine. Opções: {names}.")

    def update_issue(
        self, issue_id: str, notes: str | None = None, status: str | None = None
    ) -> tuple[str, str | None]:
        """Anota e/ou muda o status numa única atualização. Retorna (URL da tarefa, status atual se `status`).

        O Redmine ignora em silêncio transições que o fluxo de trabalho não permite, por isso o status é relido:
        quem chama compara com o pedido.
        """
        fields: dict[str, object] = {}
        if notes:
            fields["notes"] = notes
        if status:
            fields["status_id"] = self._named_id("/issue_statuses.json", "issue_statuses", status, "Status")
        issue_url, _ = self._request("PUT", f"/issues/{issue_id}.json", issue_id, json={"issue": fields})
        logger.info("Tarefa %s atualizada no Redmine (nota=%s, status=%s)", issue_id, bool(notes), status)
        return issue_url, self.get_issue(issue_id).status if status else None

    def log_time(self, issue_id: str, hours: float, activity: str, comments: str = "") -> None:
        activity_id = self._named_id(
            "/enumerations/time_entry_activities.json", "time_entry_activities", activity, "Atividade"
        )
        self._request("POST", "/time_entries.json", issue_id, json={"time_entry": {
            "issue_id": int(issue_id), "hours": hours, "activity_id": activity_id, "comments": comments[:255],
        }})
        logger.info("%.2f h lançadas na tarefa %s (%s)", hours, issue_id, activity)
