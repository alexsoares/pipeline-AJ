# pipeline-AJ

Pipeline do Agente AJ. A partir de um card (repositório + número + descrição), ela:

1. **Status do Git**: confirma que o caminho é um repositório e lista as alterações pendentes.
2. **Classificação**: um LLM leve classifica a solicitação como `feature`, `hotfix` ou `release`.
3. **Branch**: se você estiver em `main`/`master`, cria (ou reaproveita) o branch `<classificação>/card-<n>`.
4. **Resposta consolidada**: relatório com classificação, branch, tempo e tokens consumidos.

A implementação do card é feita depois, no Claude Code, já no branch preparado.

## Requisitos

- Python 3.10+
- Git no `PATH`
- Chave da API da Anthropic (usada pelo classificador)

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

Mostra o progresso de cada passo em tempo real. Roda uma execução por vez e, por padrão, escuta só em `127.0.0.1`,
pois executa Git na máquina local.

## Configuração

`config/settings.yaml`:

| Seção | O que controla |
|---|---|
| `git` | branches protegidos (onde a pipeline cria o branch do card) e timeout dos comandos |
| `classifier` | modelo, `max_tokens` e timeout do classificador |
| `logging` | nível e arquivo de log (padrão `logs/pipeline.log`) |

As seções `claude_code` e `documentation` pertencem a módulos ainda não integrados à pipeline.

## Estrutura

```
main.py              CLI
web/                 interface web (Flask + página estática)
core/                validação, Git, classificador, orquestração dos passos
integration/         execução do Claude Code em modo headless (ainda não integrada)
config/settings.yaml configuração
```
