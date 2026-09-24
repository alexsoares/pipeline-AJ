import json

import httpx
import pytest

from core.exceptions import RedmineError
from core.settings import RedmineSettings
from integration.redmine import RedmineClient


@pytest.fixture
def redmine_env(monkeypatch):
    monkeypatch.setenv("REDMINE_URL", "https://redmine.exemplo/")
    monkeypatch.setenv("REDMINE_API_KEY", "chave-teste")


def client_returning(status, body=None, calls=None, exc=None):
    def handler(request: httpx.Request) -> httpx.Response:
        if calls is not None:
            calls.append(request)
        if exc:
            raise exc
        return httpx.Response(status, json=body) if body is not None else httpx.Response(status)

    return RedmineClient(RedmineSettings(), transport=httpx.MockTransport(handler))


def test_adiciona_nota(redmine_env):
    calls = []
    url = client_returning(204, calls=calls).add_note("1425", "# Card 1425\n\ntexto")

    assert url == "https://redmine.exemplo/issues/1425"
    request = calls[0]
    assert request.method == "PUT"
    assert str(request.url) == "https://redmine.exemplo/issues/1425.json"
    assert request.headers["X-Redmine-API-Key"] == "chave-teste"
    assert json.loads(request.content) == {"issue": {"notes": "# Card 1425\n\ntexto"}}


def test_url_do_settings_quando_nao_ha_variavel(monkeypatch):
    monkeypatch.delenv("REDMINE_URL", raising=False)
    monkeypatch.setenv("REDMINE_API_KEY", "k")
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(204)

    client = RedmineClient(RedmineSettings(url="https://outro.redmine"), transport=httpx.MockTransport(handler))
    client.add_note("1", "x")
    assert str(calls[0].url) == "https://outro.redmine/issues/1.json"


@pytest.mark.parametrize(
    ("env", "missing"),
    [
        ({"REDMINE_API_KEY": "k"}, "REDMINE_URL"),
        ({"REDMINE_URL": "https://r"}, "REDMINE_API_KEY"),
        ({}, "REDMINE_URL e REDMINE_API_KEY"),
    ],
)
def test_nao_configurado(monkeypatch, env, missing):
    monkeypatch.delenv("REDMINE_URL", raising=False)
    monkeypatch.delenv("REDMINE_API_KEY", raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    with pytest.raises(RedmineError, match=f"defina {missing} no .env"):
        client_returning(204).add_note("1", "x")


@pytest.mark.parametrize(
    ("status", "body", "message"),
    [
        (404, None, "Tarefa 1 não encontrada"),
        (401, None, "recusou a chave de API"),
        (403, None, "HTTP 403"),
        (422, {"errors": ["Notas é muito longo"]}, "rejeitou a nota: Notas é muito longo"),
        (500, None, "HTTP 500"),
    ],
)
def test_erros_http(redmine_env, status, body, message):
    with pytest.raises(RedmineError, match=message):
        client_returning(status, body).add_note("1", "x")


@pytest.mark.parametrize(
    ("exc", "message"),
    [(httpx.ConnectTimeout("lento"), "Timeout"), (httpx.ConnectError("recusada"), "Falha de conexão")],
)
def test_erros_de_rede(redmine_env, exc, message):
    with pytest.raises(RedmineError, match=message):
        client_returning(204, exc=exc).add_note("1", "x")
