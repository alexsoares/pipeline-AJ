import pytest

import main
from core import pipeline as pipeline_module
from core.conclusion import ConclusionOptions, conclude
from core.exceptions import ClassificationError, GitError, PipelineCancelled, RedmineError
from core.history import History, format_history
from core.models import PipelineInput
from core.pipeline import Pipeline, StepFailed
from core.settings import PROJECT_ROOT, HistorySettings, Settings
from tests.conftest import FakeClassifier, FakeRedmine, git


@pytest.fixture
def history(history_path) -> History:
    return History(history_path)


def run_pipeline(repo, settings, history, classifier=None, implement=False, card="1425"):
    data = PipelineInput(repo_path=repo, card_number=card, request="tarefa")
    return Pipeline(settings, classifier=classifier or FakeClassifier(), history=history).run(data, implement=implement)


def test_registra_execucao_com_sucesso(repo, settings_for, history, fake_claude):
    run_pipeline(repo, settings_for(fake_claude()), history, implement=True)

    [item] = history.recent()
    assert (item["kind"], item["card"], item["status"], item["classification"]) == ("pipeline", "1425", "succeeded", "feature")
    assert (item["classification_source"], item["branch"], item["implement"]) == ("llm", "feature/card-1425", 1)
    assert (item["input_tokens"], item["output_tokens"], item["cost_usd"]) == (100 + 1500, 1 + 200, 0.02)
    assert item["details"]["changed_files"] == ["?? novo.txt"]
    assert item["error"] is None


def test_registra_falha_e_cancelamento(repo, settings_for, history):
    with pytest.raises(StepFailed):
        run_pipeline(repo, settings_for(), history, classifier=FakeClassifier(error=ClassificationError("API fora")))

    class Cancels(FakeClassifier):
        def classify(self, request):
            raise PipelineCancelled("Execução cancelada pelo usuário.")

    with pytest.raises(PipelineCancelled):
        run_pipeline(repo, settings_for(), history, classifier=Cancels())

    cancelled, failed = history.recent()
    assert (failed["status"], failed["classification"]) == ("failed", None)
    assert "API fora" in failed["error"]
    assert (cancelled["status"], cancelled["error"]) == ("cancelled", "Execução cancelada pelo usuário.")


def test_filtra_por_card_e_limita(repo, make_repo, settings_for, history):
    run_pipeline(repo, settings_for(), history, card="1")
    run_pipeline(make_repo("b"), settings_for(), history, card="2")
    run_pipeline(make_repo("c"), settings_for(), history, card="2")

    assert [i["card"] for i in history.recent()] == ["2", "2", "1"]
    assert [i["card"] for i in history.recent(card="1")] == ["1"]
    assert len(history.recent(limit=2)) == 2


def test_totais_por_mes(repo, make_repo, settings_for, history, fake_claude):
    run_pipeline(repo, settings_for(fake_claude()), history, implement=True)
    run_pipeline(make_repo("b"), settings_for(), history)

    [month] = history.monthly_summary()
    assert (month["runs"], month["succeeded"], month["implemented"]) == (2, 2, 1)
    assert (month["input_tokens"], month["output_tokens"], month["cost_usd"]) == (1700, 202, 0.02)


def test_registra_conclusao(repo, history, monkeypatch):
    for key in ("GIT_AUTHOR_NAME", "GIT_COMMITTER_NAME"):
        monkeypatch.setenv(key, "Teste")
    for key in ("GIT_AUTHOR_EMAIL", "GIT_COMMITTER_EMAIL"):
        monkeypatch.setenv(key, "teste@example.com")

    with pytest.raises(GitError):  # fora do branch do card
        conclude(Settings(), repo, "42", history=history)

    git(repo, "checkout", "-q", "-b", "hotfix/card-42")
    conclude(Settings(), repo, "42", "x", ConclusionOptions(commit=True, hours=1),
             redmine_client=FakeRedmine(), history=history)
    conclude(Settings(), repo, "42", "x", ConclusionOptions(redmine_note=True),
             redmine_client=FakeRedmine(note_error=RedmineError("fora do ar")), history=history)

    partial, ok, failed = history.recent()
    assert (failed["kind"], failed["status"]) == ("conclusao", "failed") and "não no branch" in failed["error"]
    assert ok["status"] == "succeeded" and ok["details"]["commit"] and ok["details"]["hours"] == "1 h (Codificação)"
    assert (partial["status"], partial["error"]) == ("partial", "Redmine: fora do ar")
    assert history.monthly_summary() == []  # os totais contam só as execuções da pipeline


def test_falha_ao_gravar_nao_derruba_a_pipeline(repo, settings_for, tmp_path, caplog):
    blocked = tmp_path / "bloqueado"
    blocked.mkdir()
    history = History(blocked)  # um diretório no lugar do arquivo do banco

    report = run_pipeline(repo, settings_for(), history)

    assert report.branch.created
    assert "Não foi possível gravar o histórico" in caplog.text


def test_historico_desativado():
    assert History.from_settings(HistorySettings(enabled=False)) is None


def test_caminho_relativo_fica_na_raiz_do_projeto():
    assert History.from_settings(HistorySettings(path="data/x.db")).path == PROJECT_ROOT / "data" / "x.db"


def test_banco_inexistente_nao_e_criado_na_consulta(history, history_path):
    assert history.recent() == [] and history.monthly_summary() == []
    assert not history_path.exists()


def test_texto_do_historico(repo, settings_for, history):
    assert format_history([], []) == "Nenhuma execução registrada no histórico."
    run_pipeline(repo, settings_for(), history)
    text = format_history(history.recent(), history.monthly_summary())
    assert "pipeline" in text and "1425" in text and "succeeded" in text
    assert "Totais por mês" in text


def test_cli_historico(repo, settings_for, history, monkeypatch, capsys):
    monkeypatch.setattr(main, "load_settings", lambda: settings_for())
    run_pipeline(repo, settings_for(), history, card="7")

    assert main.main(["--historico"]) == main.EXIT_OK
    assert "Histórico" in capsys.readouterr().out
    assert main.main(["--historico", "5", "--card", "8"]) == main.EXIT_OK
    assert "Nenhuma execução" in capsys.readouterr().out


def test_web_historico(repo, settings_for, history, monkeypatch):
    from web.app import create_app

    monkeypatch.setattr(pipeline_module, "RequestClassifier", lambda settings: FakeClassifier())
    run_pipeline(repo, settings_for(), history, card="7")
    client = create_app(settings_for()).test_client()

    data = client.get("/api/history?limit=10").get_json()
    assert data["enabled"] and data["items"][0]["card"] == "7" and data["summary"][0]["runs"] == 1
    assert client.get("/api/history?card=%238").get_json()["items"] == []
