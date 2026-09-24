import pytest

from core.conclusion import commit_message, conclude, conclusion_to_dict, format_conclusion
from core.exceptions import GitError
from core.settings import Settings
from tests.conftest import GIT_IDENTITY, git


@pytest.fixture
def card_repo(repo, monkeypatch):
    """Repositório no branch do card, com um commit e alterações não commitadas."""
    for key, value in (("GIT_AUTHOR_NAME", "Teste"), ("GIT_COMMITTER_NAME", "Teste"),
                       ("GIT_AUTHOR_EMAIL", "teste@example.com"), ("GIT_COMMITTER_EMAIL", "teste@example.com")):
        monkeypatch.setenv(key, value)
    (repo / "existente.txt").write_text("v1\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "base")
    git(repo, "checkout", "-q", "-b", "hotfix/card-42")
    (repo / "commitado.py").write_text("print(1)\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "wip")
    (repo / "existente.txt").write_text("v2\n")
    (repo / "novo.py").write_text("print(2)\n")
    return repo


def test_lista_alteracoes_desde_a_base_e_gera_documento(card_repo):
    report = conclude(Settings(), card_repo, "42", "Corrigir login")

    assert report.classification == "hotfix"
    assert (report.branch, report.base) == ("hotfix/card-42", "main")
    assert report.changed_files == ["A commitado.py", "M existente.txt", "?? novo.py"]
    assert report.commit is None and not report.commit_requested

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
    report = conclude(Settings(), card_repo, "42", "Corrigir login\ndetalhes", commit=True)

    assert report.commit == git(card_repo, "rev-parse", "--short", "HEAD")
    assert git(card_repo, "log", "-1", "--format=%s") == "hotfix(card-42): Corrigir login"
    assert git(card_repo, "status", "--porcelain") == ""
    assert "CARD-42.md" in git(card_repo, "show", "--name-only", "--format=", "HEAD").split()


def test_documento_do_card_nao_entra_na_lista_ao_concluir_de_novo(card_repo):
    conclude(Settings(), card_repo, "42", commit=True)  # CARD-42.md passa a ser rastreado
    (card_repo / "mais.txt").write_text("x")
    report = conclude(Settings(), card_repo, "42")
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


def test_relatorios(card_repo):
    report = conclude(Settings(), card_repo, "42", "Corrigir login")
    text = format_conclusion(report)
    assert "Conclusão do card 42" in text and "não solicitado" in text and "?? novo.py" in text
    data = conclusion_to_dict(report)
    assert data["document_path"] == str(card_repo / "CARD-42.md")
    assert data["commit_requested"] is False
