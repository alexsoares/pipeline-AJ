# pipeline-AJ

Pipeline do Agente AJ. A partir de um card (repositório + número; a descrição é opcional com o Redmine), ela:

1. **Status do Git**: confirma que o caminho é um repositório e lista as alterações pendentes.
2. **Tarefa no Redmine**: lê a tarefa de mesmo número do card (título, descrição e critérios de aceitação), que vira
   a solicitação; o que você digitar entra como observação. Sem Redmine configurado, o passo é pulado.
3. **Classificação**: `feature`, `hotfix` ou `release`. Pelo tipo da tarefa no Redmine quando ele é inequívoco
   (ex.: Nova funcionalidade → feature, Incidente → hotfix), sem gastar tokens; nos demais casos, por um LLM leve.
4. **Branch**: se você estiver em `main`/`master`, cria (ou reaproveita) o branch `<classificação>/card-<n>` a partir
   da base da classificação (`git.base_branches`), atualizada do remoto com `git fetch` (`origin/<base>`). Sem rede,
   usa a base local e avisa.
5. **Implementação (opcional)**: roda o Claude Code em modo headless no branch do card, mostrando ao vivo o que ele
   está fazendo (arquivos lidos e editados, comandos). Desligada, o passo é pulado e a pipeline só avisa para você
   abrir o Claude Code.
6. **Resposta consolidada**: classificação, branch, tempo e tokens; com implementação, também o resumo do Claude Code,
   os arquivos alterados e o custo.

Depois da implementação, feita pelo passo 5 ou por você no Claude Code, **conclua o card**: a pipeline confere que
o repositório está no branch do card, lista tudo o que mudou desde a base do branch e gera o `CARD-<n>.md`. Depois,
só o que você escolher (cada item é sim/não):

| Opção | CLI | O que faz |
|---|---|---|
| Commit | `--commit` | `git add -A` + commit `<classificação>(card-<n>): <título da tarefa>` |
| Merge request | `--mr` / `--sem-mr` | envia o branch e abre o MR no Ginner com o documento como descrição (padrão em `gitlab.merge_request_default`) |
| Nota no Redmine | `--redmine` | anota o mesmo texto do documento na tarefa (com o link do MR, se houver) |
| Status no Redmine | `--status [NOME]` | muda o status da tarefa (sem nome: `redmine.conclusion_status`, ex.: Homologar) |
| Horas no Redmine | `--horas H [--atividade NOME]` | lança H horas na tarefa (atividade padrão: `redmine.time_entry_activity`) |

Documento e commit vêm primeiro; se o merge request ou o Redmine falharem, o que já foi feito é mantido e a falha
aparece no resultado (a CLI sai com código 1).

Todas as execuções e conclusões ficam no **histórico** (SQLite em `data/history.db`): `python main.py --historico`
ou o painel "Histórico" da web mostram as últimas execuções e os totais de tokens e custo por mês.

A escolha é feita a cada execução. Com a implementação ligada, o repositório precisa estar sem alterações pendentes
(faça commit ou stash antes), assim os arquivos listados no final são só os que o Claude Code alterou. O Claude Code
nunca faz commit, push nem troca de branch: a revisão e o commit ficam com você.

## Requisitos

