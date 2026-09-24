import pytest

import main
from core import pipeline as pipeline_module
from core.exceptions import ClassificationError
from tests.conftest import FakeClassifier


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
    assert cli(["--implementar", "--repo", str(repo), "--card", "12", "tarefa"], binary=fake_claude()) == main.EXIT_OK
    assert "Arquivo criado." in capsys.readouterr().out


def test_entrada_invalida(cli, capsys):
    assert cli(["tarefa sem repo"]) == main.EXIT_INVALID_INPUT
    assert "Dados imprescindíveis ausentes" in capsys.readouterr().err


def test_falha_da_pipeline(cli, repo, capsys):
    code = cli(["--repo", str(repo), "--card", "12", "tarefa"], classifier=FakeClassifier(error=ClassificationError("x")))
    assert code == main.EXIT_PIPELINE_ERROR
    assert "Falha no passo 2" in capsys.readouterr().err


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
    assert "FALHOU: Redmine não configurado" in out
    assert (repo / "CARD-12.md").exists()
