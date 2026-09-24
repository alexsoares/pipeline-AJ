from types import SimpleNamespace

import anthropic
import httpx
import pytest

from core.classifier import RequestClassifier
from core.exceptions import ClassificationError
from core.settings import ClassifierSettings

REQUEST = httpx.Request("POST", "https://api.anthropic.com/v1/messages")


def response(text: str, stop_reason: str = "end_turn"):
    return SimpleNamespace(
        content=[SimpleNamespace(type="text", text=text)],
        stop_reason=stop_reason,
        usage=SimpleNamespace(
            input_tokens=80, output_tokens=2, cache_creation_input_tokens=None, cache_read_input_tokens=10
        ),
        _request_id="req_teste",
    )


class FakeClient:
    def __init__(self, result):
        self.result = result
        self.calls = []
        self.messages = self

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if isinstance(self.result, BaseException):
            raise self.result
        return self.result


def classify(result, request="corrigir bug em produção"):
    client = FakeClient(result)
    return RequestClassifier(ClassifierSettings(), client=client).classify(request), client


@pytest.mark.parametrize(
    ("text", "expected"),
    [("hotfix", "hotfix"), ("Feature.", "feature"), ("  RELEASE\n", "release"), ("Classificação: hotfix", "hotfix")],
)
def test_extrai_rotulo(text, expected):
    result, _ = classify(response(text))
    assert result.classification == expected


def test_envia_modelo_prompt_e_solicitacao():
    _, client = classify(response("feature"), request="nova tela")
    call = client.calls[0]
    settings = ClassifierSettings()
    assert call["model"] == settings.model
    assert call["max_tokens"] == settings.max_tokens
    assert call["messages"] == [{"role": "user", "content": "nova tela"}]
    assert "hotfix" in call["system"]


def test_contabiliza_tokens():
    result, _ = classify(response("feature"))
    assert (result.usage.input_tokens, result.usage.output_tokens) == (80, 2)
    assert result.usage.cache_creation_input_tokens == 0
    assert result.usage.total_input == 90


def test_rotulo_invalido():
    with pytest.raises(ClassificationError, match="Classificação inválida"):
        classify(response("bugfix"))


def test_recusa():
    with pytest.raises(ClassificationError, match="recusou"):
        classify(response("", stop_reason="refusal"))


def _status_error(cls, code):
    return cls("erro", response=httpx.Response(code, request=REQUEST), body=None)


@pytest.mark.parametrize(
    ("error", "message"),
    [
        (TypeError("sem credencial"), "Nenhuma credencial"),
        (_status_error(anthropic.AuthenticationError, 401), "inválidas ou ausentes"),
        (_status_error(anthropic.RateLimitError, 429), "Limite de requisições"),
        (_status_error(anthropic.InternalServerError, 500), r"Erro da API Anthropic \(500\)"),
        (anthropic.APIConnectionError(request=REQUEST), "Falha de conexão"),
    ],
)
def test_erros_da_api_viram_classification_error(error, message):
    with pytest.raises(ClassificationError, match=message):
        classify(error)
