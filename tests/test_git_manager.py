import pytest

from core.exceptions import GitError
from core.git_manager import GitManager
from tests.conftest import git

PROTECTED = ("main", "master")


def test_status_de_repo_limpo(repo):
    status = GitManager(repo).status()
    assert status.branch == "main"
    assert status.dirty_files == []


def test_status_lista_alteracoes(repo):
    (repo / "a.txt").write_text("x")
    (repo / "dir").mkdir()
    (repo / "dir" / "b.txt").write_text("x")
    assert GitManager(repo).status().dirty_files == ["?? a.txt", "?? dir/b.txt"]


def test_diretorio_que_nao_e_repo(tmp_path):
    with pytest.raises(GitError, match="não é um repositório Git"):
        GitManager(tmp_path).status()


def test_detached_head(repo):
    git(repo, "checkout", "-q", "--detach")
    with pytest.raises(GitError, match="detached HEAD"):
        GitManager(repo).current_branch()


def test_toplevel_de_subdiretorio(repo):
    sub = repo / "a" / "b"
    sub.mkdir(parents=True)
    assert GitManager(sub).toplevel() == repo.resolve()


def test_cria_branch_a_partir_de_protegido(repo):
    result = GitManager(repo).ensure_work_branch("feature/card-1", PROTECTED)
    assert (result.name, result.created, result.switched) == ("feature/card-1", True, True)
    assert git(repo, "rev-parse", "--abbrev-ref", "HEAD") == "feature/card-1"


def test_reaproveita_branch_existente(repo):
    git(repo, "branch", "feature/card-1")
    result = GitManager(repo).ensure_work_branch("feature/card-1", PROTECTED)
    assert (result.created, result.switched) == (False, True)
    assert git(repo, "rev-parse", "--abbrev-ref", "HEAD") == "feature/card-1"


def test_mantem_branch_nao_protegido(repo):
    git(repo, "checkout", "-q", "-b", "minha-branch")
    result = GitManager(repo).ensure_work_branch("feature/card-1", PROTECTED)
    assert (result.name, result.created, result.switched) == ("minha-branch", False, False)
    assert not GitManager(repo).branch_exists("feature/card-1")


def test_nome_de_branch_invalido(repo):
    with pytest.raises(GitError, match="Nome de branch inválido"):
        GitManager(repo).ensure_work_branch("feature/card..1", PROTECTED)


def test_commit_all(repo, monkeypatch):
    monkeypatch.setenv("GIT_AUTHOR_NAME", "Teste")
    monkeypatch.setenv("GIT_AUTHOR_EMAIL", "teste@example.com")
    monkeypatch.setenv("GIT_COMMITTER_NAME", "Teste")
    monkeypatch.setenv("GIT_COMMITTER_EMAIL", "teste@example.com")
    manager = GitManager(repo)
    assert manager.commit_all("nada") is None

    (repo / "a.txt").write_text("x")
    sha = manager.commit_all("feature(card-1): a")
    assert sha == git(repo, "rev-parse", "--short", "HEAD")
    assert git(repo, "status", "--porcelain") == ""
