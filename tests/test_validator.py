import pytest

from core.exceptions import MissingInputError
from core.validator import validate_input


def test_argumentos_explicitos(repo):
    data = validate_input("Adicionar health check", repo=str(repo), card="1425")
    assert data.repo_path == repo.resolve()
    assert data.card_number == "1425"
    assert data.request == "Adicionar health check"


@pytest.mark.parametrize(
    "template",
    [
        "repo: {repo} card: 1425 Adicionar health check",
        "card #1425 repositório={repo} Adicionar health check",
        "Adicionar health check path: '{repo}' tarefa-1425",
    ],
)
def test_repo_e_card_na_mensagem(repo, template):
    data = validate_input(template.format(repo=repo))
    assert data.repo_path == repo.resolve()
    assert data.card_number == "1425"
    assert data.request == "Adicionar health check"


def test_argumentos_explicitos_tem_precedencia(repo, make_repo):
    outro = make_repo("outro")
    data = validate_input(f"repo: {outro} card: 1 tarefa", repo=str(repo), card="2")
    assert data.repo_path == repo.resolve()
    assert data.card_number == "2"


def test_card_com_cerquilha(repo):
    assert validate_input("tarefa", repo=str(repo), card="#77").card_number == "77"


def test_caminho_com_til_e_expandido(repo, monkeypatch):
    monkeypatch.setenv("HOME", str(repo.parent))
    assert validate_input("tarefa", repo=f"~/{repo.name}", card="1").repo_path == repo.resolve()


@pytest.mark.parametrize(
    ("kwargs", "faltando"),
    [
        ({"card": "1"}, "caminho do repositório"),
        ({"repo": "."}, "número do card"),
        ({}, "caminho do repositório e número do card"),
    ],
)
def test_dados_imprescindiveis_ausentes(kwargs, faltando):
    with pytest.raises(MissingInputError, match=faltando):
        validate_input("tarefa", **kwargs)


def test_card_nao_numerico(repo):
    with pytest.raises(MissingInputError, match="Número do card inválido"):
        validate_input("tarefa", repo=str(repo), card="ABC-1")


def test_repositorio_inexistente(tmp_path):
    with pytest.raises(MissingInputError, match="não existe"):
        validate_input("tarefa", repo=str(tmp_path / "nada"), card="1")


def test_descricao_vazia(repo):
    with pytest.raises(MissingInputError, match="descrição da tarefa está vazia"):
        validate_input(f"repo: {repo} card: 1")


def test_descricao_opcional_quando_nao_exigida(repo):
    data = validate_input(f"repo: {repo} card: 1", require_request=False)
    assert (data.card_number, data.request) == ("1", "")
