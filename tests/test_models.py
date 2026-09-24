from core.models import compose_request
from tests.conftest import make_issue


def test_sem_tarefa_usa_o_texto_do_usuario():
    assert compose_request(None, "  corrigir login ") == "corrigir login"


def test_tarefa_sem_texto_do_usuario():
    assert compose_request(make_issue(), " ") == make_issue().as_request()


def test_texto_do_usuario_vira_observacao():
    request = compose_request(make_issue(), "priorizar o banco")
    assert request.startswith("Tarefa #1425 (Evolução): Adicionar health check")
    assert request.endswith("Observações do usuário:\npriorizar o banco")
