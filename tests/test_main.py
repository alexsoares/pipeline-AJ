from dataclasses import replace

import pytest

import main
from core import pipeline as pipeline_module
from core.exceptions import ClassificationError
from tests.conftest import FakeClassifier, FakeRedmine


@pytest.fixture
def cli(monkeypatch, settings_for):
    """Roda main.main() com classificador falso e sem gravar log."""

    def _run(argv, classifier=None, binary="nao-existe"):
        monkeypatch.setattr(pipeline_module, "RequestClassifier", lambda settings: classifier or FakeClassifier())
        monkeypatch.setattr(main, "load_settings", lambda: settings_for(binary))
        monkeypatch.setattr(main, "setup_logging", lambda settings: None)
        return main.main(argv)

    return _run


def test_sucesso(cli, repo, capsys):
    assert cli(["--repo", str(repo), "--card", "12", "tarefa"]) == main.EXIT_OK
    out = capsys.readouterr().out
    assert "AGENTE AJ | Card 12" in out
    assert "Abra o Claude Code" in out


def test_implementar(cli, repo, capsys, fake_claude):
    event = {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Bash",
                                                         "input": {"command": "pytest"}}]}}
    binary = fake_claude(events=[event])
    assert cli(["--implementar", "--repo", str(repo), "--card", "12", "tarefa"], binary=binary) == main.EXIT_OK
    captured = capsys.readouterr()
    assert "Arquivo criado." in captured.out
    assert "  › Executando: pytest" in captured.err


def test_entrada_invalida(cli, capsys):
    assert cli(["tarefa sem repo"]) == main.EXIT_INVALID_INPUT
    assert "Dados imprescindíveis ausentes" in capsys.readouterr().err


def test_falha_da_pipeline(cli, repo, capsys):
    code = cli(["--repo", str(repo), "--card", "12", "tarefa"], classifier=FakeClassifier(error=ClassificationError("x")))
    assert code == main.EXIT_PIPELINE_ERROR
    assert "Falha no passo 3" in capsys.readouterr().err


def test_erro_inesperado_nao_mostra_traceback(cli, repo, capsys):
    code = cli(["--repo", str(repo), "--card", "12", "tarefa"], classifier=FakeClassifier(error=RuntimeError("boom")))
    assert code == main.EXIT_PIPELINE_ERROR
    err = capsys.readouterr().err
    assert "Erro inesperado: boom" in err
    assert "Traceback" not in err


def test_concluir(cli, repo, capsys):
    assert cli(["--repo", str(repo), "--card", "12", "tarefa"]) == main.EXIT_OK
    assert cli(["--concluir", "--repo", str(repo), "--card", "12"]) == main.EXIT_OK
    out = capsys.readouterr().out
    assert "Conclusão do card 12" in out
    assert (repo / "CARD-12.md").exists()


def test_concluir_fora_do_branch(cli, repo, capsys):
    assert cli(["--concluir", "--repo", str(repo), "--card", "12"]) == main.EXIT_PIPELINE_ERROR
    assert "não no branch do card 12" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("flags", "message"),
    [
        (["--commit"], "--commit só vale junto com --concluir"),
        (["--redmine"], "--redmine só vale junto com --concluir"),
        (["--mr"], "--mr/--sem-mr só vale junto com --concluir"),
        (["--sem-mr"], "--mr/--sem-mr só vale junto com --concluir"),
        (["--status"], "--status só vale junto com --concluir"),
        (["--horas", "1"], "--horas só vale junto com --concluir"),
        (["--concluir", "--horas", "0"], "--horas deve ser maior que zero"),
        (["--concluir", "--atividade", "Análise"], "--atividade só vale junto com --horas"),
        (["--concluir", "--implementar"], "não os dois"),
    ],
)
def test_combinacoes_invalidas(cli, repo, capsys, flags, message):
    assert cli([*flags, "--repo", str(repo), "--card", "12", "tarefa"]) == main.EXIT_INVALID_INPUT
    assert message in capsys.readouterr().err


def test_concluir_com_redmine_sem_configuracao_sai_com_erro(cli, repo, capsys, monkeypatch):
    monkeypatch.delenv("REDMINE_URL", raising=False)
    monkeypatch.delenv("REDMINE_API_KEY", raising=False)
    assert cli(["--repo", str(repo), "--card", "12", "tarefa"]) == main.EXIT_OK
    assert cli(["--concluir", "--redmine", "--repo", str(repo), "--card", "12"]) == main.EXIT_PIPELINE_ERROR
    out = capsys.readouterr().out
    assert "Nota Redmine  : FALHOU" in out
    assert "Redmine: Redmine não configurado" in out
    assert (repo / "CARD-12.md").exists()


def test_sem_descricao_e_sem_redmine(cli, repo, capsys):
    assert cli(["--repo", str(repo), "--card", "12"]) == main.EXIT_INVALID_INPUT
    assert "configure o Redmine" in capsys.readouterr().err


def test_sem_descricao_com_redmine(cli, repo, capsys, redmine_env, monkeypatch):
    monkeypatch.setattr(pipeline_module, "RedmineClient", lambda settings: FakeRedmine())
    assert cli(["--repo", str(repo), "--card", "1425"]) == main.EXIT_OK
    out = capsys.readouterr().out
    assert "#1425 Adicionar health check" in out
    assert "pelo tipo 'Evolução' no Redmine" in out


@pytest.fixture
def conclude_spy(cli, repo, monkeypatch):
    """Prepara o branch do card e captura as opções que a CLI passa para conclude()."""
    assert cli(["--repo", str(repo), "--card", "12", "tarefa"]) == main.EXIT_OK
    captured = {}
    real = main.conclude

    def spy(settings, repo_path, card, request, options, **kwargs):
        captured["options"] = options
        captured["settings"] = settings
        return real(settings, repo_path, card, request, options, **kwargs)

    monkeypatch.setattr(main, "conclude", spy)
    return captured


def test_opcoes_da_conclusao(cli, repo, conclude_spy):
    cli(["--concluir", "--status", "--horas", "1.5", "--atividade", "Análise", "--repo", str(repo), "--card", "12"])
    options = conclude_spy["options"]
    assert options.redmine_status == conclude_spy["settings"].redmine.conclusion_status
    assert (options.hours, options.activity, options.merge_request) == (1.5, "Análise", False)


def test_status_com_nome_e_mr(cli, repo, conclude_spy):
    cli(["--concluir", "--status", "Testar", "--mr", "--repo", str(repo), "--card", "12"])
    assert (conclude_spy["options"].redmine_status, conclude_spy["options"].merge_request) == ("Testar", True)


def test_mr_padrao_vem_da_configuracao(cli, repo, conclude_spy, monkeypatch, settings_for):
    settings = settings_for()
    settings = replace(settings, gitlab=replace(settings.gitlab, merge_request_default=True))
    monkeypatch.setattr(main, "load_settings", lambda: settings)

    cli_code = main.main(["--concluir", "--repo", str(repo), "--card", "12"])
    assert conclude_spy["options"].merge_request is True
    assert cli_code == main.EXIT_PIPELINE_ERROR  # GitLab não configurado: falha registrada

    main.main(["--concluir", "--sem-mr", "--repo", str(repo), "--card", "12"])
    assert conclude_spy["options"].merge_request is False
