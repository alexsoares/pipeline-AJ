"""Ponto de entrada da pipeline do Agente AJ.

Uso:
    python main.py "repo: /caminho/do/repo card: 1425 Adicionar endpoint de health check"
    python main.py --repo /caminho/do/repo --card 1425 "Adicionar endpoint de health check"
    python main.py --repo /caminho/do/repo --card 1425        # com o Redmine configurado, a descrição vem da tarefa
    python main.py --implementar --repo /caminho/do/repo --card 1425 "..."   # já implementa via Claude Code
    python main.py --historico [N] [--card 1425]                    # últimas execuções e totais por mês
    python main.py --concluir [--commit] [--mr|--sem-mr] [--redmine] [--status [NOME]] [--horas H [--atividade NOME]]
                   --repo /caminho/do/repo --card 1425 ["..."]   # fecha o card
"""

from __future__ import annotations

import argparse
import logging
import sys

from core.conclusion import ConclusionOptions, conclude, format_conclusion
from core.exceptions import MissingInputError, PipelineError
from core.history import History, format_history
from core.logging_config import setup_logging
from core.pipeline import MISSING_REQUEST, Pipeline, format_report
from core.settings import load_settings
from core.validator import validate_input
from integration.redmine import redmine_configured

EXIT_OK = 0
EXIT_INVALID_INPUT = 2
EXIT_PIPELINE_ERROR = 1

# Opções que só fazem sentido na conclusão do card.
CONCLUSION_FLAGS = {"commit": "--commit", "mr": "--mr/--sem-mr", "redmine": "--redmine", "status": "--status",
                    "horas": "--horas", "atividade": "--atividade"}
STATUS_FROM_SETTINGS = "__padrao__"


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
    mr = parser.add_mutually_exclusive_group()
    mr.add_argument("--mr", dest="mr", action="store_true", default=None,
                    help="Com --concluir, envia o branch e abre o merge request no GitLab")
    mr.add_argument("--sem-mr", dest="mr", action="store_false",
                    help="Com --concluir, não abre merge request (padrão: gitlab.merge_request_default)")
    parser.add_argument(
        "--redmine", action="store_true", help="Com --concluir, anota o documento do card na tarefa do Redmine"
    )
    parser.add_argument(
        "--status", nargs="?", const=STATUS_FROM_SETTINGS, metavar="NOME",
        help="Com --concluir, muda o status da tarefa no Redmine (sem NOME: redmine.conclusion_status)",
    )
    parser.add_argument("--horas", type=float, metavar="H", help="Com --concluir, lança H horas na tarefa do Redmine")
    parser.add_argument("--atividade", metavar="NOME",
                        help="Atividade das horas (padrão: redmine.time_entry_activity)")
    parser.add_argument("--historico", nargs="?", type=int, const=30, metavar="N",
                        help="Mostra as N últimas execuções (padrão 30) e os totais por mês; filtra por --card")
    return parser.parse_args(argv)


def show_history(limit: int, card: str | None) -> int:
    try:
        settings = load_settings()
    except PipelineError as exc:
        print(f"❌ {exc}", file=sys.stderr)
        return EXIT_PIPELINE_ERROR
    history = History.from_settings(settings.history)
    if not history:
        print("❌ Histórico desativado (history.enabled: false no settings.yaml).", file=sys.stderr)
        return EXIT_INVALID_INPUT
    print(format_history(history.recent(limit, card=card), [] if card else history.monthly_summary()))
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.historico is not None:
        return show_history(args.historico, args.card)
    for dest, flag in CONCLUSION_FLAGS.items():
        value = getattr(args, dest)
        # --mr/--sem-mr: None = não informado (False é um --sem-mr explícito); nas demais, False também é "não".
        informed = value is not None if dest == "mr" else value not in (None, False)
        if informed and not args.concluir:
            print(f"❌ {flag} só vale junto com --concluir.", file=sys.stderr)
            return EXIT_INVALID_INPUT
    if args.horas is not None and args.horas <= 0:
        print("❌ --horas deve ser maior que zero.", file=sys.stderr)
        return EXIT_INVALID_INPUT
    if args.atividade and args.horas is None:
        print("❌ --atividade só vale junto com --horas.", file=sys.stderr)
        return EXIT_INVALID_INPUT
    if args.concluir and args.implementar:
        print("❌ Use --implementar ou --concluir, não os dois.", file=sys.stderr)
        return EXIT_INVALID_INPUT

    # Guard clause: nada é executado sem repositório e card. A descrição pode vir da tarefa no Redmine.
    try:
        data = validate_input(" ".join(args.message), repo=args.repo, card=args.card, require_request=False)
    except MissingInputError as exc:
        print(f"❌ {exc}", file=sys.stderr)
        return EXIT_INVALID_INPUT

    try:
        settings = load_settings()
        if not args.concluir and not data.request and not redmine_configured(settings.redmine):
            print(f"❌ {MISSING_REQUEST}", file=sys.stderr)
            return EXIT_INVALID_INPUT
        setup_logging(settings.logging)
        if args.concluir:
            status = settings.redmine.conclusion_status if args.status == STATUS_FROM_SETTINGS else args.status
            options = ConclusionOptions(
                commit=args.commit,
                merge_request=settings.gitlab.merge_request_default if args.mr is None else args.mr,
                redmine_note=args.redmine,
                redmine_status=status,
                hours=args.horas,
                activity=args.atividade,
            )
            conclusion = conclude(
                settings, data.repo_path, data.card_number, data.request, options,
                history=History.from_settings(settings.history),
            )
            print(format_conclusion(conclusion))
            return EXIT_PIPELINE_ERROR if conclusion.errors else EXIT_OK
        report = Pipeline(
            settings,
            on_progress=lambda message: print(f"  › {message}", file=sys.stderr),
            history=History.from_settings(settings.history),
        ).run(data, implement=args.implementar)
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
