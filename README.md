# pipeline-AJ

Pipeline do Agente AJ: recebe um card (repositório + número + descrição), verifica o status do Git,
classifica a solicitação (feature, hotfix ou release) com um LLM e prepara o branch `<tipo>/card-<n>`.

## Instalação

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # preencha a chave da API
```

## Uso

```bash
# CLI
python main.py --repo /caminho/do/repo --card 1425 "Adicionar endpoint de health check"
python main.py "repo: /caminho/do/repo card: 1425 Adicionar endpoint de health check"

# Interface web (http://127.0.0.1:8000)
python -m web.app
```

Configurações em `config/settings.yaml`; credenciais apenas no `.env` (não versionado).
