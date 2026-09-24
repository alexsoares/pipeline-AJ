"""Passo 4: executa as alterações do card via Claude Code em modo headless (`claude -p`)."""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
from pathlib import Path

from core.exceptions import ClaudeExecutionError
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

        try:
            proc = subprocess.run(
                cmd,
                cwd=repo_path,
                capture_output=True,
                text=True,
                timeout=self.settings.timeout_seconds,
            )
        except subprocess.TimeoutExpired as exc:
            raise ClaudeExecutionError(f"Claude Code excedeu o timeout de {self.settings.timeout_seconds}s.") from exc

        try:
            payload = json.loads(proc.stdout)
        except json.JSONDecodeError as exc:
            detail = proc.stderr.strip() or proc.stdout.strip()
            raise ClaudeExecutionError(
                f"Saída inesperada do Claude Code (exit {proc.returncode}): {detail[:500]}"
            ) from exc

        if proc.returncode != 0 or payload.get("is_error"):
            raise ClaudeExecutionError(
                f"Claude Code terminou com erro ({payload.get('subtype', proc.returncode)}): "
                f"{payload.get('result') or proc.stderr.strip()}"
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
