# Roteiro de validação segura

Este roteiro valida o Agente AJ contra os serviços **reais**: API da Anthropic, Claude Code, Redmine e GitLab.
A ideia é subir o risco aos poucos. Cada etapa só é feita depois que a anterior deu certo, e diz exatamente o que
ela toca.

| Etapa | O que valida | Toca em algo real? | Custo |
|---|---|---|---|
| 0 | instalação e testes automáticos | não | zero |
| 1 | repositório descartável | só `/tmp` | zero |
| 2 | classificação (API Anthropic) e branch | só `/tmp` | frações de centavo |
| 3 | implementação com o Claude Code real | só `/tmp` | alguns centavos |
| 4 | conclusão local (documento e commit) | só `/tmp` | zero |
| 5 | interface web (etapas 2 a 4 pela tela) | só `/tmp` | como nas etapas 2 e 3 |
| 6 | leitura da tarefa no Redmine | **só leitura** no Redmine | zero |
| 7 | **opcional:** nota, status e horas no Redmine | **escreve** numa tarefa de teste | zero |
| 8 | **opcional:** merge request no Ginner | **escreve** num projeto de teste | zero |

As etapas 2 a 5 rodam com o Redmine e o GitLab **desligados** (chaves vazias no `.env`), de propósito: com o Redmine
ligado, todo número de card passa a ser buscado nele, e os cards fictícios usados nessas etapas (999, 998…) não
existem lá.

> **Regra de ouro:** até a etapa 6, nada é escrito fora do repositório descartável em `/tmp`. O Redmine só é
> alterado com `--redmine`, `--status` ou `--horas`, e o GitLab só com `--mr`. Sem essas opções, eles não são tocados.

---

## Etapa 0: instalação e testes automáticos

**Objetivo:** confirmar que o projeto está íntegro nesta máquina, antes de qualquer credencial.

```bash
cd ~/pipeline-aj
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
python -m pytest
```

**Confira:** a última linha mostra `... passed`, sem `failed`. Os testes usam substitutos de tudo: não gastam
tokens e não acessam Redmine nem GitLab.

## Etapa 1: repositório descartável

**Objetivo:** ter um repositório onde a pipeline pode criar branches e arquivos à vontade.

```bash
rm -rf /tmp/teste-aj
git init -b main /tmp/teste-aj
git -C /tmp/teste-aj commit --allow-empty -m "init"
```

**Como desfazer, a qualquer momento:** `rm -rf /tmp/teste-aj`.

## Etapa 2: classificação e criação do branch

**Objetivo:** validar a chave da Anthropic, a classificação pelo LLM e a criação do branch.
**Toca em:** só `/tmp/teste-aj`. Custa uma chamada ao modelo leve, com cerca de 200 tokens.

1. Crie o `.env` e preencha **apenas** a chave da Anthropic. Deixe `REDMINE_API_KEY` e `GITLAB_TOKEN` vazios:
   ```bash
   cp .env.example .env
   # edite .env: ANTHROPIC_API_KEY=sk-ant-...
   ```
   Com as chaves do Redmine e do GitLab vazias, esses serviços ficam desligados.
2. Rode a pipeline sem implementação:
   ```bash
   python main.py --repo /tmp/teste-aj --card 999 "Corrigir erro 500 no login em produção"
   ```

**Confira:**
- o passo 2 (Redmine) aparece como pulado;
- `Classificação : hotfix (LLM)`, ou outra classificação coerente com o texto;
- `Branch : hotfix/card-999 (criado a partir de main)`;
- `git -C /tmp/teste-aj branch` lista o branch novo.

**Se falhar:** "credencial inválida" indica a chave errada. "Limite de requisições" indica conta sem saldo.

**Volte para o main antes da próxima etapa:** `git -C /tmp/teste-aj checkout main`.

## Etapa 3: implementação com o Claude Code real

**Objetivo:** validar o Claude Code em modo headless, o progresso ao vivo e a lista de arquivos alterados.
**Toca em:** só `/tmp/teste-aj`. O modo `acceptEdits` do `settings.yaml` deixa o Claude Code **apenas editar
arquivos**, sem rodar comandos. Custo: alguns centavos, que aparecem no relatório.

