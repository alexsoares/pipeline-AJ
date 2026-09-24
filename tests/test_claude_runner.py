import json
import os
import threading
import time

import pytest

from core.exceptions import ClaudeExecutionError, PipelineCancelled
from core.settings import ClaudeCodeSettings
from integration.claude_runner import ClaudeCodeRunner


def run(runner, repo):
    return runner.run(repo, "1425", "feature", "Adicionar health check")


def args_of(binary):
    return json.loads(binary.with_name(binary.name + ".args.json").read_text())


def process_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    # Processo zumbi (já morreu, aguardando o pai) conta como encerrado.
    try:
        with open(f"/proc/{pid}/stat") as f:
            return f.read().split()[2] != "Z"
    except FileNotFoundError:
        return False


def test_sucesso(repo, fake_claude):
    binary = fake_claude()
    result = run(ClaudeCodeRunner(ClaudeCodeSettings(binary=str(binary))), repo)

    assert result.output == "Arquivo criado."
    assert result.duration_seconds == 1.5
    assert result.cost_usd == 0.02
    assert (result.usage.input_tokens, result.usage.output_tokens, result.usage.cache_read_input_tokens) == (1000, 200, 500)
    assert (repo / "novo.txt").exists(), "o Claude Code deve rodar com cwd no repositório"


def test_monta_comando(repo, fake_claude):
    binary = fake_claude()
    run(ClaudeCodeRunner(ClaudeCodeSettings(binary=str(binary), model="opus", permission_mode="acceptEdits")), repo)
    args = args_of(binary)

    prompt = args[args.index("-p") + 1]
    assert "#1425 (feature)" in prompt and "Adicionar health check" in prompt
    assert "Não faça commit" in prompt
    assert args[args.index("--output-format") + 1] == "json"
    assert args[args.index("--permission-mode") + 1] == "acceptEdits"
    assert args[args.index("--model") + 1] == "opus"
    assert "--strict-mcp-config" in args


def test_sem_modelo_nao_passa_flag(repo, fake_claude):
    binary = fake_claude()
    run(ClaudeCodeRunner(ClaudeCodeSettings(binary=str(binary))), repo)
    assert "--model" not in args_of(binary)


def test_binario_inexistente(repo):
    with pytest.raises(ClaudeExecutionError, match="não encontrado"):
        run(ClaudeCodeRunner(ClaudeCodeSettings(binary="nao-existe-claude")), repo)


def test_saida_nao_json(repo, fake_claude):
    binary = fake_claude(stdout="Error: not logged in", exit_code=1)
    with pytest.raises(ClaudeExecutionError, match="Saída inesperada.*not logged in"):
        run(ClaudeCodeRunner(ClaudeCodeSettings(binary=str(binary))), repo)


def test_resultado_com_erro(repo, fake_claude):
    binary = fake_claude({"is_error": True, "subtype": "error_max_turns", "result": "limite de turnos"})
    with pytest.raises(ClaudeExecutionError, match=r"error_max_turns.*limite de turnos"):
        run(ClaudeCodeRunner(ClaudeCodeSettings(binary=str(binary))), repo)


def test_timeout_mata_o_processo(repo, fake_claude):
    binary = fake_claude(sleep=30)
    started = time.monotonic()
    with pytest.raises(ClaudeExecutionError, match="timeout de 1s"):
        run(ClaudeCodeRunner(ClaudeCodeSettings(binary=str(binary), timeout_seconds=1)), repo)
    assert time.monotonic() - started < 10
    child = int(binary.with_name(binary.name + ".child").read_text())
    assert not process_alive(child)


def test_cancelar_durante_execucao_encerra_grupo_de_processos(repo, fake_claude):
    binary = fake_claude(sleep=30)
    child_file = binary.with_name(binary.name + ".child")
    runner = ClaudeCodeRunner(ClaudeCodeSettings(binary=str(binary)))

    def cancel_when_started():
        deadline = time.monotonic() + 10
        while not child_file.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        runner.cancel()

    threading.Thread(target=cancel_when_started).start()
    started = time.monotonic()
    with pytest.raises(PipelineCancelled, match="alterações parciais"):
        run(runner, repo)
    assert time.monotonic() - started < 10
    child = int(child_file.read_text())
    deadline = time.monotonic() + 5
    while process_alive(child) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not process_alive(child)


def test_cancelar_antes_de_iniciar(repo, fake_claude):
    binary = fake_claude()
    runner = ClaudeCodeRunner(ClaudeCodeSettings(binary=str(binary)))
    runner.cancel()
    with pytest.raises(PipelineCancelled):
        run(runner, repo)
    assert not (repo / "novo.txt").exists()
