"""Operações Git usadas pela pipeline (passos 1, 3 e levantamento de alterações)."""

from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path

from core.exceptions import GitError
from core.models import BranchResult, GitStatus

logger = logging.getLogger(__name__)


class GitManager:
    def __init__(self, repo_path: Path, timeout_seconds: int = 30) -> None:
        self.repo_path = repo_path
        self.timeout_seconds = timeout_seconds

    def _run(
        self, *args: str, check: bool = True, timeout: int | None = None, env: dict[str, str] | None = None
    ) -> subprocess.CompletedProcess[str]:
        cmd = ["git", *args]
        logger.debug("Executando: %s (cwd=%s)", " ".join(cmd), self.repo_path)
        try:
            result = subprocess.run(
                cmd,
                cwd=self.repo_path,
                capture_output=True,
                text=True,
                timeout=timeout or self.timeout_seconds,
                env={**os.environ, **env} if env else None,
            )
        except FileNotFoundError as exc:
            raise GitError("Git não encontrado no PATH.") from exc
        except subprocess.TimeoutExpired as exc:
            raise GitError(f"Timeout ao executar: {' '.join(cmd)}") from exc

        if check and result.returncode != 0:
            raise GitError(f"'{' '.join(cmd)}' falhou: {result.stderr.strip() or result.stdout.strip()}")
        return result

    # --- Passo 1 -----------------------------------------------------------

    def ensure_repository(self) -> None:
        result = self._run("rev-parse", "--is-inside-work-tree", check=False)
        if result.returncode != 0 or result.stdout.strip() != "true":
            raise GitError(f"O diretório não é um repositório Git: {self.repo_path}")

    def toplevel(self) -> Path:
        """Raiz do repositório (a mesma para qualquer subdiretório dele)."""
        return Path(self._run("rev-parse", "--show-toplevel").stdout.strip()).resolve()

    def current_branch(self) -> str:
        branch = self._run("rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
        if branch == "HEAD":
            raise GitError("O repositório está em 'detached HEAD'. Faça checkout de um branch antes de continuar.")
        return branch

    def changed_files(self) -> list[str]:
        """Arquivos modificados/novos/removidos no working tree (formato porcelain)."""
        output = self._run("status", "--porcelain", "--untracked-files=all").stdout
        return [line for line in output.splitlines() if line.strip()]

    def status(self) -> GitStatus:
        self.ensure_repository()
        return GitStatus(branch=self.current_branch(), dirty_files=self.changed_files())

    # --- Passo 3 -----------------------------------------------------------

    def branch_exists(self, name: str) -> bool:
        return self.ref_exists(f"refs/heads/{name}")

    def ref_exists(self, ref: str) -> bool:
        return self._run("rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}", check=False).returncode == 0

    def has_remote(self, remote: str) -> bool:
        return remote in self._run("remote").stdout.split()

    def fetch(self, remote: str, branch: str, timeout: int) -> None:
        self._run("fetch", "--quiet", remote, branch, timeout=timeout)

    def resolve_start_point(
        self, base: str, remote: str, fetch: bool, timeout: int
    ) -> tuple[str, str | None]:
        """Ponto de partida do branch do card: (<remote>/<base> ou <base>, aviso ou None)."""
        warning = None
        fetched = False
        if fetch and self.has_remote(remote):
            try:
                self.fetch(remote, base, timeout)
                fetched = True
            except GitError as exc:
                warning = f"Não foi possível atualizar '{base}' a partir de '{remote}' ({exc}); usada a base local."
                logger.warning(warning)
        remote_ref = f"refs/remotes/{remote}/{base}"
        # Recém-buscada, a base remota é a mais atual. Sem fetch, a local pode estar à frente (commits não enviados).
        if fetched and self.ref_exists(remote_ref):
            return f"{remote}/{base}", None
        if self.branch_exists(base):
            return base, warning
        if self.ref_exists(remote_ref):
            return f"{remote}/{base}", warning
        raise GitError(f"Branch base '{base}' não existe (nem local nem em '{remote}').")

    def ensure_work_branch(
        self,
        name: str,
        protected: tuple[str, ...],
        base: str | None = None,
        remote: str = "origin",
        fetch: bool = False,
        network_timeout: int = 120,
    ) -> BranchResult:
        """Cria/usa `name` somente se o branch atual for protegido (main/master).

        O branch novo sai de `base` (padrão: o branch protegido atual), atualizado do remoto quando `fetch`.
        """
        current = self.current_branch()
        if current not in protected:
            logger.info("Branch atual '%s' não é protegido; mantendo.", current)
            return BranchResult(name=current, created=False, switched=False)

        if self._run("check-ref-format", "--branch", name, check=False).returncode != 0:
            raise GitError(f"Nome de branch inválido: {name}")

        if self.branch_exists(name):
            logger.info("Branch '%s' já existe; fazendo checkout.", name)
            self._run("checkout", name)
            return BranchResult(name=name, created=False, switched=True)

        start_point, warning = self.resolve_start_point(base or current, remote, fetch, network_timeout)
        # --no-track: o branch do card não deve ficar configurado para enviar à base.
        self._run("checkout", "--no-track", "-b", name, start_point)
        logger.info("Branch '%s' criado a partir de '%s'.", name, start_point)
        return BranchResult(name=name, created=True, switched=True, start_point=start_point, warning=warning)

    # --- Conclusão do card --------------------------------------------------

    def base_commit(self, candidates: list[str]) -> tuple[str, str]:
        """Ponto em que o branch atual saiu da primeira base existente em `candidates`: (base, commit)."""
        for ref in candidates:
            if self.ref_exists(ref):
                return ref, self._run("merge-base", "HEAD", ref).stdout.strip()
        raise GitError(f"Nenhum branch base encontrado ({', '.join(candidates)}) para comparar as alterações do card.")

    def changes_since(self, base: str) -> list[str]:
        """Alterações desde `base` (commitadas ou não) em formato name-status, mais os arquivos não rastreados."""
        tracked = self._run("diff", "--name-status", base).stdout.splitlines()
        untracked = self._run("ls-files", "--others", "--exclude-standard").stdout.splitlines()
        return [line.replace("\t", " ") for line in tracked if line.strip()] + [f"?? {path}" for path in untracked if path]

    def diff_stat(self, base: str = "HEAD") -> str:
        return self._run("diff", "--stat", base, check=False).stdout.strip()

    def remote_url(self, remote: str) -> str:
        return self._run("remote", "get-url", remote).stdout.strip()

    def commits_ahead(self, base: str) -> int:
        return int(self._run("rev-list", "--count", f"{base}..HEAD").stdout.strip() or 0)

    def push(self, remote: str, branch: str, timeout: int) -> None:
        # Sem terminal (web): falta de credencial deve falhar na hora, não esperar senha até o timeout.
        self._run(
            "push", "--quiet", "--set-upstream", remote, branch, timeout=timeout, env={"GIT_TERMINAL_PROMPT": "0"}
        )

    def commit_all(self, message: str) -> str | None:
        """Faz `git add -A` e commit. Retorna o hash curto, ou None se não houver nada para commitar."""
        self._run("add", "-A")
        if self._run("diff", "--cached", "--quiet", check=False).returncode == 0:
            return None
        self._run("commit", "-m", message)
        return self._run("rev-parse", "--short", "HEAD").stdout.strip()
