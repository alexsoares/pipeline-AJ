import json
import os
import threading
import time

import pytest

from core.exceptions import ClaudeExecutionError, PipelineCancelled
from core.settings import ClaudeCodeSettings
from integration.claude_runner import ClaudeCodeRunner, describe_event


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
    assert args[args.index("--output-format") + 1] == "stream-json"
    assert "--verbose" in args
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



# --- Progresso (stream-json) ---------------------------------------------------------------------


def assistant(*content):
    return {"type": "assistant", "message": {"content": list(content)}}


def tool(name, **args):
    return {"type": "tool_use", "name": name, "input": args}


def test_descreve_eventos(tmp_path):
    events = [
        {"type": "system", "subtype": "init", "model": "claude-sonnet-5"},
        assistant({"type": "text", "text": "Vou  ler o\nvalidador."}, tool("Read", file_path=str(tmp_path / "core/v.py"))),
        assistant(tool("Edit", file_path="/fora/do/repo.py"), tool("Write", file_path=str(tmp_path / "n.py"))),
        assistant(tool("Bash", command="python -m pytest -q"), tool("Grep", pattern="def main"), tool("TodoWrite")),
        assistant(tool("WebFetch", url="x")),
        {"type": "user", "message": {"content": [{"type": "tool_result"}]}},
    ]
    described = [line for event in events for line in describe_event(event, tmp_path)]
    assert described == [
        "Claude Code iniciado (claude-sonnet-5)",
        "💬 Vou ler o validador.",
        "Lendo core/v.py",
        "Editando /fora/do/repo.py",
        "Criando n.py",
        "Executando: python -m pytest -q",
        "Buscando def main",
        "Atualizando o plano de trabalho",
        "Usando WebFetch",
    ]


def test_texto_longo_e_resumido(tmp_path):
    [line] = describe_event(assistant({"type": "text", "text": "x" * 500}), tmp_path)
    assert len(line) == 2 + 140 and line.endswith("…")


def test_progresso_chega_durante_a_execucao(repo, fake_claude):
    binary = fake_claude(events=[assistant(tool("Edit", file_path=str(repo / "a.py")))], sleep=2)
    received = []
    started = time.monotonic()

    run_result = ClaudeCodeRunner(ClaudeCodeSettings(binary=str(binary))).run(
        repo, "1", "feature", "x", on_progress=lambda m: received.append((m, time.monotonic() - started))
    )

    assert run_result.output == "Arquivo criado."
    assert [m for m, _ in received] == ["Editando a.py"]
    assert received[0][1] < 1.5, "o progresso deve chegar antes do fim (o processo leva 2 s)"


def test_callback_de_progresso_com_erro_nao_derruba(repo, fake_claude):
    binary = fake_claude(events=[assistant(tool("Read", file_path="x"))])

    def broken(message):
        raise RuntimeError("falhou")

    assert ClaudeCodeRunner(ClaudeCodeSettings(binary=str(binary))).run(repo, "1", "f", "x", on_progress=broken)


def test_sem_evento_result(repo, fake_claude):
    binary = fake_claude(stdout=json.dumps({"type": "assistant", "message": {"content": []}}))
    with pytest.raises(ClaudeExecutionError, match=r"Saída inesperada do Claude Code \(exit 0\): \(sem saída\)"):
        run(ClaudeCodeRunner(ClaudeCodeSettings(binary=str(binary))), repo)