Pré-requisito: o `claude` instalado e autenticado. Confira com `claude --version`.

```bash
python main.py --implementar --repo /tmp/teste-aj --card 998 \
  "Crie um arquivo soma.py com uma função soma(a, b) que retorna a soma, com docstring"
```

**Confira:**
- durante a execução, aparecem linhas `› Claude Code iniciado`, `› Criando soma.py`, e assim por diante;
- no fim: `Resumo do Claude Code`, `Arquivos alterados` com `?? soma.py`, e `Custo Claude Code : US$ ...`;
- `cat /tmp/teste-aj/soma.py` mostra o arquivo criado;
- o Claude Code **não** fez commit: `git -C /tmp/teste-aj log --oneline` continua só com o `init`.

**Teste também o cancelamento:** rode de novo com outro card e aperte **Ctrl+C** no meio. A pipeline deve sair na
hora com "interrompida pelo usuário" (código 130), e `pgrep -f "claude -p"` não deve mostrar nenhum processo.

## Etapa 4: conclusão local (documento e commit)

**Objetivo:** validar o `CARD-<n>.md` e o commit padronizado.
**Toca em:** só `/tmp/teste-aj`. Sem `--redmine`, `--status`, `--horas` ou `--mr`, nada externo é alterado.

Continuando no branch do card 998, criado na etapa 3:

```bash
python main.py --concluir --repo /tmp/teste-aj --card 998            # só o documento
cat /tmp/teste-aj/CARD-998.md

python main.py --concluir --commit --repo /tmp/teste-aj --card 998   # documento + commit
git -C /tmp/teste-aj log --oneline -2
```

**Confira:**
- o `CARD-998.md` tem a solicitação, os arquivos alterados e o resumo do diff;
- o commit se chama `<classificação>(card-998): ...`, e `git -C /tmp/teste-aj status` está limpo;
- no relatório, MR, nota, status e horas aparecem como "não solicitado".

**Veja o histórico:** `python main.py --historico`. Todas as execuções das etapas 2 a 4 devem aparecer, com tokens e
custo.

## Etapa 5: interface web

**Objetivo:** ver a tela e repetir as etapas 2 a 4 por ela.

```bash
git -C /tmp/teste-aj checkout main
python -m web.app          # abra http://127.0.0.1:8000
```

**Confira na tela:**
1. Repositório `/tmp/teste-aj`, card `997`, descrição curta, **sem** "Implementar": os passos 2 e 5 aparecem
   riscados (pulados).
2. De novo, com card `996` e **com** "Implementar": o quadro de progresso mostra o que o Claude Code faz.
3. Durante uma implementação, teste o botão **Cancelar**: o passo 5 fica cancelado (■).
4. Numa execução concluída, abra **Concluir card**, marque só "Fazer commit" e clique. Confira o documento e o commit.
5. Abra **Histórico** no topo: a tabela e os totais por mês.

## Etapa 6: leitura da tarefa no Redmine (só leitura)

**Objetivo:** validar a chave do Redmine, a leitura da tarefa e a classificação pelo tipo.
**Toca em:** no Redmine, **só leitura** (`GET`); no disco, só `/tmp/teste-aj`.

1. No Redmine: **Minha conta → Chave de acesso à API → Mostrar**. Copie a chave para o `.env`:
   ```
   REDMINE_URL=https://projetos.ima.sp.gov.br
   REDMINE_API_KEY=<sua chave>
   ```
2. Escolha uma tarefa sua **já finalizada**, por exemplo a `26899`. Ela só será lida.
3. Rode **sem descrição**, porque ela vem da tarefa:
   ```bash
   python main.py --repo /tmp/teste-aj --card 26899
   ```
4. Teste também uma tarefa de tipo mapeado (Evolução ou Nova funcionalidade), para ver a classificação sem LLM:
   ```bash
   git -C /tmp/teste-aj checkout main
   python main.py --repo /tmp/teste-aj --card <número de uma Evolução sua>
   ```

