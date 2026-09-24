"""Passo 2: classifica a solicitação em feature, hotfix ou release com um LLM leve."""

from __future__ import annotations

import logging
import re

import anthropic

from core.exceptions import ClassificationError
from core.models import VALID_CLASSIFICATIONS, ClassificationResult, TokenUsage
from core.settings import ClassifierSettings

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """Você classifica solicitações de desenvolvimento de software.
Responda com exatamente uma palavra, em minúsculas, sem pontuação:
- feature: nova funcionalidade, melhoria ou refatoração planejada.
- hotfix: correção urgente de bug ou falha em produção.
- release: preparação de versão (bump de versão, changelog, ajustes de empacotamento/deploy)."""

_LABEL_PATTERN = re.compile(r"\b(" + "|".join(VALID_CLASSIFICATIONS) + r")\b")


class RequestClassifier:
    def __init__(self, settings: ClassifierSettings, client: anthropic.Anthropic | None = None) -> None:
        self.settings = settings
        self.client = client or anthropic.Anthropic(timeout=settings.timeout_seconds)

    def classify(self, request: str) -> ClassificationResult:
        try:
            response = self.client.messages.create(
                model=self.settings.model,
                max_tokens=self.settings.max_tokens,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": request}],
            )
        except TypeError as exc:
            # A SDK valida os headers antes de enviar e lança TypeError quando não há credencial.
            raise ClassificationError(
                "Nenhuma credencial da Anthropic encontrada: defina ANTHROPIC_API_KEY."
            ) from exc
        except anthropic.AuthenticationError as exc:
            raise ClassificationError("Credenciais da Anthropic inválidas ou ausentes (ANTHROPIC_API_KEY).") from exc
        except anthropic.RateLimitError as exc:
            raise ClassificationError("Limite de requisições da Anthropic atingido; tente novamente.") from exc
        except anthropic.APIStatusError as exc:
            raise ClassificationError(f"Erro da API Anthropic ({exc.status_code}): {exc.message}") from exc
        except anthropic.APIConnectionError as exc:
            raise ClassificationError("Falha de conexão com a API Anthropic.") from exc

        if response.stop_reason == "refusal":
            raise ClassificationError("O modelo recusou classificar a solicitação.")

        text = "".join(b.text for b in response.content if b.type == "text").strip().lower()
        match = _LABEL_PATTERN.search(text)
        if not match:
            raise ClassificationError(f"Classificação inválida retornada pelo LLM: '{text}'")

        usage = TokenUsage(
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            cache_creation_input_tokens=response.usage.cache_creation_input_tokens or 0,
            cache_read_input_tokens=response.usage.cache_read_input_tokens or 0,
        )
        logger.info("Solicitação classificada como '%s' (request_id=%s)", match.group(1), response._request_id)
        return ClassificationResult(classification=match.group(1), usage=usage)  # type: ignore[arg-type]
