import pytest

from core.exceptions import ClassificationError, PipelineCancelled
from core.models import PipelineInput
from core.pipeline import STEPS, Pipeline, StepFailed, format_report, report_to_dict
from integration.claude_runner import ClaudeCodeRunner
from tests.conftest import FakeClassifier, git


def make_pipeline(settings, classifier):
    events = []
    pipeline = Pipeline(settings, classifier=classifier, on_step=lambda n, state: events.append((n, state)))
    return pipeline, events


def data_for(repo, request="Adicionar health check"):
    return PipelineInput(repo_path=repo, card_number="1425", request=request)


def test_sem_implementacao_pula_o_passo_4(repo, settings_for, fake_classifier):
    pipeline, events = make_pipeline(settings_for("nao-existe"), fake_classifier)
    report = pipeline.run(data_for(repo))

    assert events == [
        (1, "running"), (1, "done"),
        (2, "running"), (2, "done"),
        (3, "running"), (3, "done"),
        (4, "skipped"),
        (5, "running"), (5, "done"),
    ]
    assert report.branch.name == "feature/card-1425" and report.branch.created
    assert git(repo, "rev-parse", "--abbrev-ref", "HEAD") == "feature/card-1425"
    assert report.claude is None
    assert fake_classifier.requests == ["Adicionar health check"]


def test_com_implementacao(repo, settings_for, fake_claude):
    pipeline, events = make_pipeline(settings_for(fake_claude()), FakeClassifier("hotfix"))
    report = pipeline.run(data_for(repo), implement=True)

    assert (4, "done") in events
    assert report.branch.name == "hotfix/card-1425"
    assert report.claude.output == "Arquivo criado."
    assert report.changed_files == ["?? novo.txt"]
    assert report.total_usage.output_tokens == 201  # classificador + Claude Code


def test_classificacao_define_o_prefixo_do_branch(repo, settings_for):
    report = make_pipeline(settings_for(), FakeClassifier("release"))[0].run(data_for(repo))
    assert report.branch.name == "release/card-1425"


def test_implementar_exige_working_tree_limpo(repo, settings_for, fake_claude, fake_classifier):
    (repo / "pendente.txt").write_text("x")
    pipeline, events = make_pipeline(settings_for(fake_claude()), fake_classifier)

    with pytest.raises(StepFailed, match="commit ou stash") as exc:
        pipeline.run(data_for(repo), implement=True)

    assert exc.value.step == 1
    assert "?? pendente.txt" in str(exc.value)
    assert events == [(1, "running"), (1, "failed")]
    assert fake_classifier.requests == []
    assert git(repo, "rev-parse", "--abbrev-ref", "HEAD") == "main"


def test_lista_de_pendencias_e_truncada(repo, settings_for, fake_classifier):
    for i in range(12):
        (repo / f"f{i:02}.txt").write_text("x")
    with pytest.raises(StepFailed, match="e mais 2"):
        make_pipeline(settings_for(), fake_classifier)[0].run(data_for(repo), implement=True)


def test_sem_implementacao_aceita_working_tree_sujo(repo, settings_for, fake_classifier):
    (repo / "pendente.txt").write_text("x")
    report = make_pipeline(settings_for(), fake_classifier)[0].run(data_for(repo))
    assert report.initial_status.dirty_files == ["?? pendente.txt"]


def test_falha_de_passo_informa_numero_e_nome(repo, settings_for):
    classifier = FakeClassifier(error=ClassificationError("API fora"))
    pipeline, events = make_pipeline(settings_for(), classifier)

    with pytest.raises(StepFailed) as exc:
        pipeline.run(data_for(repo))

    assert exc.value.step == 2
    assert isinstance(exc.value.cause, ClassificationError)
    assert str(exc.value) == f"Falha no passo 2 ({STEPS[2]}): API fora"
    assert events[-1] == (2, "failed")
    assert git(repo, "rev-parse", "--abbrev-ref", "HEAD") == "main"


def test_cancelado_antes_do_proximo_passo(repo, settings_for):
    class CancelsWhileClassifying(FakeClassifier):
        def classify(self, request):
            pipeline.cancel()
            return super().classify(request)

    pipeline, events = make_pipeline(settings_for(), CancelsWhileClassifying())
    with pytest.raises(PipelineCancelled):
        pipeline.run(data_for(repo))

    assert events[-2:] == [(2, "done"), (3, "cancelled")]
    assert git(repo, "rev-parse", "--abbrev-ref", "HEAD") == "main"


def test_cancelado_durante_o_claude_code(repo, settings_for, fake_classifier):
    class CancelledRunner(ClaudeCodeRunner):
        def run(self, *args, **kwargs):
            raise PipelineCancelled("cancelado")

    settings = settings_for()
    events = []
    pipeline = Pipeline(
        settings,
        classifier=fake_classifier,
        runner=CancelledRunner(settings.claude_code),
        on_step=lambda n, state: events.append((n, state)),
    )
    with pytest.raises(PipelineCancelled):  # não é embrulhado em StepFailed
        pipeline.run(data_for(repo), implement=True)
    assert events[-1] == (4, "cancelled")


def test_callback_com_erro_nao_derruba_a_pipeline(repo, settings_for, fake_classifier):
    def broken(number, state):
        raise RuntimeError("callback quebrado")

    report = Pipeline(settings_for(), classifier=fake_classifier, on_step=broken).run(data_for(repo))
    assert report.branch.created


def test_relatorio_texto_sem_implementacao(repo, settings_for, fake_classifier):
    text = format_report(make_pipeline(settings_for(), fake_classifier)[0].run(data_for(repo)))
    assert "Card 1425" in text
    assert "feature/card-1425 (criado)" in text
    assert "Abra o Claude Code" in text
    assert "Resumo do Claude Code" not in text


def test_relatorio_texto_com_implementacao(repo, settings_for, fake_claude, fake_classifier):
    report = make_pipeline(settings_for(fake_claude()), fake_classifier)[0].run(data_for(repo), implement=True)
    text = format_report(report)
    assert "Arquivo criado." in text
    assert "?? novo.txt" in text
    assert "Revise as alterações" in text
    assert "US$ 0.0200" in text


def test_relatorio_json(repo, settings_for, fake_claude, fake_classifier):
    report = make_pipeline(settings_for(fake_claude()), fake_classifier)[0].run(data_for(repo), implement=True)
    data = report_to_dict(report)

    assert data["implement"] is True
    assert data["classification"] == "feature"
    assert data["branch"] == {"name": "feature/card-1425", "created": True}
    assert data["claude"] == {"output": "Arquivo criado.", "duration_seconds": 1.5, "cost_usd": 0.02}
    assert data["changed_files"] == ["?? novo.txt"]
    assert data["tokens"]["classifier"]["total"] == 101
    assert data["tokens"]["claude"]["input"] == 1500
    assert data["tokens"]["total"]["total"] == 101 + 1700
