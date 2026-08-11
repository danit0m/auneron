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

## Arquitetura recomendada

```text
Internet
   |
 HTTPS
   v
Frontend / reverse proxy
   | \
   |  \ cookie HttpOnly do usuário
   |
   | /api + X-API-Key
   v
FastAPI
   |
   v
PostgreSQL
```

O PostgreSQL não deve ser publicado diretamente na Internet.

## Segredos necessários

A plataforma de deploy fornece segredos em runtime.

Backend:

```text
APP_ENV=production
DATABASE_URL=<URL PostgreSQL de produção>
API_KEY=<chave aleatória com pelo menos 32 caracteres>
LOG_LEVEL=INFO
AUTH_COOKIE_NAME=auneron_session
AUTH_SESSION_TTL_MINUTES=480
AUTH_ELEVATION_TTL_MINUTES=10
AUTH_LOGIN_ACCOUNT_MAX_FAILURES=5
AUTH_LOGIN_IP_MAX_FAILURES=25
AUTH_LOGIN_WINDOW_SECONDS=900
AUTH_ELEVATION_USER_MAX_FAILURES=5
AUTH_ELEVATION_IP_MAX_FAILURES=15
AUTH_ELEVATION_WINDOW_SECONDS=600
AUTH_SESSION_CLEANUP_INTERVAL_SECONDS=3600
AUTH_REVOKED_SESSION_RETENTION_HOURS=24
```

Frontend/reverse proxy:

```text
AUNERON_API_KEY=<mesma chave de serviço do backend>
```

A API key não deve ser enviada ao build React como variável `VITE_*`.

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

Consulte `POSTGRESQL_OPERATIONS.md` antes de aumentar workers.

## Build do backend

Na raiz do repositório:

```powershell
docker build `
    -f backend/Dockerfile `
    -t auneron-backend:local `
    .
```

A imagem executa o FastAPI como usuário não-root.

## Migrações

Execute migrações como etapa única antes da nova versão receber tráfego:

```text
python -m alembic upgrade head
```

Não execute a mesma migração simultaneamente em vários workers.

Antes de uma migração em banco real:

1. valide backup recente;
2. valide a revisão Alembic esperada;
3. aplique a migração;
4. execute `alembic current`;
5. execute o health check.

## Execução do backend

```text
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --proxy-headers
```

Probes públicos:

```text
GET /health
GET /ready
```

Use `/health` como liveness do processo. Ele não depende do PostgreSQL.

Use `/ready` para readiness. Ele retorna `HTTP 503` quando o PostgreSQL não
está disponível e `HTTP 200` quando a API está pronta para atender operações
que dependem do banco.

## Primeiro usuário

Depois da migration de autenticação e com o backend disponível, crie o
primeiro operador de forma interativa:

```powershell
docker compose exec backend `
    python -m scripts.create_user `
    --name "Administrador" `
    --email "SEU_EMAIL" `
    --role administrator
```

A senha é solicitada por `getpass` e não deve ser enviada por variável de
ambiente.

## Build do frontend

```powershell
docker build `
    -f frontend/Dockerfile `
    -t auneron-frontend:local `
    .
```

O build React usa somente URLs relativas `/api`.

A imagem final é Nginx. Durante o startup, o template substitui
`AUNERON_API_KEY`. A chave fica na configuração interna do reverse proxy,
não nos assets JavaScript.

## Rede

O Nginx assume:

```text
backend:8000
```

O navegador acessa:

```text
/api/...
```

e o proxy converte para as rotas FastAPI, injeta `X-API-Key` e preserva o
cookie de sessão.

## TLS e cookie

HTTPS é obrigatório em produção.

Com `APP_ENV=production`, o cookie de sessão é marcado `Secure`; portanto,
o fluxo real de login deve ser validado através de HTTPS.

TLS pode terminar no load balancer, ingress ou proxy externo.

## CORS e configuração de produção

Quando frontend e API usam o mesmo host e `/api`, o navegador trabalha em
mesma origem. Nesse caso, prefira `CORS_ORIGINS` vazio.

Se forem origens diferentes, configure `CORS_ORIGINS` explicitamente.
Em `production`, somente origens HTTPS são aceitas e wildcard `*` é rejeitado.

O backend também recusa startup de produção com PostgreSQL ausente, API key
fraca/placeholder, `DEBUG=true` ou `DATABASE_ECHO=true`.

## Autenticação e autorização

A API key é apenas a credencial entre serviços.

Os usuários autenticam por sessão `HttpOnly`. O backend aplica RBAC às
rotas de negócio e exige elevação temporária para operações
administrativas sensíveis.

A sessão padrão expira em 8 horas e a elevação padrão em 10 minutos,
salvo configuração diferente.

## Rate limiting e sessões

Login e elevação possuem rate limiting. Respostas bloqueadas usam `HTTP 429`
e `Retry-After`.

A implementação atual do limiter é por processo. Não aumente o número de
instâncias FastAPI sem migrar essa proteção para um armazenamento
compartilhado.

Sessões expiradas são removidas periodicamente. Sessões revogadas ainda
válidas são retidas pelo período configurado e depois removidas.

## Headers de segurança

O backend e o Nginx aplicam headers de segurança. Em produção/HTTPS, valide
também HSTS no caminho público. O template Nginx da SPA aplica CSP,
`Permissions-Policy`, `X-Frame-Options`, `Referrer-Policy` e
`X-Content-Type-Options`.

## Logs

Envie stdout/stderr ao coletor da plataforma.

Procure por:

```text
request_id
status_code
duration_ms
environment
```

Nunca imprima `DATABASE_URL`, `API_KEY`, cookies, senhas ou tokens.

## Banco

Recomendação:

- PostgreSQL 17;
- backups automáticos;
- retenção definida;
- criptografia em trânsito;
- acesso de rede restrito;
- usuário de aplicação sem privilégios administrativos.

O `backend/docker-compose.yml` é destinado ao desenvolvimento local.

## Escalabilidade

Cada processo FastAPI pode utilizar, no pico:

```text
DATABASE_POOL_SIZE + DATABASE_MAX_OVERFLOW
```

Logo:

```text
(pool_size + max_overflow) * workers + reserva < max_connections
```

## Validação pós-deploy

Após cada publicação:

1. confirme a revisão Alembic;
2. consulte `/health` e `/ready`;
3. carregue `/login`;
4. faça login com usuário real de validação;
5. valide Dashboard e Clientes;
6. recarregue a página e confirme restauração da sessão;
7. confirme HTTP 401 quando houver API key mas não houver sessão;
8. valide um acesso com permissão insuficiente retornando HTTP 403;
9. valide elevação em recurso administrativo;
10. confirme `X-Request-ID` nos logs;
11. confirme que a API key não aparece em HTML/JS;
12. confirme headers de segurança no endpoint público;
13. valide `HTTP 429`/`Retry-After` em ambiente de teste controlado.

Use também `RELEASE_CHECKLIST.md`.
