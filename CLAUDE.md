# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Visão geral

Pipeline do "Agente AJ": recebe um card (caminho do repositório + número do card + descrição), roda o `git status`,
classifica a solicitação como `feature` / `hotfix` / `release` com um LLM e prepara o branch `<classificação>/card-<n>`.
A implementação do card **não** faz parte da pipeline: o relatório final orienta abrir o Claude Code no branch preparado.
Todo o código, os comentários e as mensagens para o usuário são em português.

## Comandos

```bash
pip install -r requirements.txt
cp .env.example .env                      # credenciais (ANTHROPIC_API_KEY)

python main.py --repo /caminho/repo --card 1425 "descrição"        # CLI
python main.py "repo: /caminho/repo card: 1425 descrição"           # repo/card embutidos na mensagem
python -m web.app [--port 9000]                                     # UI web em 127.0.0.1:8000
```

Ainda não há testes, lint nem build. Para testar sem mexer em repositórios reais, crie um repo descartável
(`git init -b main && git commit --allow-empty -m init`) e aponte `--repo` para ele: o passo 3 faz `git checkout`
no repositório-alvo.

Códigos de saída da CLI: `0` ok, `2` entrada inválida, `1` falha da pipeline, `130` interrompida.

## Arquitetura

- **Dois pontos de entrada, um núcleo.** `main.py` (CLI) e `web/app.py` (Flask) chamam
  `core.validator.validate_input` → `core.settings.load_settings` → `core.pipeline.Pipeline.run`. Toda regra de negócio
  fica em `core/`; os pontos de entrada só convertem o resultado (`format_report` para texto, `report_to_dict` para JSON).
- **Passos numerados.** `STEPS` em `core/pipeline.py` é a fonte única dos nomes dos passos (também servida em
  `/api/steps` para a UI). Cada passo roda via `Pipeline._step`, que dispara o callback `on_step(n, "running"|"done"|"failed")`
  e embrulha qualquer `PipelineError` em `StepFailed` (com o número do passo). Ao adicionar um passo, registre-o em `STEPS`.
- **Erros.** Todo erro esperado é subclasse de `PipelineError` (`core/exceptions.py`), com mensagem legível para o chat.
  Os pontos de entrada nunca exibem traceback: exceções inesperadas vão para o log (`logs/pipeline.log`).
- **Guard clause.** Nada roda sem repositório existente, card numérico e descrição não vazia (`MissingInputError`).
- **Branch.** `GitManager.ensure_work_branch` só cria/troca de branch se o branch atual estiver em
  `git.protected_branches` (main/master); fora deles, mantém o branch atual.
- **Web.** `JobManager` executa uma pipeline por vez em thread (HTTP 409 se já houver uma ativa), porque execuções
  simultâneas trocariam de branch no mesmo repo. A UI (`web/static/index.html`, arquivo único) faz polling em
  `GET /api/runs/<id>`. Os jobs ficam só em memória.
- **Configuração.** `config/settings.yaml` é carregado em dataclasses congeladas (`core/settings.py`); chaves desconhecidas
  geram `ConfigError`, então um campo novo no YAML exige um campo novo na dataclass. `load_settings()` também carrega o
  `.env` da raiz (`override=False`: variáveis do shell têm precedência).
- **Classificador** (`core/classifier.py`): SDK da Anthropic, modelo leve, `max_tokens` baixo; extrai o rótulo por regex
  sobre `VALID_CLASSIFICATIONS` (`core/models.py`). Os tokens consumidos entram no relatório via `TokenUsage`.

## Código ainda não integrado

`integration/claude_runner.py` (roda `claude -p` em modo headless) e `core/documentation.py` (gera `CARD-<n>.md` no repo)
**não são chamados pela pipeline**, assim como as seções `claude_code` e `documentation` do `settings.yaml`. O
`permission_mode` padrão diverge: `acceptEdits` na dataclass, `auto` no YAML.
