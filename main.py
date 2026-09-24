"""Ponto de entrada da pipeline do Agente AJ.

Uso:
    python main.py "repo: /caminho/do/repo card: 1425 Adicionar endpoint de health check"
    python main.py --repo /caminho/do/repo --card 1425 "Adicionar endpoint de health check"
    python main.py --implementar --repo /caminho/do/repo --card 1425 "..."   # já implementa via Claude Code
    python main.py --concluir [--commit] [--redmine] --repo /caminho/do/repo --card 1425 ["..."]  # fecha o card
"""

from __future__ import annotations

import argparse
import logging
import sys

from core.conclusion import conclude, format_conclusion
from core.exceptions import MissingInputError, PipelineError
from core.logging_config import setup_logging
from core.pipeline import Pipeline, format_report
from core.settings import load_settings
from core.validator import validate_input

EXIT_OK = 0
EXIT_INVALID_INPUT = 2
EXIT_PIPELINE_ERROR = 1


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Pipeline do Agente AJ")
    parser.add_argument("message", nargs="*", help="Mensagem do chat (descrição da tarefa)")
    parser.add_argument("--repo", help="Caminho absoluto ou relativo do repositório")
    parser.add_argument("--card", help="Número do card/tarefa")
    parser.add_argument(
        "--implementar",
        action="store_true",
        help="Implementa o card com o Claude Code (headless) no branch preparado, em vez de só orientar",
    )
    parser.add_argument(
        "--concluir",
        action="store_true",
        help="Conclui o card depois da implementação: lista as alterações desde a base e gera o CARD-<n>.md",
    )
    parser.add_argument("--commit", action="store_true", help="Com --concluir, também faz o commit padronizado")
    parser.add_argument(
        "--redmine", action="store_true", help="Com --concluir, anota o documento do card na tarefa do Redmine"
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    for flag in ("commit", "redmine"):
        if getattr(args, flag) and not args.concluir:
            print(f"❌ --{flag} só vale junto com --concluir.", file=sys.stderr)
            return EXIT_INVALID_INPUT
    if args.concluir and args.implementar:
        print("❌ Use --implementar ou --concluir, não os dois.", file=sys.stderr)
        return EXIT_INVALID_INPUT

    # Guard clause: nada é executado sem repositório e card.
    try:
        data = validate_input(
            " ".join(args.message), repo=args.repo, card=args.card, require_request=not args.concluir
        )
    except MissingInputError as exc:
        print(f"❌ {exc}", file=sys.stderr)
        return EXIT_INVALID_INPUT

    try:
        settings = load_settings()
        setup_logging(settings.logging)
        if args.concluir:
            conclusion = conclude(
                settings, data.repo_path, data.card_number, data.request, commit=args.commit, redmine=args.redmine
            )
            print(format_conclusion(conclusion))
            return EXIT_PIPELINE_ERROR if conclusion.redmine_error else EXIT_OK
        report = Pipeline(settings).run(data, implement=args.implementar)
    except PipelineError as exc:
        print(f"❌ {exc}", file=sys.stderr)
        return EXIT_PIPELINE_ERROR
    except KeyboardInterrupt:
        print("\n⚠️  Pipeline interrompida pelo usuário.", file=sys.stderr)
        return 130
    except Exception as exc:  # noqa: BLE001 - última barreira: nunca devolver traceback ao chat
        logging.getLogger(__name__).exception("Erro inesperado na pipeline")
        print(f"❌ Erro inesperado: {exc}. Detalhes no log da pipeline.", file=sys.stderr)
        return EXIT_PIPELINE_ERROR

    print(format_report(report))
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
