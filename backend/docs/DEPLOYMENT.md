# Deploy do Auneron

## Objetivo

Este documento define uma base de deploy independente de provedor.

O repositório inclui:

```text
backend/Dockerfile
frontend/Dockerfile
frontend/nginx/default.conf.template
.dockerignore
```

Esses arquivos permitem construir imagens sem colocar os arquivos locais
`.env`, bancos SQLite históricos, CSVs ou `node_modules` no contexto útil
das imagens.

## Arquitetura recomendada

```text
Internet
   |
 HTTPS
   v
Frontend / reverse proxy
   |
   | /api
   | + X-API-Key
   v
FastAPI
   |
   v
PostgreSQL gerenciado ou dedicado
```

O PostgreSQL não deve ser publicado diretamente na Internet.

## Segredos necessários

A plataforma de deploy deve fornecer segredos em runtime.

Backend:

```text
APP_ENV=production
DATABASE_URL=<URL PostgreSQL de produção>
API_KEY=<chave aleatória com pelo menos 32 caracteres>
LOG_LEVEL=INFO
```

Frontend/reverse proxy:

```text
AUNERON_API_KEY=<mesma chave de serviço do backend>
```

A chave do backend não deve ser enviada ao processo de build React como
`VITE_API_KEY`.

## Configuração operacional do banco

Ajuste conforme a capacidade do serviço:

```text
DATABASE_POOL_SIZE=5
DATABASE_MAX_OVERFLOW=5
DATABASE_POOL_TIMEOUT=10
DATABASE_POOL_RECYCLE=900
DATABASE_CONNECT_TIMEOUT=5
DATABASE_STATEMENT_TIMEOUT_MS=30000
DATABASE_LOCK_TIMEOUT_MS=5000
DATABASE_IDLE_TRANSACTION_TIMEOUT_MS=60000
DATABASE_APPLICATION_NAME=auneron-api-production
```

Consulte `POSTGRESQL_OPERATIONS.md` antes de aumentar o número de workers.

## Build do backend

Na raiz do repositório:

```powershell
docker build `
    -f backend/Dockerfile `
    -t auneron-backend:local `
    .
```

A imagem executa o FastAPI como usuário não-root.

O container necessita da `DATABASE_URL` e da `API_KEY` em runtime.

## Migrações

Migrações devem ser executadas como etapa única antes da nova versão da
API receber tráfego:

```text
python -m alembic upgrade head
```

Não execute a mesma migração simultaneamente em vários workers.

Antes de aplicar uma migração em banco com dados reais:

1. valide um backup recente;
2. valide a revisão Alembic esperada;
3. aplique a migração;
4. execute `alembic current`;
5. execute o health check.

## Execução do backend

Com variáveis fornecidas pela plataforma:

```text
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --proxy-headers
```

O endpoint público de saúde é:

```text
GET /health
```

Use esse endpoint no health check da plataforma.

## Build do frontend

Na raiz do repositório:

```powershell
docker build `
    -f frontend/Dockerfile `
    -t auneron-frontend:local `
    .
```

O build React utiliza somente `/api`.

A imagem final é Nginx. Durante o startup, o template de configuração
substitui somente `AUNERON_API_KEY`. A chave fica na configuração interna
do reverse proxy e não nos arquivos JavaScript do SPA.

## Rede entre frontend e backend

O template Nginx assume que o backend pode ser resolvido por:

```text
backend:8000
```

Em Docker Compose/Kubernetes, utilize `backend` como nome do serviço ou
adapte `frontend/nginx/default.conf.template` para o DNS interno da
plataforma.

O navegador acessa somente:

```text
/api/...
```

e o Nginx converte para as rotas reais do FastAPI.

## TLS

Em produção, HTTPS é obrigatório.

TLS pode terminar:

- no load balancer da plataforma;
- no ingress;
- em um proxy externo ao container.

Não exponha uma implantação pública apenas por HTTP.

## CORS

Quando frontend e API são publicados pelo mesmo host e `/api` é
encaminhado internamente, o navegador trabalha em mesma origem e CORS
deixa de ser o mecanismo principal de integração.

Caso frontend e API sejam publicados em origens diferentes, configure
`CORS_ORIGINS` explicitamente no backend. Não use `*` com credenciais ou
em um ambiente sensível.

## Logs

O processo deve enviar stdout/stderr para o coletor de logs da plataforma.

Procure por:

```text
request_id
status_code
duration_ms
environment
```

Nunca configure o serviço para imprimir `DATABASE_URL`, `API_KEY` ou
headers completos.

## Banco

Recomendação para produção:

- PostgreSQL 17;
- backups automáticos;
- retenção definida;
- criptografia em trânsito;
- acesso de rede restrito;
- usuário de aplicação sem privilégios administrativos.

O `backend/docker-compose.yml` contém PostgreSQL para desenvolvimento
local. Ele não é um manifesto de produção.

## Escalabilidade

Cada processo FastAPI pode utilizar, no pico:

```text
DATABASE_POOL_SIZE + DATABASE_MAX_OVERFLOW
```

Logo:

```text
(pool_size + max_overflow) * workers + reserva < max_connections
```

Dimensione os workers junto com a capacidade do PostgreSQL.

## Autenticação

A API key atual é uma credencial entre serviços. Ela não identifica o
usuário final.

Antes de exposição pública multiusuário, implemente:

- autenticação individual;
- autorização no backend;
- sessão segura ou token de curta duração;
- expiração e revogação;
- proteção contra abuso;
- HTTPS obrigatório.

## Validação pós-deploy

Após cada publicação:

1. confirme a revisão Alembic;
2. consulte `/health`;
3. carregue o frontend;
4. valide Dashboard e Clientes;
5. provoque uma chamada protegida sem chave e confirme HTTP 401;
6. confirme que `/api` funciona pelo reverse proxy;
7. valide logs com `X-Request-ID`;
8. confirme que nenhum segredo aparece no HTML/JS entregue ao navegador.

Use também `RELEASE_CHECKLIST.md`.
