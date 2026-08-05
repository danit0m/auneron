# Sprint 9.1 — Commit 1

Este commit centraliza a configuração, adiciona PostgreSQL com Psycopg 3,
mantém compatibilidade temporária com SQLite e adiciona Docker Compose.

## Instalação

```powershell
pip install -r requirements.txt
Copy-Item .env.example .env
docker compose up -d
docker compose ps
uvicorn app.main:app --reload
```

Valide em:

- http://127.0.0.1:8000/health
- http://127.0.0.1:8000/docs

Não apague `auneron.db`; ele será a origem da migração de dados.
