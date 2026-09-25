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



# --- Base do branch do card (fetch + <remote>/<base>) ------------------------------------------------


def create(repo, name="feature/card-1", **kwargs):
    return GitManager(repo).ensure_work_branch(name, PROTECTED, remote="origin", **kwargs)


def test_cria_a_partir_da_base_remota_atualizada(remote_setup):
    local, _, push = remote_setup
    push("main", "novo-no-remoto.txt")  # o main local fica para trás

    result = create(local, fetch=True)

    assert (result.start_point, result.warning) == ("origin/main", None)
    assert (local / "novo-no-remoto.txt").exists()
    assert git(local, "rev-parse", "main") != git(local, "rev-parse", "origin/main"), "o main local não é alterado"
    assert git(local, "config", "--get", "branch.feature/card-1.merge", check=False) == "", "não rastreia a base"


def test_sem_fetch_usa_a_base_local(remote_setup):
    local, _, push = remote_setup
    push("main", "novo-no-remoto.txt")

    result = create(local, fetch=False)

    assert result.start_point == "main"
    assert not (local / "novo-no-remoto.txt").exists()


def test_base_configurada_existente_so_no_remoto(remote_setup):
    local, _, push = remote_setup
    push("develop", "da-develop.txt")

    result = create(local, base="develop", fetch=True)

    assert result.start_point == "origin/develop"
    assert (local / "da-develop.txt").exists()


def test_fetch_falhou_usa_base_local_e_avisa(remote_setup):
    local, _, _ = remote_setup
    git(local, "remote", "set-url", "origin", "/caminho/que/nao/existe.git")

    result = create(local, fetch=True)

    assert result.start_point == "main"
    assert "Não foi possível atualizar 'main'" in result.warning


def test_repo_sem_remoto_nao_tenta_fetch(repo):
    result = create(repo, fetch=True)
    assert (result.start_point, result.warning) == ("main", None)


def test_base_inexistente(repo):
    with pytest.raises(GitError, match="Branch base 'develop' não existe"):
        create(repo, base="develop", fetch=True)
    assert git(repo, "rev-parse", "--abbrev-ref", "HEAD") == "main"


def test_base_commit_usa_o_primeiro_candidato_existente(remote_setup):
    local, _, push = remote_setup
    push("develop", "d.txt")
    create(local, base="develop", fetch=True)
    (local / "card.txt").write_text("x")

    base, commit = GitManager(local).base_commit(["origin/develop", "develop", "origin/main", "main"])

    assert base == "origin/develop"
    assert commit == git(local, "rev-parse", "origin/develop")
