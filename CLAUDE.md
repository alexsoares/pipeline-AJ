# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Visão geral

Pipeline do "Agente AJ": recebe um card (caminho do repositório + número do card + descrição opcional), roda o
`git status`, lê a tarefa de mesmo número no Redmine (se configurado), classifica a solicitação como `feature` /
`hotfix` / `release` (pelo tipo da tarefa ou por um LLM) e prepara o branch `<classificação>/card-<n>`.
A implementação (passo 5) é opcional e escolhida a cada execução (`--implementar` na CLI, checkbox na web): ou a pipeline
roda o Claude Code em modo headless no branch, ou é pulada e o relatório orienta abrir o Claude Code manualmente.
Nos dois casos o usuário volta depois pelo modo **concluir** (`--concluir [--commit]`, botão na web), que gera o
`CARD-<n>.md` com as alterações desde a base do branch e, opcionalmente, faz o commit padronizado e anota o mesmo
texto na tarefa `<n>` do Redmine (o número do card é o número da tarefa).
Todo o código, os comentários e as mensagens para o usuário são em português.

## Comandos

```bash
pip install -r requirements-dev.txt       # dependências + pytest
cp .env.example .env                      # credenciais (ANTHROPIC_API_KEY)

python -m pytest                                          # todos os testes
python -m pytest tests/test_pipeline.py -k cancelado      # um arquivo / filtro por nome

python main.py --repo /caminho/repo --card 1425 "descrição"        # CLI
python main.py --repo /caminho/repo --card 1425                     # descrição vem da tarefa no Redmine
python main.py "repo: /caminho/repo card: 1425 descrição"           # repo/card embutidos na mensagem
python main.py --implementar --repo /caminho/repo --card 1425 "…"  # também implementa via Claude Code
python main.py --concluir --commit --redmine --repo /caminho/repo --card 1425  # fecha o card (descrição opcional)
python -m web.app [--port 9000]                                     # UI web em 127.0.0.1:8000
```

Não há lint nem build. Os testes nunca chamam a API nem o Claude Code reais: `tests/conftest.py` fornece repositórios
Git descartáveis (`make_repo`/`repo`), `FakeClassifier` e `fake_claude`, que gera um executável imitando
`claude -p --output-format json` (pode criar arquivo, demorar com processo filho e registrar os argumentos recebidos).
Pipeline, web e CLI recebem o classificador falso via `monkeypatch` em `core.pipeline.RequestClassifier` (e o
`FakeRedmine` em `core.pipeline.RedmineClient`); os testes de cancelamento e timeout conferem que o processo filho
morreu. Uma fixture autouse apaga `REDMINE_URL`/`REDMINE_API_KEY`: quem precisa do Redmine "configurado" usa
`redmine_env` e injeta um substituto, então nenhum teste fala com o Redmine real.

Códigos de saída da CLI: `0` ok, `2` entrada inválida, `1` falha da pipeline, `130` interrompida.

## Arquitetura

- **Dois pontos de entrada, um núcleo.** `main.py` (CLI) e `web/app.py` (Flask) chamam
  `core.validator.validate_input` → `core.settings.load_settings` → `core.pipeline.Pipeline.run`. Toda regra de negócio
  fica em `core/`; os pontos de entrada só convertem o resultado (`format_report` para texto, `report_to_dict` para JSON).
- **Passos numerados.** `STEPS` em `core/pipeline.py` é a fonte única dos nomes dos passos (também servida em
  `/api/steps` para a UI). Cada passo roda via `Pipeline._step`, que dispara o callback `on_step(n, "running"|"done"|"failed")`
  e embrulha qualquer `PipelineError` em `StepFailed` (com o número do passo). Passos 2 (Redmine) e 5 (implementação)
  notificam `"skipped"` quando o Redmine não está configurado / `implement=False`. Use as constantes `REDMINE_STEP`,
  `IMPLEMENT_STEP` e `FINAL_STEP` em vez de números soltos. `Pipeline.cancel()` encerra o Claude Code e impede os passos seguintes: o passo atingido fica
  `"cancelled"` e `PipelineCancelled` sobe **sem** ser embrulhado em `StepFailed`. Ao adicionar um passo, registre-o em `STEPS` e no fallback da UI (`loadSteps`).
