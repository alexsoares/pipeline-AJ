# Prova de conceito: card 194621 no repositório `simples`

Passo a passo para rodar o Agente AJ de ponta a ponta com dados reais e risco mínimo:

- **Card:** tarefa [#194621](https://projetos.ima.sp.gov.br/issues/194621), "Acompanhamento execução LOA 2026".
  Projeto DECOR, tipo **Dúvida**, status **Desenvolver**, atribuída a você, prazo 2026-09-30. O tipo "Dúvida" não
  está em `tracker_classification`, então a classificação vai para o LLM. A descrição é curta ("Alterações e
  esclarecimentos/acompanhamento na execução LOA 2026"); o campo "critérios de aceitação" ("Realizar atendimentos
  para execução dos decretos/oficio LOA2026") também entra na solicitação.
- **Repositório:** `~/simples` (Ginner `ajsoares/simples`), branch `master`, só com o `README.md`.

| Passo | O que faz | Toca em algo real? |
|---|---|---|
| 1 | credenciais e conferência | não |
| 2 | lê a tarefa, classifica, cria o branch | Redmine **só leitura**; API Anthropic (centavos) |
| 3 | implementa com o Claude Code | só `~/simples` local; alguns centavos |
| 4 | documento + commit | só `~/simples` local |
| 5 | **opcional:** push + merge request | **escreve** no projeto pessoal `ajsoares/simples` |
| 6 | **opcional:** nota na tarefa | **escreve** na tarefa 194621 (visível para quem acompanha, inclusive o cliente) |

> **Cuidado com `--status` e `--horas` na 194621.** Diferente da 193056, esta tarefa está **ativa** e é sua:
> `--status` a moveria de Desenvolver para **Homologar** (o que, pelo histórico, sinaliza ao cliente que está pronta
> para conferência) e `--horas` lançaria tempo de verdade nela (hoje 34,9 h de 50 h estimadas). Só use se o lançamento
> corresponder a trabalho real no atendimento; para a prova de conceito, deixe os dois de fora.

Nesta máquina o comando é `python3` (não existe `python`).

---

## 1. Credenciais e conferência

O `.env` já existe com `ANTHROPIC_API_KEY`. Preencha o que falta:

```
ANTHROPIC_API_KEY=sk-ant-...            # já preenchido; obrigatório: "Dúvida" é classificada pelo LLM
REDMINE_URL=https://projetos.ima.sp.gov.br
REDMINE_API_KEY=<Minha conta → Chave de acesso à API>
GITLAB_URL=https://ginner.ima.sp.gov.br
GITLAB_TOKEN=                           # deixe vazio até o passo 5
```

Sem `REDMINE_URL`/`REDMINE_API_KEY` a pipeline pula o passo 2 e exige a descrição digitada: o objetivo aqui é
justamente ler a tarefa real.

Confira:

```bash
cd ~/pipeline-aj
python3 -m pytest -q                    # 209 passed
claude --version                        # Claude Code instalado e autenticado
git -C ~/simples status -sb             # "## master...origin/master", sem alterações
```

## 2. Ler a tarefa, classificar e criar o branch (sem implementar)

A descrição vem da tarefa. O texto entre aspas entra como "Observações do usuário" e serve para **limitar** o que o
Claude Code fará no passo 3 (o sistema ELO não está no `simples`; não há relatório 0020 nem RCL para corrigir):

```bash
python3 main.py --repo ~/simples --card 194621 \
  "Prova de conceito: apenas documente no README.md, em uma seção 'Card 194621', os atendimentos da execução da LOA 2026 (receitas com mesma conta e nomes diferentes nos ofícios, PT de despesa como projeto em vez de atividade, dotação sem função no PPA 2026, duplicação de receitas indiretas no relatório 0020, nova dedução na RCL). Não crie outros arquivos."
```

**Confira no relatório:**
- `Tarefa : #194621 Acompanhamento execução LOA 2026` (Dúvida, Desenvolver, link);
- `Classificação : <feature|hotfix|release> (LLM)`, com tokens gastos (poucas centenas);
- `Branch : <classificação>/card-194621 (criado a partir de origin/master)`;
- passo 5 **pulado**, com a orientação de abrir o Claude Code manualmente.

```bash
git -C ~/simples branch                 # * <classificação>/card-194621
```

Abra a tarefa no Redmine: **nada mudou** no histórico.

> O Claude Code só recebe o texto da tarefa (título, descrição, critérios de aceitação), não o histórico de notas.
> Por isso os atendimentos estão resumidos nas observações acima.

## 3. Implementar com o Claude Code

Como o branch atual não é protegido, a pipeline **mantém** o branch do passo 2 e só roda a implementação nele:

```bash
python3 main.py --implementar --repo ~/simples --card 194621 \
  "Prova de conceito: apenas documente no README.md, em uma seção 'Card 194621', os atendimentos da execução da LOA 2026 (receitas com mesma conta e nomes diferentes nos ofícios, PT de despesa como projeto em vez de atividade, dotação sem função no PPA 2026, duplicação de receitas indiretas no relatório 0020, nova dedução na RCL). Não crie outros arquivos."
```

> **Traga o histórico real nas observações.** Com o texto curto acima, o Claude Code preencheu os itens com
> orientações genéricas (e erradas, como a causa da duplicação no relatório 0020), porque as notas da tarefa não
> chegam a ele. Na execução validada, as observações listaram cada atendimento com data, situação, causa e solução
> tiradas do histórico da 194621, e pediram "use somente estas informações (não invente causas nem orientações)".
> Texto longo cabe melhor num arquivo: `python3 main.py --implementar --repo ~/simples --card 194621 "$(cat obs.txt)"`.

**Confira:**
- linhas de progresso `› ...` enquanto roda;
- no fim: `Resumo do Claude Code`, `Arquivos alterados` com ` M README.md`, e o custo em US$;
- `git -C ~/simples diff` mostra a seção nova no README, com o título exato pedido;
- **sem commit** do Claude Code: `git -C ~/simples log --oneline` continua só com `add README`.

Se não gostar do resultado: `git -C ~/simples checkout -- README.md` e rode o passo 3 de novo.

## 4. Concluir localmente: documento + commit

```bash
python3 main.py --concluir --commit --repo ~/simples --card 194621
cat ~/simples/CARD-194621.md
git -C ~/simples log --oneline -2
git -C ~/simples status -s              # vazio
```

**Confira:**
- o `CARD-194621.md` traz a solicitação (tarefa + critérios de aceitação), o `README.md` como alterado e o resumo do
  diff. As observações do passo 3 **não** entram: a conclusão só usa o texto passado no próprio `--concluir` (e o
  resumo do Claude Code só entra pela web). Para registrá-las, passe o texto também aqui, por exemplo
  `python3 main.py --concluir --commit --repo ~/simples --card 194621 "$(cat obs.txt)"`;
- o commit é `<classificação>(card-194621): Acompanhamento execução LOA 2026`;
- no relatório, MR, nota, status e horas aparecem como "não solicitado".

Até aqui nada saiu da sua máquina (fora a leitura da tarefa e as chamadas de LLM).

## 5. Opcional: push e merge request em `ajsoares/simples`

Crie um token no Ginner (**Preferências → Tokens de acesso**, escopo `api`) e preencha `GITLAB_TOKEN` no `.env`.

```bash
python3 main.py --concluir --commit --mr --repo ~/simples --card 194621
```

O `--commit` vai junto porque o documento tem data e hora: cada conclusão o regrava, e o MR exige working tree limpo.
Isso gera um segundo commit, só com o `CARD-194621.md` atualizado.

**Confira:** o link do MR no relatório; no Ginner, o MR `<classificação>/card-194621 → master` com o documento na
descrição. Rodar o mesmo comando de novo deve **reaproveitar** o MR (sem abrir outro).

## 6. Opcional: nota na tarefa 194621

Só se você aceitar que apareça uma nota na tarefa real. O cliente (Sergio, responsável PMC) e os observadores podem
ser notificados, e a nota falará de um README de prova de conceito, não do ELO:

```bash
python3 main.py --concluir --commit --redmine --repo ~/simples --card 194621
```

**Confira no Redmine:** a nota com o documento em Markdown e, se o passo 5 foi feito, o link do MR. O status continua
**Desenvolver** e as horas não mudam (sem `--status` e sem `--horas`).

## 7. Pela interface web (alternativa aos passos 2 a 4)

Faça antes o "Desfazer" abaixo, para começar de novo do `master` sem o branch do card.

```bash
python3 -m web.app                      # http://127.0.0.1:8000
```

Repositório `/home/alexandre/simples`, card `194621`, as mesmas observações, **com** "Implementar". Acompanhe o
progresso e, ao terminar, use **Concluir card** marcando só "Fazer commit" (deixe **desmarcados** status e horas).
Em **Histórico**, veja todas as execuções desta prova de conceito (ou na CLI:
`python3 main.py --historico --card 194621`).

> Se o servidor já estava rodando antes de você preencher o `.env`, reinicie-o: o `.env` é lido na subida.

---

## Desfazer

```bash
git -C ~/simples checkout master
git -C ~/simples branch -D <classificação>/card-194621
```

Se fez o passo 5: feche o MR e apague o branch no Ginner. Se fez o passo 6: edite ou apague a nota na tarefa
(se tiver permissão).