**Confira:**
- a linha `Tarefa : #26899 ...` com título, tipo, status, projeto e link;
- para "Correção": `(LLM)`; para "Evolução": `feature (pelo tipo 'Evolução' no Redmine)` e `Tokens totais : 0`;
- abra a tarefa no Redmine: **nada mudou** no histórico dela.

**Se falhar:** "HTTP 401/403" indica chave errada ou API REST desabilitada no Redmine (fale com o administrador).
"Tarefa não encontrada" indica número errado ou tarefa sem acesso para você.

**Volte para o main:** `git -C /tmp/teste-aj checkout main`.

> **A partir daqui, o Redmine está ligado:** todo `--card` passa a ser buscado no Redmine, então use só números de
> tarefas reais (a leitura continua inofensiva). Para desligar o Redmine num comando isolado, sem mexer no `.env`,
> prefixe com a variável vazia, porque o shell tem precedência sobre o `.env`:
> `REDMINE_API_KEY= python main.py --repo /tmp/teste-aj --card 999 "..."`.

---

## Etapa 7 (opcional): escrever no Redmine

**Objetivo:** validar a nota, o status e as horas.
**Toca em:** **escreve** numa tarefa real, e isso fica visível para quem acompanha a tarefa.

**Antes:** crie no Redmine uma tarefa **sua, só para teste**, por exemplo "Teste do Agente AJ", num projeto em que
você possa editar e lançar horas. Anote o número dela. Nunca use uma tarefa em andamento de outra pessoa.

```bash
git -C /tmp/teste-aj checkout main
python main.py --repo /tmp/teste-aj --card <tarefa de teste>
python main.py --concluir --commit --redmine --repo /tmp/teste-aj --card <tarefa de teste>
```

Depois, em execuções separadas, para ver o efeito de cada opção:

```bash
python main.py --concluir --status --repo /tmp/teste-aj --card <tarefa de teste>       # status → Homologar
python main.py --concluir --horas 0.25 --repo /tmp/teste-aj --card <tarefa de teste>   # 15 min de Codificação
```

**Confira no Redmine:** a nota com o documento, formatada em Markdown; o status novo; o lançamento de 0,25 h.
Se o fluxo de trabalho não permitir o status, a pipeline avisa "não foi aplicado", e isso também é um resultado válido.

**Como desfazer:** apague ou edite a nota (se tiver permissão), volte o status manualmente e apague o lançamento em
**Tempo gasto**.

## Etapa 8 (opcional): merge request no Ginner

**Objetivo:** validar o push e a abertura do MR.
**Toca em:** **escreve** num projeto do Ginner.

**Antes:** crie no Ginner um **projeto pessoal de teste**, vazio, e um token em **Preferências → Tokens de acesso**
com escopo `api`. Preencha no `.env`:
```
GITLAB_URL=https://ginner.ima.sp.gov.br
GITLAB_TOKEN=<token>
```

```bash
rm -rf /tmp/teste-mr
git clone git@ginner.ima.sp.gov.br:<seu-usuario>/<projeto-de-teste>.git /tmp/teste-mr
git -C /tmp/teste-mr commit --allow-empty -m "init" && git -C /tmp/teste-mr push -u origin main

# use a mesma tarefa de teste da etapa 7 (com o Redmine ligado, o card precisa existir)
python main.py --repo /tmp/teste-mr --card <tarefa de teste> "Adicionar README de teste"
echo "# Teste" > /tmp/teste-mr/README.md
python main.py --concluir --commit --mr --repo /tmp/teste-mr --card <tarefa de teste>
```

**Confira:** o link do MR no relatório; no Ginner, o MR de `<classificação>/card-<n>` para `main`, com o `CARD-<n>.md` como
descrição. Rodar a última linha de novo deve **reaproveitar** o mesmo MR, sem abrir outro.

**Como desfazer:** feche o MR, apague o branch no Ginner e apague o projeto de teste.

---

## Limpeza geral

```bash
rm -rf /tmp/teste-aj /tmp/teste-mr
rm -f data/history.db        # opcional: apaga o histórico gerado pelos testes
```

Mantenha o `.env`: ele não é versionado (está no `.gitignore`).
