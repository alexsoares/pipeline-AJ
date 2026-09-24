import time

import pytest

from core import pipeline as pipeline_module
from tests.conftest import FakeClassifier, FakeRedmine
from web.app import create_app


@pytest.fixture
def client_for(settings_for, monkeypatch):
    monkeypatch.setattr(pipeline_module, "RequestClassifier", lambda settings: FakeClassifier())

    def _make(binary="nao-existe"):
        return create_app(settings_for(binary)).test_client()

    return _make


def post(client, repo, **body):
    return client.post("/api/runs", json={"repo": str(repo), "card": "7", "message": "tarefa", **body})


def wait(client, job_id, timeout=15):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = client.get(f"/api/runs/{job_id}").get_json()
        if job["status"] != "running":
            return job
        time.sleep(0.05)
    raise AssertionError("a execução não terminou a tempo")


def test_lista_os_passos(client_for):
    steps = client_for().get("/api/steps").get_json()
    assert [s["number"] for s in steps] == [1, 2, 3, 4, 5, 6]


def test_pagina_inicial(client_for):
    assert b"Agente AJ" in client_for().get("/").data


def test_entrada_invalida(client_for):
    res = client_for().post("/api/runs", json={"message": "tarefa"})
    assert res.status_code == 400
    assert "Dados imprescindíveis ausentes" in res.get_json()["error"]


def test_execucao_sem_implementacao(client_for, repo):
    client = client_for()
    res = post(client, repo)
    assert res.status_code == 202
    assert res.get_json()["repo"] == str(repo.resolve())

    job = wait(client, res.get_json()["id"])
    assert job["status"] == "succeeded"
    assert job["steps"] == {"1": "done", "2": "skipped", "3": "done", "4": "done", "5": "skipped", "6": "done"}
    assert job["result"]["branch"]["name"] == "feature/card-7"


def test_implement_so_liga_com_true(client_for, repo, fake_claude):
    client = client_for(fake_claude())
    job = wait(client, post(client, repo, implement="sim").get_json()["id"])
    assert job["implement"] is False
    assert job["steps"]["5"] == "skipped"


def test_execucao_com_implementacao(client_for, repo, fake_claude):
    client = client_for(fake_claude())
    job = wait(client, post(client, repo, implement=True).get_json()["id"])
    assert job["status"] == "succeeded"
    assert job["result"]["claude"]["output"] == "Arquivo criado."
    assert job["result"]["changed_files"] == ["?? novo.txt"]


def test_falha_informa_o_passo(client_for, repo):
    (repo / "pendente.txt").write_text("x")
    client = client_for()
    job = wait(client, post(client, repo, implement=True).get_json()["id"])
    assert job["status"] == "failed"
    assert job["failed_step"] == 1
    assert "commit ou stash" in job["error"]


def test_bloqueio_por_repositorio_e_cancelamento(client_for, make_repo, fake_claude):
    repo_a, repo_b = make_repo("a"), make_repo("b")
    (repo_a / "sub").mkdir()
    client = client_for(fake_claude(sleep=30))

    job_a = post(client, repo_a, implement=True)
    job_b = post(client, repo_b, implement=True)
    same_repo = post(client, repo_a / "sub")
    assert (job_a.status_code, job_b.status_code) == (202, 202)
    assert same_repo.status_code == 409
    assert "neste repositório" in same_repo.get_json()["error"]

    for job in (job_a, job_b):
        assert client.post(f"/api/runs/{job.get_json()['id']}/cancel").status_code == 202
    for job in (job_a, job_b):
        final = wait(client, job.get_json()["id"])
        assert final["status"] == "cancelled"
        assert final["cancel_requested"] is True
        assert "cancelled" in final["steps"].values()

    assert client.post(f"/api/runs/{job_a.get_json()['id']}/cancel").status_code == 409
    assert post(client, repo_a).status_code == 202  # repositório liberado


def test_execucao_inexistente(client_for):
    client = client_for()
    assert client.get("/api/runs/nada").status_code == 404
    assert client.post("/api/runs/nada/cancel").status_code == 404


def test_conclusao_pela_web(client_for, repo, fake_claude):
    client = client_for(fake_claude())
    job = wait(client, post(client, repo, implement=True).get_json()["id"])

    res = client.post("/api/conclusions", json={
        "repo": str(repo), "card": "7", "message": "tarefa", "job_id": job["id"], "commit": False,
    })
    assert res.status_code == 200
    data = res.get_json()
    assert data["branch"] == "feature/card-7"
    assert data["changed_files"] == ["?? novo.txt"]
    assert "Arquivo criado." in (repo / "CARD-7.md").read_text()  # resumo do Claude Code vindo do job


def test_conclusao_fora_do_branch_do_card(client_for, repo):
    res = client_for().post("/api/conclusions", json={"repo": str(repo), "card": "7"})
    assert res.status_code == 422
    assert "não no branch do card 7" in res.get_json()["error"]


def test_conclusao_bloqueada_durante_execucao(client_for, repo, fake_claude):
    client = client_for(fake_claude(sleep=30))
    job = post(client, repo, implement=True).get_json()
    res = client.post("/api/conclusions", json={"repo": str(repo), "card": "7"})
    assert res.status_code == 409
    client.post(f"/api/runs/{job['id']}/cancel")
    wait(client, job["id"])


def test_conclusao_com_redmine_pela_web(client_for, repo, monkeypatch):
    monkeypatch.delenv("REDMINE_URL", raising=False)
    monkeypatch.delenv("REDMINE_API_KEY", raising=False)
    client = client_for()
    wait(client, post(client, repo).get_json()["id"])

    res = client.post("/api/conclusions", json={"repo": str(repo), "card": "7", "redmine": True})
    assert res.status_code == 200  # documento gerado; a falha do Redmine vem no corpo
    data = res.get_json()
    assert data["redmine_requested"] is True
    assert data["redmine_url"] is None
    assert "Redmine não configurado" in data["redmine_error"]


def test_config_informa_se_o_redmine_esta_configurado(client_for, redmine_env, monkeypatch):
    assert client_for().get("/api/config").get_json() == {"redmine": True}
    monkeypatch.delenv("REDMINE_API_KEY")
    assert client_for().get("/api/config").get_json() == {"redmine": False}


def test_sem_descricao_e_sem_redmine(client_for, repo):
    res = client_for().post("/api/runs", json={"repo": str(repo), "card": "7"})
    assert res.status_code == 400
    assert "configure o Redmine" in res.get_json()["error"]


def test_sem_descricao_com_redmine(client_for, repo, redmine_env, monkeypatch):
    monkeypatch.setattr(pipeline_module, "RedmineClient", lambda settings: FakeRedmine())
    client = client_for()
    res = client.post("/api/runs", json={"repo": str(repo), "card": "1425"})
    assert res.status_code == 202

    job = wait(client, res.get_json()["id"])
    assert job["status"] == "succeeded"
    assert job["steps"]["2"] == "done"
    assert job["result"]["issue"]["subject"] == "Adicionar health check"
    assert job["result"]["classification_source"] == "redmine"
