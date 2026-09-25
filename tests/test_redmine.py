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


# --- Status e horas ------------------------------------------------------------------------------

STATUSES = {"issue_statuses": [{"id": 1, "name": "Analisar"}, {"id": 4, "name": "Homologar"}]}
ACTIVITIES = {"time_entry_activities": [{"id": 9, "name": "Codificação"}, {"id": 20, "name": "Análise"}]}


def routed_client(routes, calls):
    def handler(request):
        calls.append(request)
        key = (request.method, request.url.path)
        status, body = routes[key] if not callable(routes[key]) else routes[key](request)
        return httpx.Response(status, json=body) if body is not None else httpx.Response(status)

    return RedmineClient(RedmineSettings(), transport=httpx.MockTransport(handler))


def issue_with_status(name):
    body = json.loads(json.dumps(ISSUE_JSON))
    body["issue"]["status"] = {"name": name}
    return body


def test_nota_e_status_numa_unica_atualizacao(redmine_env):
    calls = []
    client = routed_client({
        ("GET", "/issue_statuses.json"): (200, STATUSES),
        ("PUT", "/issues/1425.json"): (204, None),
        ("GET", "/issues/1425.json"): (200, issue_with_status("Homologar")),
    }, calls)

    url, current = client.update_issue("1425", notes="texto", status="homologar")

    assert (url, current) == ("https://redmine.exemplo/issues/1425", "Homologar")
    put = next(c for c in calls if c.method == "PUT")
    assert json.loads(put.content) == {"issue": {"notes": "texto", "status_id": 4}}


def test_status_devolve_o_status_real_da_tarefa(redmine_env):
    client = routed_client({
        ("GET", "/issue_statuses.json"): (200, STATUSES),
        ("PUT", "/issues/1425.json"): (204, None),
        ("GET", "/issues/1425.json"): (200, issue_with_status("Analisar")),  # fluxo de trabalho barrou
    }, [])
    assert client.update_issue("1425", status="Homologar")[1] == "Analisar"


def test_status_inexistente(redmine_env):
    client = routed_client({("GET", "/issue_statuses.json"): (200, STATUSES)}, [])
    with pytest.raises(RedmineError, match="Status 'Pronto' não existe no Redmine. Opções: Analisar, Homologar"):
        client.update_issue("1425", status="Pronto")


def test_lanca_horas(redmine_env):
    calls = []
    client = routed_client({
        ("GET", "/enumerations/time_entry_activities.json"): (200, ACTIVITIES),
        ("POST", "/time_entries.json"): (201, {"time_entry": {"id": 1}}),
    }, calls)

    client.log_time("1425", 1.5, "análise", comments="x" * 300)

    body = json.loads(calls[-1].content)["time_entry"]
    assert body == {"issue_id": 1425, "hours": 1.5, "activity_id": 20, "comments": "x" * 255}


def test_atividade_inexistente(redmine_env):
    client = routed_client({("GET", "/enumerations/time_entry_activities.json"): (200, ACTIVITIES)}, [])
    with pytest.raises(RedmineError, match="Atividade 'Dormir' não existe"):
        client.log_time("1425", 1, "Dormir")
