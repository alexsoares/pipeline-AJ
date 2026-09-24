"""Guard clause da pipeline: garante repositório e número do card antes de qualquer passo.

Os dados podem vir por argumentos explícitos (--repo / --card) ou embutidos na
mensagem do chat, por exemplo:

    repo: ~/projetos/api card: 1425 Adicionar endpoint de health check
"""

from __future__ import annotations

import re
from pathlib import Path

from core.exceptions import MissingInputError
from core.models import PipelineInput

_REPO_PATTERN = re.compile(
    r"\b(?:repo|repositorio|repositório|caminho|path)\s*[:=]\s*(\"[^\"]+\"|'[^']+'|\S+)",
    re.IGNORECASE,
)
_CARD_PATTERN = re.compile(r"\b(?:card|tarefa|task)\s*[:=#-]?\s*#?(\d+)\b", re.IGNORECASE)
_CARD_NUMBER = re.compile(r"^\d+$")

USAGE_HINT = (
    "Exemplo de uso:\n"
    '  python main.py "repo: /caminho/do/repo card: 1425 <descrição da tarefa>"\n'
    '  python main.py --repo /caminho/do/repo --card 1425 "<descrição da tarefa>"'
)


def _extract(pattern: re.Pattern[str], text: str) -> tuple[str | None, str]:
    """Retorna o primeiro valor capturado e o texto sem o trecho correspondente."""
    match = pattern.search(text)
    if not match:
        return None, text
    value = match.group(1).strip("\"'")
    remaining = (text[: match.start()] + text[match.end():]).strip()
    return value, remaining


def validate_input(message: str, repo: str | None = None, card: str | None = None) -> PipelineInput:
    """Valida a entrada do chat. Levanta MissingInputError se faltar repositório ou card."""
    request = (message or "").strip()

    repo_from_msg, request = _extract(_REPO_PATTERN, request)
    card_from_msg, request = _extract(_CARD_PATTERN, request)
    repo = (repo or repo_from_msg or "").strip()
    card = (card or card_from_msg or "").strip().lstrip("#")

    missing = []
    if not repo:
        missing.append("caminho do repositório")
    if not card:
        missing.append("número do card/tarefa")
    if missing:
        raise MissingInputError(
            "Dados imprescindíveis ausentes: "
            + " e ".join(missing)
            + ".\nA pipeline foi interrompida: é obrigatório informar o caminho do "
            "repositório E o número do card.\n\n" + USAGE_HINT
        )

    if not _CARD_NUMBER.match(card):
        raise MissingInputError(f"Número do card inválido: '{card}'. Informe apenas dígitos (ex.: 1425).")

    repo_path = Path(repo).expanduser().resolve()
    if not repo_path.is_dir():
        raise MissingInputError(f"O caminho do repositório não existe ou não é um diretório: {repo_path}")

    if not request:
        raise MissingInputError("A descrição da tarefa está vazia: informe o que deve ser feito no card.")

    return PipelineInput(repo_path=repo_path, card_number=card, request=request)
