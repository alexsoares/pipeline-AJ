"""Passo 5 (opcional): executa as alterações do card via Claude Code em modo headless (`claude -p`).

A saída é lida como `stream-json` (um evento JSON por linha): cada ação do Claude Code vira uma frase curta
de progresso (`on_progress`), e o evento final `result` traz o resumo, os tokens e o custo.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import signal
import subprocess
import threading
from collections.abc import Callable
from pathlib import Path

from core.exceptions import ClaudeExecutionError, PipelineCancelled
from core.models import ClaudeRunResult, TokenUsage
from core.settings import ClaudeCodeSettings

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[str], None]
JOIN_TIMEOUT_SECONDS = 5
TEXT_PREVIEW = 140

_TOOL_VERBS = {
    "Read": "Lendo", "Write": "Criando", "Edit": "Editando", "MultiEdit": "Editando", "NotebookEdit": "Editando",
}


def _short(text: str, limit: int = TEXT_PREVIEW) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _relative(path: str, cwd: Path) -> str:
    try:
        return str(Path(path).relative_to(cwd))
    except ValueError:
        return path


def describe_event(event: dict, cwd: Path) -> list[str]:
    """Frases de progresso para um evento do stream-json do Claude Code."""
    if event.get("type") == "system" and event.get("subtype") == "init":
        model = event.get("model")
        return [f"Claude Code iniciado{f' ({model})' if model else ''}"]
    if event.get("type") != "assistant":
        return []
    messages = []
    for item in (event.get("message") or {}).get("content") or []:
        if item.get("type") == "text" and item.get("text", "").strip():
            messages.append(f"💬 {_short(item['text'])}")
        elif item.get("type") == "tool_use":
            name, args = item.get("name", ""), item.get("input") or {}
            if name in _TOOL_VERBS and args.get("file_path"):
                messages.append(f"{_TOOL_VERBS[name]} {_relative(args['file_path'], cwd)}")
            elif name == "Bash":
                messages.append(f"Executando: {_short(args.get('command', ''), 100)}")
            elif name in ("Grep", "Glob"):
                messages.append(f"Buscando {_short(args.get('pattern', ''), 80)}")
            elif name == "TodoWrite":
                messages.append("Atualizando o plano de trabalho")
            else:
                messages.append(f"Usando {name}")
    return messages

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
            "--output-format", "stream-json",
            "--verbose",  # exigido pelo stream-json no modo -p
            "--permission-mode", self.settings.permission_mode,
            "--strict-mcp-config",  # não carrega os MCPs/conectores do usuário: menos tokens e ruído
        ]
        if self.settings.model:
            cmd += ["--model", self.settings.model]
        return cmd

    def run(
        self,
        repo_path: Path,
        card: str,
        classification: str,
        request: str,
        on_progress: ProgressCallback | None = None,
    ) -> ClaudeRunResult:
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
                bufsize=1,
                start_new_session=True,
            )

        result: dict[str, dict] = {}
        unexpected: list[str] = []
        stderr_parts: list[str] = []

        def notify(message: str) -> None:
            if not on_progress:
                return
            try:
                on_progress(message)
            except Exception:  # noqa: BLE001 - quem observa o progresso não pode derrubar a execução
                logger.exception("Falha no callback de progresso do Claude Code")

        def read_stdout() -> None:
            for line in proc.stdout:
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    unexpected.append(line)
                    continue
                if not isinstance(event, dict):
                    continue
                if event.get("type") == "result":
                    result["payload"] = event
                for message in describe_event(event, repo_path):
                    notify(message)

        readers = [
            threading.Thread(target=read_stdout, daemon=True),
            threading.Thread(target=lambda: stderr_parts.append(proc.stderr.read()), daemon=True),
        ]
        for reader in readers:
            reader.start()

        try:
            proc.wait(timeout=self.settings.timeout_seconds)
        except subprocess.TimeoutExpired as exc:
            self._kill(proc, signal.SIGKILL)
            proc.wait()
            raise ClaudeExecutionError(f"Claude Code excedeu o timeout de {self.settings.timeout_seconds}s.") from exc
        except BaseException:
            # Ctrl+C na CLI: em sessão própria, o Claude Code não recebe o SIGINT do terminal.
            self._kill(proc, signal.SIGKILL)
            proc.wait()
            raise
        finally:
            for reader in readers:
                reader.join(JOIN_TIMEOUT_SECONDS)
            with self._lock:
                self._proc = None

        if self._cancelled:
            raise PipelineCancelled(
                "Execução cancelada pelo usuário. O Claude Code pode ter deixado alterações parciais no branch."
            )

        stderr = "".join(stderr_parts)
        payload = result.get("payload")
        if payload is None:
            detail = stderr.strip() or "\n".join(unexpected).strip() or "(sem saída)"
            raise ClaudeExecutionError(f"Saída inesperada do Claude Code (exit {proc.returncode}): {detail[:500]}")

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
