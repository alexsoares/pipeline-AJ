"""Abre o merge request do card no GitLab (Ginner) pela API v4."""

from __future__ import annotations

import logging
import os
import re
from urllib.parse import quote, urlsplit

import httpx

from core.exceptions import GitLabError
from core.settings import GitLabSettings

logger = logging.getLogger(__name__)

# git@host:grupo/projeto.git | ssh://git@host[:porta]/grupo/projeto.git | https://host/grupo/projeto.git
_SCP_LIKE = re.compile(r"^(?:[^@/]+@)?(?P<host>[^:/]+):(?P<path>[^/].*)$")


def _credentials(settings: GitLabSettings) -> tuple[str, str]:
    url = (os.environ.get("GITLAB_URL") or settings.url or "").strip().rstrip("/")
    return url, os.environ.get("GITLAB_TOKEN", "").strip()


def gitlab_configured(settings: GitLabSettings) -> bool:
    return all(_credentials(settings))


def parse_remote(remote_url: str) -> tuple[str, str]:
    """(host, caminho do projeto) a partir da URL do remoto Git."""
    url = remote_url.strip()
    if "://" in url:
        parts = urlsplit(url)
        host, path = parts.hostname or "", parts.path
    else:
        match = _SCP_LIKE.match(url)
        if not match:
            raise GitLabError(f"Não foi possível entender a URL do remoto: {remote_url}")
        host, path = match.group("host"), match.group("path")
    path = path.strip("/").removesuffix(".git")
    if not host or not path:
        raise GitLabError(f"Não foi possível entender a URL do remoto: {remote_url}")
    return host.lower(), path


class GitLabClient:
    def __init__(self, settings: GitLabSettings, transport: httpx.BaseTransport | None = None) -> None:
        self.settings = settings
        self.transport = transport  # injetável nos testes

    def _credentials(self) -> tuple[str, str]:
        url, token = _credentials(self.settings)
        missing = [name for name, value in (("GITLAB_URL", url), ("GITLAB_TOKEN", token)) if not value]
        if missing:
            raise GitLabError(f"GitLab não configurado: defina {' e '.join(missing)} no .env.")
        return url, token

    def project_for_remote(self, remote_url: str) -> str:
        """Caminho do projeto no GitLab configurado; recusa remotos de outro servidor (ex.: GitHub)."""
        url, _ = self._credentials()
        host, path = parse_remote(remote_url)
        gitlab_host = (urlsplit(url).hostname or "").lower()
        if host != gitlab_host:
            raise GitLabError(
                f"O remoto aponta para '{host}', não para o GitLab configurado ('{gitlab_host}'): "
                "o merge request só pode ser aberto no GitLab do próprio repositório."
            )
        return path

    def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        url, token = self._credentials()
        try:
            with httpx.Client(
                timeout=self.settings.timeout_seconds, verify=self.settings.verify_ssl, transport=self.transport
            ) as client:
                return client.request(method, f"{url}/api/v4{path}", headers={"PRIVATE-TOKEN": token}, **kwargs)
        except httpx.TimeoutException as exc:
            raise GitLabError(f"Timeout ao falar com o GitLab ({url}).") from exc
        except httpx.HTTPError as exc:
            raise GitLabError(f"Falha de conexão com o GitLab ({url}): {exc}") from exc

    @staticmethod
    def _error_message(response: httpx.Response) -> str:
        try:
            body = response.json()
        except ValueError:
            return response.text[:300]
        message = body.get("message") or body.get("error") or body
        if isinstance(message, list):
            return "; ".join(map(str, message))
        return str(message)

    def create_merge_request(
        self, project: str, source: str, target: str, title: str, description: str
    ) -> str:
        """Abre o MR (ou reaproveita o aberto para o mesmo branch). Retorna a URL do MR."""
        project_path = f"/projects/{quote(project, safe='')}/merge_requests"
        response = self._request("POST", project_path, json={
            "source_branch": source,
            "target_branch": target,
            "title": title,
            "description": description,
            "remove_source_branch": self.settings.remove_source_branch,
        })
        if response.status_code == 201:
            web_url = response.json()["web_url"]
            logger.info("Merge request aberto: %s", web_url)
            return web_url
        if response.status_code == 409:
            existing = self._request(
                "GET", project_path, params={"state": "opened", "source_branch": source, "target_branch": target}
            )
            if existing.status_code == 200 and existing.json():
                web_url = existing.json()[0]["web_url"]
                logger.info("Merge request já existia: %s", web_url)
                return web_url
        if response.status_code in (401, 403):
            raise GitLabError(
                f"GitLab recusou o token (GITLAB_TOKEN) ou ele não tem permissão no projeto (HTTP {response.status_code})."
            )
        if response.status_code == 404:
            raise GitLabError(f"Projeto '{project}' não encontrado no GitLab (ou sem acesso a ele).")
        raise GitLabError(
            f"GitLab não abriu o merge request (HTTP {response.status_code}): {self._error_message(response)}"
        )
