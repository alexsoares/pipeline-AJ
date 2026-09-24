import json

import httpx
import pytest

from core.exceptions import RedmineError
from core.settings import RedmineSettings
from integration.redmine import RedmineClient, redmine_configured
from tests.conftest import make_issue


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
        (422, {"errors": ["Notas é muito longo"]}, "rejeitou a requisição: Notas é muito longo"),
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


ISSUE_JSON = {
    "issue": {
        "id": 1425,
        "subject": " Adicionar health check ",
        "description": "Criar o endpoint /health.",
        "tracker": {"id": 3, "name": "Evolução"},
        "status": {"id": 1, "name": "Analisar"},
        "project": {"id": 9, "name": "DCCA - SIM"},
        "custom_fields": [
            {"id": 1, "name": "Critérios de Aceitação", "value": "Responder 200."},
            {"id": 2, "name": "aceite", "value": "1"},
            {"id": 3, "name": "outro", "value": None},
        ],
    }
}


def test_le_a_tarefa(redmine_env):
    calls = []
    issue = client_returning(200, ISSUE_JSON, calls=calls).get_issue("1425")

    assert calls[0].method == "GET"
    assert str(calls[0].url) == "https://redmine.exemplo/issues/1425.json"
    assert calls[0].headers["X-Redmine-API-Key"] == "chave-teste"
    assert issue == make_issue()  # título aparado; só os campos personalizados configurados


def test_tarefa_sem_descricao_nem_campos(redmine_env):
    body = {"issue": {"id": 7, "subject": "Só título", "description": None, "tracker": {"name": "Correção"}}}
    issue = client_returning(200, body).get_issue("7")
    assert (issue.description, issue.custom_fields, issue.status) == ("", {}, "")
    assert issue.as_request() == "Tarefa #7 (Correção): Só título"


def test_resposta_inesperada(redmine_env):
    with pytest.raises(RedmineError, match="Resposta inesperada"):
        client_returning(200, {"outra": 1}).get_issue("1")


def test_texto_da_tarefa_como_solicitacao():
    assert make_issue().as_request() == (
        "Tarefa #1425 (Evolução): Adicionar health check\n\n"
        "Criar o endpoint /health.\n\n"
        "Critérios de aceitação: Responder 200."
    )


def test_redmine_configured(monkeypatch):
    assert not redmine_configured(RedmineSettings())
    monkeypatch.setenv("REDMINE_API_KEY", "k")
    assert not redmine_configured(RedmineSettings())
    assert redmine_configured(RedmineSettings(url="https://r"))
