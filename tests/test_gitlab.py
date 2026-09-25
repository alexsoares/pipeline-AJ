import json

import httpx
import pytest

from core.exceptions import GitLabError
from core.settings import GitLabSettings
from integration.gitlab import GitLabClient, gitlab_configured, parse_remote


def client(handler, **settings):
    return GitLabClient(GitLabSettings(**settings), transport=httpx.MockTransport(handler))


@pytest.mark.parametrize(
    ("remote", "expected"),
    [
        ("git@ginner.exemplo:docsim/00_Sistemas/ARRE048.git", ("ginner.exemplo", "docsim/00_Sistemas/ARRE048")),
        ("ssh://git@Ginner.Exemplo:2222/grupo/proj.git", ("ginner.exemplo", "grupo/proj")),
        ("https://ginner.exemplo/grupo/proj", ("ginner.exemplo", "grupo/proj")),
        ("https://usuario@ginner.exemplo/grupo/sub/proj.git/", ("ginner.exemplo", "grupo/sub/proj")),
    ],
)
def test_parse_remote(remote, expected):
    assert parse_remote(remote) == expected


def test_parse_remote_invalido():
    with pytest.raises(GitLabError, match="Não foi possível entender"):
        parse_remote("/caminho/local/repo.git")


def test_configuracao(monkeypatch):
    assert not gitlab_configured(GitLabSettings())
    monkeypatch.setenv("GITLAB_TOKEN", "t")
    assert gitlab_configured(GitLabSettings(url="https://g"))


def test_remoto_de_outro_servidor(gitlab_env):
    with pytest.raises(GitLabError, match="aponta para 'github.com'"):
        client(lambda r: httpx.Response(500)).project_for_remote("git@github.com:alexsoares/pipeline-AJ.git")


def test_remoto_do_gitlab_configurado(gitlab_env):
    assert client(lambda r: httpx.Response(500)).project_for_remote("git@ginner.exemplo:g/p.git") == "g/p"


def test_abre_merge_request(gitlab_env):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(201, json={"web_url": "https://ginner.exemplo/g/p/-/merge_requests/3"})

    url = client(handler, remove_source_branch=True).create_merge_request(
        "g/sub/p", "hotfix/card-1", "main", "hotfix(card-1): x", "# Card 1"
    )

    assert url == "https://ginner.exemplo/g/p/-/merge_requests/3"
    request = calls[0]
    assert request.method == "POST"
    assert request.url.raw_path == b"/api/v4/projects/g%2Fsub%2Fp/merge_requests"
    assert request.headers["PRIVATE-TOKEN"] == "token-teste"
    assert json.loads(request.content) == {
        "source_branch": "hotfix/card-1", "target_branch": "main", "title": "hotfix(card-1): x",
        "description": "# Card 1", "remove_source_branch": True,
    }


def test_reaproveita_merge_request_aberto(gitlab_env):
    def handler(request):
        if request.method == "POST":
            return httpx.Response(409, json={"message": ["Another open merge request already exists"]})
        assert request.url.params["source_branch"] == "hotfix/card-1"
        return httpx.Response(200, json=[{"web_url": "https://ginner.exemplo/g/p/-/merge_requests/2"}])

    url = client(handler).create_merge_request("g/p", "hotfix/card-1", "main", "t", "d")
    assert url.endswith("/merge_requests/2")


@pytest.mark.parametrize(
    ("status", "body", "message"),
    [
        (401, {"message": "401 Unauthorized"}, "recusou o token"),
        (404, {"message": "404 Project Not Found"}, "Projeto 'g/p' não encontrado"),
        (400, {"message": ["Target branch não existe"]}, r"HTTP 400\): Target branch não existe"),
        (409, {"message": ["conflito"]}, "HTTP 409"),
    ],
)
def test_erros(gitlab_env, status, body, message):
    def handler(request):
        if request.method == "GET":
            return httpx.Response(200, json=[])
        return httpx.Response(status, json=body)

    with pytest.raises(GitLabError, match=message):
        client(handler).create_merge_request("g/p", "b", "main", "t", "d")


def test_nao_configurado():
    with pytest.raises(GitLabError, match="defina GITLAB_URL e GITLAB_TOKEN"):
        client(lambda r: httpx.Response(201)).create_merge_request("g/p", "b", "main", "t", "d")


def test_erro_de_rede(gitlab_env):
    def handler(request):
        raise httpx.ConnectError("recusada")

    with pytest.raises(GitLabError, match="Falha de conexão"):
        client(handler).create_merge_request("g/p", "b", "main", "t", "d")
