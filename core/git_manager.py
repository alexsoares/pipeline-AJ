"""Operações Git usadas pela pipeline (passos 1, 3 e levantamento de alterações)."""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

from core.exceptions import GitError
from core.models import BranchResult, GitStatus

logger = logging.getLogger(__name__)


class GitManager:
    def __init__(self, repo_path: Path, timeout_seconds: int = 30) -> None:
        self.repo_path = repo_path
        self.timeout_seconds = timeout_seconds

    def _run(self, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        cmd = ["git", *args]
        logger.debug("Executando: %s (cwd=%s)", " ".join(cmd), self.repo_path)
        try:
            result = subprocess.run(
                cmd,
                cwd=self.repo_path,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
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
        return self._run("rev-parse", "--verify", "--quiet", f"refs/heads/{name}", check=False).returncode == 0

    def ensure_work_branch(self, name: str, protected: tuple[str, ...]) -> BranchResult:
        """Cria/usa `name` somente se o branch atual for protegido (main/master)."""
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

        self._run("checkout", "-b", name)
        logger.info("Branch '%s' criado a partir de '%s'.", name, current)
        return BranchResult(name=name, created=True, switched=True)

    # --- Conclusão do card --------------------------------------------------

    def base_commit(self, protected: tuple[str, ...]) -> tuple[str, str]:
        """Ponto em que o branch atual saiu do primeiro branch protegido existente: (nome do protegido, commit)."""
        for name in protected:
            if self.branch_exists(name):
                return name, self._run("merge-base", "HEAD", name).stdout.strip()
        raise GitError(f"Nenhum branch base encontrado ({', '.join(protected)}) para comparar as alterações do card.")

    def changes_since(self, base: str) -> list[str]:
        """Alterações desde `base` (commitadas ou não) em formato name-status, mais os arquivos não rastreados."""
        tracked = self._run("diff", "--name-status", base).stdout.splitlines()
        untracked = self._run("ls-files", "--others", "--exclude-standard").stdout.splitlines()
        return [line.replace("\t", " ") for line in tracked if line.strip()] + [f"?? {path}" for path in untracked if path]

    def diff_stat(self, base: str = "HEAD") -> str:
        return self._run("diff", "--stat", base, check=False).stdout.strip()

    def commit_all(self, message: str) -> str | None:
        """Faz `git add -A` e commit. Retorna o hash curto, ou None se não houver nada para commitar."""
        self._run("add", "-A")
        if self._run("diff", "--cached", "--quiet", check=False).returncode == 0:
            return None
        self._run("commit", "-m", message)
        return self._run("rev-parse", "--short", "HEAD").stdout.strip()
