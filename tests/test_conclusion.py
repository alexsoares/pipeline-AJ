from dataclasses import replace

import pytest

from core.conclusion import (
    ConclusionOptions,
    base_candidates,
    commit_message,
    conclude,
    conclusion_to_dict,
    format_conclusion,
)
from core.exceptions import GitError, GitLabError, RedmineError
from core.settings import Settings
from tests.conftest import FakeGitLab, FakeRedmine, git, make_issue

MR_URL = "https://ginner.exemplo/grupo/projeto/-/merge_requests/1"


@pytest.fixture(autouse=True)
def identidade_git(monkeypatch):
    for key, value in (("GIT_AUTHOR_NAME", "Teste"), ("GIT_COMMITTER_NAME", "Teste"),
                       ("GIT_AUTHOR_EMAIL", "teste@example.com"), ("GIT_COMMITTER_EMAIL", "teste@example.com")):
        monkeypatch.setenv(key, value)


def prepare_card(repo, branch="hotfix/card-42"):
    """Branch do card com um commit e alterações não commitadas."""
    (repo / "existente.txt").write_text("v1\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "base")
    git(repo, "checkout", "-q", "-b", branch)
    (repo / "commitado.py").write_text("print(1)\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "wip")
    (repo / "existente.txt").write_text("v2\n")
    (repo / "novo.py").write_text("print(2)\n")
    return repo


@pytest.fixture
def card_repo(repo):
    return prepare_card(repo)


@pytest.fixture
def remote_card_repo(remote_setup):
    """Branch do card num clone com remoto (para o push do merge request)."""
    local, _, _ = remote_setup
    prepare_card(local)
    git(local, "push", "-q", "origin", "main")
    git(local, "fetch", "-q", "origin")
    return local


def run(repo, request="", redmine=None, gitlab=None, settings=None, **options):
    return conclude(
        settings or Settings(), repo, "42", request, ConclusionOptions(**options),
        redmine_client=redmine or FakeRedmine(), gitlab_client=gitlab or FakeGitLab(),
    )


# --- Documento e commit --------------------------------------------------------------------------


def test_lista_alteracoes_desde_a_base_e_gera_documento(card_repo):
    report = run(card_repo, "Corrigir login")

    assert report.classification == "hotfix"
    assert (report.branch, report.base) == ("hotfix/card-42", "main")
    assert report.changed_files == ["A commitado.py", "M existente.txt", "?? novo.py"]
    assert report.commit is None and report.errors == []

    doc = (card_repo / "CARD-42.md").read_text()
    assert report.document_path == card_repo / "CARD-42.md"
    assert "# Card 42" in doc and "Corrigir login" in doc
    assert "`hotfix/card-42` (base: `main`)" in doc
    assert "- `?? novo.py`" in doc
    assert "## Resumo do diff" in doc
    assert "Resumo do Claude Code" not in doc
    assert git(card_repo, "rev-parse", "--abbrev-ref", "HEAD") == "hotfix/card-42"


def test_resumo_do_claude_code_entra_no_documento(card_repo):
    conclude(Settings(), card_repo, "42", claude_output="Ajustei a validação.")
    doc = (card_repo / "CARD-42.md").read_text()
    assert "## Resumo do Claude Code" in doc and "Ajustei a validação." in doc
    assert "_Não informada._" in doc


def test_commit_inclui_alteracoes_e_documento(card_repo):
    report = run(card_repo, "Corrigir login\ndetalhes", commit=True)

    assert report.commit == git(card_repo, "rev-parse", "--short", "HEAD")
    assert git(card_repo, "log", "-1", "--format=%s") == "hotfix(card-42): Corrigir login"
    assert git(card_repo, "status", "--porcelain") == ""
    assert "CARD-42.md" in git(card_repo, "show", "--name-only", "--format=", "HEAD").split()


def test_documento_do_card_nao_entra_na_lista_ao_concluir_de_novo(card_repo):
    run(card_repo, commit=True)  # CARD-42.md passa a ser rastreado
    (card_repo / "mais.txt").write_text("x")
    report = run(card_repo)
    assert report.changed_files == ["A commitado.py", "M existente.txt", "A novo.py", "?? mais.txt"]


def test_fora_do_branch_do_card(repo):
    with pytest.raises(GitError, match="não no branch do card 42"):
        conclude(Settings(), repo, "42")
    assert not (repo / "CARD-42.md").exists()


def test_branch_de_outro_card(repo):
    git(repo, "checkout", "-q", "-b", "feature/card-420")
    with pytest.raises(GitError, match="não no branch do card 42"):
        conclude(Settings(), repo, "42")


def test_sem_branch_base(make_repo):
    repo = make_repo(branch="trunk")
    git(repo, "checkout", "-q", "-b", "feature/card-1")
    with pytest.raises(GitError, match="Nenhum branch base"):
        conclude(Settings(), repo, "1")


def test_candidatos_de_base():
    settings = Settings()
    assert base_candidates(settings, "feature") == ["origin/main", "main", "origin/master", "master"]
    settings = replace(settings, git=replace(settings.git, base_branches={"feature": "develop"}))
    assert base_candidates(settings, "feature")[:2] == ["origin/develop", "develop"]
    assert base_candidates(settings, "hotfix")[0] == "origin/main"


@pytest.mark.parametrize(
    ("request_text", "expected"),
    [
        ("Corrigir login", "feature(card-7): Corrigir login"),
        ("", "feature(card-7): conclusão do card"),
        ("\n  Primeira linha útil\noutra", "feature(card-7): Primeira linha útil"),
    ],
)
def test_mensagem_de_commit(request_text, expected):
    assert commit_message("feature", "7", request_text) == expected


def test_mensagem_de_commit_longa_e_truncada():
    message = commit_message("feature", "7", "x" * 200)
    assert len(message) == 72 and message.endswith("…")


# --- Tarefa do Redmine como solicitação ----------------------------------------------------------


def test_com_redmine_configurado_usa_a_tarefa(card_repo, redmine_env):
    redmine = FakeRedmine(make_issue("Correção", id="42", subject="Login falha com senha longa"))
    report = run(card_repo, redmine=redmine, commit=True)

    assert redmine.fetched == ["42"]
    assert git(card_repo, "log", "-1", "--format=%s") == "hotfix(card-42): Login falha com senha longa"
    assert "Tarefa #42 (Correção): Login falha com senha longa" in (card_repo / "CARD-42.md").read_text()
    assert "Tarefa        : #42 Login falha com senha longa" in format_conclusion(report)


def test_falha_ao_ler_a_tarefa_nao_impede_a_conclusao(card_repo, redmine_env):
    report = run(card_repo, "Corrigir login", redmine=FakeRedmine(error=RedmineError("Timeout")), commit=True)
    assert report.commit and report.issue is None
    assert git(card_repo, "log", "-1", "--format=%s") == "hotfix(card-42): Corrigir login"


# --- Nota, status e horas no Redmine -------------------------------------------------------------


def test_nota_recebe_o_mesmo_texto_do_documento(card_repo):
    redmine = FakeRedmine()
    report = run(card_repo, "Corrigir login", redmine=redmine, commit=True, redmine_note=True)

    assert redmine.notes == [("42", (card_repo / "CARD-42.md").read_text())]
    assert report.redmine_url == "https://redmine.exemplo/issues/42"
    assert "Nota Redmine  : adicionada em https://redmine.exemplo/issues/42" in format_conclusion(report)


def test_nada_no_redmine_sem_pedir(card_repo):
    redmine = FakeRedmine()
    report = run(card_repo, redmine=redmine)
    assert redmine.updates == [] and redmine.time_entries == []
    text = format_conclusion(report)
    assert "Nota Redmine  : não solicitado" in text and "Horas Redmine : não solicitado" in text


def test_nota_e_status_numa_unica_atualizacao(card_repo):
    redmine = FakeRedmine()
    report = run(card_repo, redmine=redmine, redmine_note=True, redmine_status="Homologar")

    assert len(redmine.updates) == 1
    issue_id, notes, status = redmine.updates[0]
    assert (issue_id, status) == ("42", "Homologar") and notes.startswith("# Card 42")
    assert report.status_applied == "Homologar"
    assert "Status Redmine: Homologar" in format_conclusion(report)


def test_status_sem_nota(card_repo):
    redmine = FakeRedmine()
    run(card_repo, redmine=redmine, redmine_status="Homologar")
    assert redmine.updates == [("42", None, "Homologar")]


def test_status_barrado_pelo_fluxo_de_trabalho(card_repo):
    report = run(card_repo, redmine=FakeRedmine(workflow_blocks=True), redmine_note=True, redmine_status="Finalizado")

    assert report.redmine_url  # a nota foi gravada
    assert report.status_applied is None
    assert "não foi aplicado (a tarefa continua em 'Analisar')" in report.errors[0]
    assert "Status Redmine: FALHOU" in format_conclusion(report)


def test_lanca_horas_com_atividade_padrao_e_escolhida(card_repo):
    redmine = FakeRedmine()
    report = run(card_repo, "Corrigir login", redmine=redmine, hours=1.5)
    assert redmine.time_entries == [("42", 1.5, "Codificação", "hotfix(card-42): Corrigir login")]
    assert report.hours_logged == "1.5 h (Codificação)"

    redmine = FakeRedmine()
    run(card_repo, redmine=redmine, hours=2, activity="Análise")
    assert redmine.time_entries[0][1:3] == (2, "Análise")


def test_falhas_no_redmine_mantem_documento_e_commit(card_repo):
    redmine = FakeRedmine(note_error=RedmineError("Tarefa 42 não encontrada"), time_error=RedmineError("Atividade x"))
    report = run(card_repo, "x", redmine=redmine, commit=True, redmine_note=True, hours=1)

    assert report.commit and (card_repo / "CARD-42.md").exists()
    assert report.errors == ["Redmine: Tarefa 42 não encontrada", "Horas no Redmine: Atividade x"]
    text = format_conclusion(report)
    assert "Nota Redmine  : FALHOU" in text and "Falhas (documento e commit foram mantidos)" in text
    assert conclusion_to_dict(report)["errors"] == report.errors


# --- Merge request -------------------------------------------------------------------------------


def test_merge_request_envia_o_branch_e_abre_o_mr(remote_card_repo, gitlab_env, redmine_env):
    gitlab, redmine = FakeGitLab(), FakeRedmine(make_issue(id="42", subject="Corrigir login"))
    report = run(remote_card_repo, redmine=redmine, gitlab=gitlab, commit=True, merge_request=True, redmine_note=True)

    assert report.merge_request_url == MR_URL and report.errors == []
    mr = gitlab.requests[0]
    assert (mr["project"], mr["source"], mr["target"]) == ("grupo/projeto", "hotfix/card-42", "main")
    assert mr["title"] == "hotfix(card-42): Corrigir login"
    assert mr["description"].startswith("# Card 42")
    assert "**Tarefa no Redmine:** https://redmine.exemplo/issues/1425" in mr["description"]
    # o branch chegou ao remoto, com upstream configurado
    assert git(remote_card_repo, "rev-parse", "origin/hotfix/card-42") == git(remote_card_repo, "rev-parse", "HEAD")
    # a nota no Redmine leva o link do MR
    assert redmine.notes[0][1].rstrip().endswith(f"**Merge request:** {MR_URL}")


def test_merge_request_sem_gitlab_configurado(remote_card_repo):
    report = run(remote_card_repo, commit=True, merge_request=True)
    assert report.merge_request_url is None
    assert "GitLab não configurado" in report.errors[0]
    assert report.commit  # o commit foi mantido


def test_merge_request_com_alteracoes_nao_commitadas(remote_card_repo, gitlab_env):
    gitlab = FakeGitLab()
    report = run(remote_card_repo, gitlab=gitlab, merge_request=True)
    assert "não commitada(s); marque o commit" in report.errors[0]
    assert gitlab.requests == []
    assert git(remote_card_repo, "ls-remote", "--heads", "origin", "hotfix/card-42") == ""


def test_merge_request_sem_commits_no_branch(remote_setup, gitlab_env):
    local, _, _ = remote_setup
    git(local, "checkout", "-q", "-b", "hotfix/card-42")
    report = run(local, merge_request=True)  # só o CARD-42.md, não commitado
    assert "não commitada" in report.errors[0]

    report = run(local, commit=True, merge_request=True)  # agora o CARD-42.md é o único commit
    assert report.merge_request_url == MR_URL


def test_falha_no_gitlab_nao_impede_o_redmine(remote_card_repo, gitlab_env):
    redmine = FakeRedmine()
    report = run(
        remote_card_repo, redmine=redmine, gitlab=FakeGitLab(error=GitLabError("Projeto não encontrado")),
        commit=True, merge_request=True, redmine_note=True,
    )
    assert report.errors == ["Merge request: Projeto não encontrado"]
    assert redmine.notes and "Merge request:" not in redmine.notes[0][1]


def test_relatorio_json(card_repo):
    report = run(card_repo, "Corrigir login", commit=True, hours=1)
    data = conclusion_to_dict(report)
    assert data["requested"] == {
        "commit": True, "merge_request": False, "redmine_note": False, "redmine_status": None, "hours": 1,
    }
    assert data["document_path"] == str(card_repo / "CARD-42.md")
    assert data["hours_logged"] == "1 h (Codificação)"
