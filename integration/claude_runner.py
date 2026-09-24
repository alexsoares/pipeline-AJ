"""Passo 4 (opcional): executa as alterações do card via Claude Code em modo headless (`claude -p`)."""

from __future__ import annotations

import json
import logging
import os
import shutil
import signal
import subprocess
import threading
from pathlib import Path

from core.exceptions import ClaudeExecutionError, PipelineCancelled
from core.models import ClaudeRunResult, TokenUsage
from core.settings import ClaudeCodeSettings

logger = logging.getLogger(__name__)

PROMPT_TEMPLATE = """Você está trabalhando no card #{card} ({classification}) deste repositório.

Solicitação:
{request}

Regras:
- Responda em português.
- Implemente somente o que foi solicitado.
- Não faça commit, push nem troque de branch.
- Ao final, resuma objetivamente o que foi alterado e por quê."""


class ClaudeCodeRunner:
    def __init__(self, settings: ClaudeCodeSettings) -> None:
        self.settings = settings
        self._lock = threading.Lock()
        self._proc: subprocess.Popen[str] | None = None
        self._cancelled = False

    def cancel(self) -> None:
        """Encerra o Claude Code em andamento (ou impede que ele comece)."""
        with self._lock:
            self._cancelled = True
            if self._proc:
                self._kill(self._proc, signal.SIGTERM)

    @staticmethod
    def _kill(proc: subprocess.Popen[str], sig: int) -> None:
        # O Claude Code roda em sessão própria: o sinal vai para ele e para os processos que ele abriu.
        if proc.poll() is not None:
            return
        try:
            os.killpg(proc.pid, sig)
        except (ProcessLookupError, PermissionError):
            proc.send_signal(sig)

    def _build_command(self, prompt: str) -> list[str]:
        binary = shutil.which(self.settings.binary)
        if not binary:
            raise ClaudeExecutionError(f"Executável do Claude Code não encontrado: '{self.settings.binary}'.")
        cmd = [
            binary, "-p", prompt,
            "--output-format", "json",
            "--permission-mode", self.settings.permission_mode,
            "--strict-mcp-config",  # não carrega os MCPs/conectores do usuário: menos tokens e ruído
        ]
        if self.settings.model:
            cmd += ["--model", self.settings.model]
        return cmd

    def run(self, repo_path: Path, card: str, classification: str, request: str) -> ClaudeRunResult:
        prompt = PROMPT_TEMPLATE.format(card=card, classification=classification, request=request)
        cmd = self._build_command(prompt)
        logger.info("Executando Claude Code em %s", repo_path)

        with self._lock:
            if self._cancelled:
                raise PipelineCancelled("Execução cancelada pelo usuário.")
            proc = self._proc = subprocess.Popen(
                cmd,
                cwd=repo_path,
                stdin=subprocess.DEVNULL,  # sem terminal: o Claude Code não pode ficar esperando entrada
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                start_new_session=True,
            )

        try:
            stdout, stderr = proc.communicate(timeout=self.settings.timeout_seconds)
        except subprocess.TimeoutExpired as exc:
            self._kill(proc, signal.SIGKILL)
            proc.communicate()
            raise ClaudeExecutionError(f"Claude Code excedeu o timeout de {self.settings.timeout_seconds}s.") from exc
        except BaseException:
            # Ctrl+C na CLI: em sessão própria, o Claude Code não recebe o SIGINT do terminal.
            self._kill(proc, signal.SIGKILL)
            proc.communicate()
            raise
        finally:
            with self._lock:
                self._proc = None

        if self._cancelled:
            raise PipelineCancelled(
                "Execução cancelada pelo usuário. O Claude Code pode ter deixado alterações parciais no branch."
            )

        try:
            payload = json.loads(stdout)
        except json.JSONDecodeError as exc:
            detail = stderr.strip() or stdout.strip()
            raise ClaudeExecutionError(
                f"Saída inesperada do Claude Code (exit {proc.returncode}): {detail[:500]}"
            ) from exc

        if proc.returncode != 0 or payload.get("is_error"):
            raise ClaudeExecutionError(
                f"Claude Code terminou com erro ({payload.get('subtype', proc.returncode)}): "
                f"{payload.get('result') or stderr.strip()}"
            )

        return self._parse(payload)

    @staticmethod
    def _parse(payload: dict) -> ClaudeRunResult:
        raw_usage = payload.get("usage") or {}
        usage = TokenUsage(
            input_tokens=int(raw_usage.get("input_tokens", 0)),
            output_tokens=int(raw_usage.get("output_tokens", 0)),
            cache_creation_input_tokens=int(raw_usage.get("cache_creation_input_tokens", 0)),
            cache_read_input_tokens=int(raw_usage.get("cache_read_input_tokens", 0)),
        )
        return ClaudeRunResult(
            output=str(payload.get("result", "")).strip(),
            usage=usage,
            duration_seconds=float(payload.get("duration_ms", 0)) / 1000,
            cost_usd=payload.get("total_cost_usd"),
        )