- **Erros.** Todo erro esperado é subclasse de `PipelineError` (`core/exceptions.py`), com mensagem legível para o chat.
  Os pontos de entrada nunca exibem traceback: exceções inesperadas vão para o log (`logs/pipeline.log`).
- **Guard clause.** Nada roda sem repositório existente e card numérico (`MissingInputError`). A descrição só é
  obrigatória sem Redmine configurado (`MISSING_REQUEST`, checado na CLI, na web e no início de `Pipeline.run`).
- **Solicitação efetiva.** `compose_request(issue, texto)` (`core/models.py`): o texto da tarefa (título, descrição e
  os campos de `redmine.custom_fields`) com o que o usuário digitou como "Observações do usuário". É ela, e não
  `PipelineInput.request`, que vai para o classificador, o Claude Code e o documento (`PipelineReport.request`).
- **Branch.** `GitManager.ensure_work_branch` só cria/troca de branch se o branch atual estiver em
  `git.protected_branches` (main/master); fora deles, mantém o branch atual.
- **Web.** `JobManager` roda cada pipeline numa thread e bloqueia **por repositório** (chave = `git rev-parse
  --show-toplevel`): no mesmo repo, HTTP 409, porque execuções simultâneas trocariam de branch; repos diferentes rodam
  em paralelo. `POST /api/runs/<id>/cancel` chama `Pipeline.cancel()`. A UI (`web/static/index.html`, arquivo único)
  acompanha cada execução por polling em `GET /api/runs/<id>`, sem bloquear novos envios. Os jobs ficam só em memória.
- **Configuração.** `config/settings.yaml` é carregado em dataclasses congeladas (`core/settings.py`); chaves desconhecidas
  geram `ConfigError`, então um campo novo no YAML exige um campo novo na dataclass. `load_settings()` também carrega o
  `.env` da raiz (`override=False`: variáveis do shell têm precedência).
- **Implementação** (`integration/claude_runner.py`): `claude -p` com `--output-format json`, `--strict-mcp-config` e o
  `permission_mode` do YAML, no cwd do repo-alvo; o prompt proíbe commit, push e troca de branch. Roda via `Popen` em
  sessão própria (`start_new_session`) para que cancelamento, timeout e Ctrl+C matem o grupo de processos inteiro.
  Com implementação ligada, o passo 1 exige working tree limpo, então `report.changed_files` (o `git status` depois do
  passo 5) contém só o que o Claude Code alterou.
- **Conclusão** (`core/conclusion.py`): independente da `Pipeline`. Exige estar no branch
  `<classificação>/card-<n>` (a classificação sai do nome do branch, sem LLM), compara com o `merge-base` do primeiro
  branch protegido existente, gera o documento via `core/documentation.py` (o próprio `CARD-<n>.md` fica fora da lista) e,
  com commit, faz `git add -A` + `<classificação>(card-<n>): <1ª linha da descrição>`. Na web (`POST /api/conclusions`)
  ocupa o bloqueio do repositório e, com `job_id`, inclui o resumo do Claude Code daquela execução.
- **Redmine** (`integration/redmine.py`): REST direto via `httpx` (`GET`/`PUT /issues/<n>.json`); URL em
  `REDMINE_URL` (ou `redmine.url`) e chave só em `REDMINE_API_KEY`. Na pipeline, falha ao ler a tarefa interrompe no
  passo 2. `redmine.tracker_classification` mapeia o tipo da tarefa para a classificação (`source="redmine"`, sem
  tokens); tipos fora do mapa vão para o LLM. Na conclusão, a leitura da tarefa é tolerante (sem ela, usa o texto do
  usuário; com ela, o título vira o assunto do commit), e a anotação é o último passo e **não levanta**:
  a falha vai para `ConclusionReport.redmine_error` para não perder documento e commit já feitos (a CLI sai com 1, a web
  responde 200 com o erro no corpo). Nos testes, `RedmineClient` recebe um `httpx.MockTransport`.
- **Classificador** (`core/classifier.py`): SDK da Anthropic, modelo leve, `max_tokens` baixo; extrai o rótulo por regex
  sobre `VALID_CLASSIFICATIONS` (`core/models.py`). Os tokens consumidos entram no relatório via `TokenUsage`.
