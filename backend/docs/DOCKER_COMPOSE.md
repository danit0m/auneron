# Docker Compose local

## Objetivo

O Compose local sobe a stack completa do Auneron:

```text
Browser
  |
  | http://127.0.0.1:8080
  v
Nginx + React
  |
  | /api
  | + X-API-Key no lado servidor
  v
FastAPI
  |
  v
PostgreSQL 17
```

Os serviços são:

- `postgres`;
- `migration`;
- `backend`;
- `frontend`.

`migration` é um serviço de execução única. Ele aplica
`alembic upgrade head` depois que o PostgreSQL fica saudável. O backend
só inicia depois que a migração termina com sucesso.

## Configuração

Na pasta `backend`:

```powershell
Copy-Item .env.example .env
```

Configure pelo menos uma `API_KEY` aleatória com 32 ou mais caracteres.

O Compose lê automaticamente `backend/.env`.

Variáveis específicas do Compose:

```text
POSTGRES_PASSWORD=auneron_dev_password
POSTGRES_PORT=5433
BACKEND_HTTP_PORT=8000
AUNERON_HTTP_PORT=8080
```

O serviço backend também recebe as configurações de autenticação, rate
limiting e manutenção de sessões definidas em `.env.example`.

O password padrão existe somente para desenvolvimento local.

### Volume PostgreSQL existente

O volume persistente continua se chamando:

```text
auneron_postgres_data
```

O PostgreSQL só usa `POSTGRES_PASSWORD` quando o volume é inicializado
pela primeira vez.

Se o volume já existe, alterar `POSTGRES_PASSWORD` no `.env` não altera
automaticamente a senha do papel PostgreSQL dentro do banco.

Não execute `docker compose down -v` em um banco que deseja preservar.

## Subir a stack

Na pasta `backend`:

```powershell
docker compose up -d --build
```

Acompanhe:

```powershell
docker compose ps
```

O estado esperado é:

```text
postgres    healthy
migration   exited (0)
backend     healthy
frontend    healthy
```

O serviço `migration` pode não aparecer em `docker compose ps` sem `-a`,
pois ele termina após aplicar o Alembic.

Para visualizar todos:

```powershell
docker compose ps -a
```

## Acessos

Frontend:

```text
http://127.0.0.1:8080
```

Backend local para diagnóstico:

```text
http://127.0.0.1:8000/health
http://127.0.0.1:8000/ready
```

`/health` mede liveness do processo. `/ready` verifica o PostgreSQL.

PostgreSQL local:

```text
127.0.0.1:5433
```

As portas podem ser alteradas no `.env`.

## Logs

Todos:

```powershell
docker compose logs -f
```

Backend:

```powershell
docker compose logs -f backend
```

Migração:

```powershell
docker compose logs migration
```

PostgreSQL:

```powershell
docker compose logs -f postgres
```

## Parar

```powershell
docker compose down
```

Esse comando remove containers e rede, mas preserva
`auneron_postgres_data`.

Para listar o volume:

```powershell
docker volume ls --filter name=auneron_postgres_data
```

## Não usar `-v` sem intenção

O comando abaixo remove o volume e seus dados:

```text
docker compose down -v
```

Ele deve ser usado somente quando a exclusão do banco local for
intencional e houver backup quando necessário.

## Validar o Compose

```powershell
docker compose config --quiet
```

## Smoke test

Na pasta `backend`:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\compose_smoke.ps1
```

O smoke test:

- valida a configuração do Compose;
- sobe/reconstrói a stack;
- aguarda liveness `/health`;
- aguarda readiness `/ready`;
- confirma HTTP 401 no backend protegido sem API key;
- confirma `/api/health` e `/api/ready` pelo Nginx;
- confirma HTTP 401 em `/api/dashboard/` e `/api/auth/me` sem sessão;
- valida headers de segurança do Nginx, incluindo CSP;
- não remove o volume PostgreSQL ao terminar.

## Produção

Este Compose é voltado ao desenvolvimento local.

Para produção, consulte:

```text
docs/DEPLOYMENT.md
```

Produção deve utilizar segredos gerenciados, HTTPS, banco protegido e
uma estratégia de deploy adequada à plataforma.

O rate limiter atual é por processo. O Compose local utiliza uma instância de
backend; não use múltiplas instâncias sem um limiter compartilhado.

## Autenticação da interface

O Compose utiliza a mesma autenticação real do restante do Auneron.

O navegador realiza login por `/api/auth/login` e recebe um cookie de
sessão `HttpOnly`. As sessões são persistidas no PostgreSQL e continuam
válidas após restart do processo backend enquanto não expirarem ou forem
revogadas.

O Nginx do serviço `frontend` injeta `X-API-Key` no lado servidor. Essa
chave é uma credencial entre serviços e, sozinha, não autoriza acesso aos
dados de negócio.

O Compose não injeta senha de usuário, token de sessão nem credencial de
elevação no bundle React. A elevação administrativa é validada pelo
backend e fica vinculada à sessão atual.

Depois da migration, o primeiro usuário pode ser criado de forma
interativa:

```powershell
docker compose exec backend `
    python -m scripts.create_user `
    --name "Administrador" `
    --email "admin@example.com" `
    --role administrator
```

A senha é solicitada via `getpass`; não a coloque em `.env`, Compose ou
histórico de comandos.
