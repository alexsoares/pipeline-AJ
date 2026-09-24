# pipeline-AJ

Pipeline do Agente AJ. A partir de um card (repositório + número + descrição), ela:

1. **Status do Git**: confirma que o caminho é um repositório e lista as alterações pendentes.
2. **Classificação**: um LLM leve classifica a solicitação como `feature`, `hotfix` ou `release`.
3. **Branch**: se você estiver em `main`/`master`, cria (ou reaproveita) o branch `<classificação>/card-<n>`.
4. **Implementação (opcional)**: roda o Claude Code em modo headless no branch do card. Desligada, o passo é pulado
   e a pipeline só avisa para você abrir o Claude Code.
5. **Resposta consolidada**: classificação, branch, tempo e tokens; com implementação, também o resumo do Claude Code,
   os arquivos alterados e o custo.

Depois da implementação, feita pelo passo 4 ou por você no Claude Code, **conclua o card**: a pipeline confere que
o repositório está no branch do card, lista tudo o que mudou desde a base do branch, gera o `CARD-<n>.md` e, se você
pedir, faz o commit `<classificação>(card-<n>): <descrição>`.

A escolha é feita a cada execução. Com a implementação ligada, o repositório precisa estar sem alterações pendentes
(faça commit ou stash antes), assim os arquivos listados no final são só os que o Claude Code alterou. O Claude Code
nunca faz commit, push nem troca de branch: a revisão e o commit ficam com você.

## Requisitos

- Python 3.10+
- Git no `PATH`
- Chave da API da Anthropic (usada pelo classificador)
- [Claude Code](https://claude.com/claude-code) instalado e autenticado, apenas para usar a implementação (passo 4)

## Instalação

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # preencha ANTHROPIC_API_KEY
```

O `.env` é carregado automaticamente e não é versionado. Variáveis já exportadas no shell têm precedência sobre ele.

## Uso

### CLI

```bash
python main.py --repo /caminho/do/repo --card 1425 "Adicionar endpoint de health check"

# ou com repositório e card embutidos na mensagem
python main.py "repo: /caminho/do/repo card: 1425 Adicionar endpoint de health check"

# prepara o branch e já implementa com o Claude Code
python main.py --implementar --repo /caminho/do/repo --card 1425 "Adicionar endpoint de health check"

# conclui o card depois da implementação (descrição opcional; --commit faz o commit)
python main.py --concluir --commit --repo /caminho/do/repo --card 1425 "Adicionar endpoint de health check"
```

Sem repositório **e** número do card, a pipeline não executa nada.

| Código de saída | Significado |
|---|---|
| `0` | sucesso |
| `1` | falha em algum passo da pipeline |
| `2` | entrada inválida (repositório/card ausente ou inválido) |
| `130` | interrompida pelo usuário |

### Interface web

```bash
python -m web.app               # http://127.0.0.1:8000
python -m web.app --port 9000
```

Mostra o progresso de cada passo em tempo real. A opção **Implementar com o Claude Code** decide se o passo 4
roda; a escolha fica lembrada no navegador. Cada execução tem um botão **Cancelar**, que encerra o Claude Code na
hora (as alterações parciais ficam no branch para você revisar ou descartar). Execuções concluídas com sucesso
mostram **Concluir card**, com a opção de fazer o commit.

Execuções em repositórios diferentes rodam em paralelo; no mesmo repositório, só uma por vez. Por padrão o servidor
escuta só em `127.0.0.1`, pois executa Git e o Claude Code na máquina local.

## Configuração

`config/settings.yaml`:

| Seção | O que controla |
|---|---|
| `git` | branches protegidos (onde a pipeline cria o branch do card) e timeout dos comandos |
| `classifier` | modelo, `max_tokens` e timeout do classificador |
| `claude_code` | executável, modelo, modo de permissão e timeout do Claude Code (passo 4) |
| `documentation` | nome do documento gerado na conclusão (padrão `CARD-{card}.md`) |
| `logging` | nível e arquivo de log (padrão `logs/pipeline.log`) |

## Testes

```bash
pip install -r requirements-dev.txt
python -m pytest
```

Os testes usam repositórios Git temporários e substitutos do classificador e do Claude Code: não gastam tokens nem
mexem em repositórios reais.

## Estrutura

```
main.py              CLI
web/                 interface web (Flask + página estática)
core/                validação, Git, classificador, orquestração dos passos, conclusão do card
integration/         execução do Claude Code em modo headless (passo 4)
config/settings.yaml configuração
tests/               testes (pytest)
```