- Python 3.10+
- Git no `PATH`
- Chave da API da Anthropic (usada pelo classificador quando o tipo da tarefa não define a classificação)
- [Claude Code](https://claude.com/claude-code) instalado e autenticado, apenas para usar a implementação (passo 5)
- Opcionais: chave de API do Redmine (ler/atualizar tarefas) e token do GitLab/Ginner (merge request)

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

# com o Redmine configurado, basta o número: a descrição vem da tarefa
python main.py --repo /caminho/do/repo --card 1425

# ou com repositório e card embutidos na mensagem
python main.py "repo: /caminho/do/repo card: 1425 Adicionar endpoint de health check"

# prepara o branch e já implementa com o Claude Code
python main.py --implementar --repo /caminho/do/repo --card 1425 "Adicionar endpoint de health check"

# conclui o card: commit, merge request, nota e status "Homologar" no Redmine, 2 h lançadas
python main.py --concluir --commit --mr --redmine --status --horas 2 --repo /caminho/do/repo --card 1425

# histórico: últimas execuções e totais por mês (ou só de um card)
python main.py --historico
python main.py --historico 10 --card 1425
```

Sem repositório **e** número do card, a pipeline não executa nada. Sem Redmine configurado, a descrição também é
obrigatória.

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

Mostra o progresso de cada passo em tempo real e, durante a implementação, o que o Claude Code está fazendo. A opção **Implementar com o Claude Code** decide se o passo 5
roda; a escolha fica lembrada no navegador. Cada execução tem um botão **Cancelar**, que encerra o Claude Code na
hora (as alterações parciais ficam no branch para você revisar ou descartar). Execuções concluídas com sucesso
mostram **Concluir card**, com as mesmas opções da CLI (commit, merge request, nota, status e horas). O painel
**Histórico**, no topo, lista as execuções e os totais por mês.

Execuções em repositórios diferentes rodam em paralelo; no mesmo repositório, só uma por vez. Por padrão o servidor
escuta só em `127.0.0.1`, pois executa Git e o Claude Code na máquina local.

## Configuração

`config/settings.yaml`:

| Seção | O que controla |
|---|---|
| `git` | branches protegidos, base por classificação (`base_branches`), remoto, `fetch` e timeouts |
| `classifier` | modelo, `max_tokens` e timeout do classificador |
| `claude_code` | executável, modelo, modo de permissão e timeout do Claude Code (passo 5) |
| `documentation` | nome do documento gerado na conclusão (padrão `CARD-{card}.md`) |
| `redmine` | URL, timeout, SSL, mapeamento tipo → classificação, campos lidos, status e atividade padrão da conclusão |
| `gitlab` | URL, timeout, SSL, se o merge request vem marcado por padrão e se apaga o branch após o merge |
| `history` | liga/desliga o histórico e o caminho do arquivo SQLite |
| `logging` | nível e arquivo de log (padrão `logs/pipeline.log`) |

### Redmine

Para ler e anotar as tarefas, defina no `.env`:

```bash
REDMINE_URL=https://projetos.ima.sp.gov.br
REDMINE_API_KEY=<sua chave>   # Redmine → Minha conta → Chave de acesso à API
```

A mudança de status respeita o fluxo de trabalho do Redmine: se o seu usuário não puder fazer aquela transição, a
pipeline avisa (o Redmine costuma ignorar a mudança em silêncio). O lançamento de horas exige permissão de "lançar
horas" no projeto.

O mapeamento de tipos em `redmine.tracker_classification` vem só com tipos inequívocos. **Correção** fica de fora
de propósito, porque é usada tanto para bug urgente quanto para ajuste simples; essas tarefas são classificadas pelo
LLM. Acrescente ou remova tipos conforme o uso da sua equipe.

A API REST precisa estar habilitada no Redmine (Administração → Configurações → API), e o usuário da chave precisa
poder editar a tarefa. Se a anotação falhar, o documento e o commit já feitos são mantidos e o erro aparece no
resultado; basta concluir de novo com a opção do Redmine.

### GitLab (Ginner)

Para abrir o merge request na conclusão, defina no `.env`:

```bash
GITLAB_URL=https://ginner.ima.sp.gov.br
GITLAB_TOKEN=<token pessoal com escopo "api">   # Ginner → Preferências → Tokens de acesso
```

O projeto é identificado pelo remoto `origin` do repositório, que precisa apontar para o mesmo servidor do
`GITLAB_URL`. O push usa as suas credenciais do Git (SSH ou HTTPS já configurados). O MR só é aberto sem alterações
pendentes (marque o commit) e com ao menos um commit além da base; se já existir um MR aberto para o branch, ele é
reaproveitado.

## Validação com os serviços reais

Antes de usar em cards de verdade, siga o [roteiro de validação segura](docs/ROTEIRO-VALIDACAO.md): ele sobe o
risco aos poucos, começando num repositório descartável, com o Redmine só em leitura e as escritas externas
(Redmine e Ginner) como etapas opcionais em tarefa e projeto de teste.

## Testes

```bash
pip install -r requirements-dev.txt
python -m pytest
```

Os testes usam repositórios Git temporários (inclusive um remoto local para fetch e push) e substitutos do
classificador, do Claude Code, do Redmine e do GitLab: não gastam tokens nem mexem em repositórios, tarefas ou
projetos reais.

## Estrutura

```
main.py              CLI
web/                 interface web (Flask + página estática)
core/                validação, Git, classificador, orquestração dos passos, conclusão do card, histórico
integration/         Claude Code headless (passo 5), Redmine e GitLab
config/settings.yaml configuração
tests/               testes (pytest)
```
