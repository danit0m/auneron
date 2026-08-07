# Auneron

Auneron é uma aplicação web para gestão financeira, clientes, conhecimento
operacional e apoio executivo. O projeto possui backend em FastAPI,
frontend em React/Vite e persistência principal em PostgreSQL.

## Arquitetura

```text
Browser
  |
  | /api/...
  v
Frontend React
  |
  | desenvolvimento: proxy do Vite
  | produção: reverse proxy
  |             + X-API-Key no lado servidor
  v
FastAPI
  |
  v
PostgreSQL
```

O navegador não deve receber a `API_KEY` do backend. Em desenvolvimento,
o processo do Vite injeta a chave ao encaminhar `/api`. Em produção,
essa responsabilidade deve ficar em um reverse proxy ou BFF.

Documentação complementar:

- `backend/docs/ARCHITECTURE.md`
- `backend/docs/API_SECURITY.md`
- `backend/docs/ENVIRONMENT_SECURITY.md`
- `backend/docs/FRONTEND_INTEGRATION.md`
- `backend/docs/OBSERVABILITY.md`
- `backend/docs/POSTGRESQL_OPERATIONS.md`
- `backend/docs/DEPLOYMENT.md`
- `backend/docs/RELEASE_CHECKLIST.md`

## Requisitos locais

Ambiente atualmente validado pelo projeto:

- Python 3.11
- Node.js 22
- PostgreSQL 17
- npm
- Git
- Chromium do Playwright para E2E
- Docker opcional para PostgreSQL local e validação das imagens

## Backend

Na pasta `backend`:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements-dev.txt
Copy-Item .env.example .env
```

Edite `backend/.env` e configure pelo menos:

```text
APP_ENV=development
DATABASE_URL=postgresql+psycopg://auneron:<senha>@localhost:5432/auneron
API_KEY=<valor aleatório com pelo menos 32 caracteres>
```

Nunca versione `.env`.

### PostgreSQL local com Docker

O arquivo `backend/docker-compose.yml` existe apenas para
desenvolvimento local do PostgreSQL.

```powershell
docker compose up -d
docker compose ps
```

O password presente nesse compose é uma credencial de desenvolvimento
local e não deve ser reutilizado em produção.

### Migrações

```powershell
python -m alembic upgrade head
python -m alembic current
python -m alembic check
```

### Executar a API

```powershell
python -m uvicorn app.main:app --reload
```

Endpoints públicos úteis:

- `GET http://127.0.0.1:8000/health`
- documentação OpenAPI, quando habilitada no ambiente

As rotas de negócio exigem `X-API-Key`.

## Frontend

Na pasta `frontend`:

```powershell
npm ci
Copy-Item .env.example .env.local
```

Configure:

```text
AUNERON_BACKEND_URL=http://127.0.0.1:8000
AUNERON_API_KEY=<mesma API_KEY do backend local>
VITE_ELEVATED_DEV_CODE=<credencial exclusivamente local>
```

`AUNERON_API_KEY` não possui prefixo `VITE_` de propósito. Ela é usada
somente pelo processo do Vite e não deve entrar no bundle do navegador.

Execute:

```powershell
npm run dev
```

## Qualidade

### Frontend

```powershell
npm run lint
npm run build
```

### Backend

Na pasta `backend`:

```powershell
python -m compileall -q app tests scripts
powershell -ExecutionPolicy Bypass -File .\scripts\test.ps1
```

### E2E

Na primeira execução:

```powershell
python -m playwright install chromium
```

Depois:

```powershell
python .\scripts\e2e_frontend.py
```

O E2E usa o banco `auneron_test` e valida o fluxo navegador -> proxy ->
FastAPI sem gravar dados de negócio.

## CI

O GitHub Actions executa em ambiente descartável:

- Python 3.11
- Node.js 22
- PostgreSQL 17
- lint e build do frontend
- validação Alembic
- pytest
- diagnóstico PostgreSQL
- Chromium + E2E
- confirmação de limpeza do banco de testes

Workflow:

```text
.github/workflows/backend-ci.yml
```

## Segurança

Regras essenciais:

- `.env` e `.env.local` nunca devem ser versionados;
- `API_KEY` deve ter pelo menos 32 caracteres;
- a chave de serviço não deve ser exposta no bundle React;
- produção deve usar HTTPS;
- credenciais reais devem ficar no gerenciador de segredos da
  plataforma;
- a API key atual é uma barreira entre serviços, não autenticação
  individual de usuários.

Antes de exposição pública multiusuário, o sistema deve adotar
autenticação de usuário e autorização no backend.

## Observabilidade

O backend gera logs estruturados em JSON e utiliza `X-Request-ID` para
correlação. O frontend também envia um request ID e pode apresentá-lo
como referência em falhas HTTP.

Dados sensíveis não devem ser incluídos nos logs.

## Deploy

O repositório inclui imagens containerizadas para backend e frontend:

```text
backend/Dockerfile
frontend/Dockerfile
frontend/nginx/default.conf.template
.dockerignore
```

A estratégia de produção é descrita em:

```text
backend/docs/DEPLOYMENT.md
```

O deploy não deve reutilizar o `backend/docker-compose.yml` de
desenvolvimento como configuração de produção.

## Estado da fundação

A fundação atual inclui PostgreSQL, Alembic, testes automatizados,
proteção da API, configuração segura por ambiente, observabilidade,
integração segura frontend/backend e E2E no CI.
