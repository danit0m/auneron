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
```

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

- sobe a stack;
- aguarda os healthchecks;
- confirma `/health`;
- confirma HTTP 401 no backend protegido sem chave;
- confirma o frontend;
- confirma `/api/dashboard/` pelo Nginx;
- não remove o volume PostgreSQL ao terminar.

## Produção

Este Compose é voltado ao desenvolvimento local.

Para produção, consulte:

```text
docs/DEPLOYMENT.md
```

Produção deve utilizar segredos gerenciados, HTTPS, banco protegido e
uma estratégia de deploy adequada à plataforma.

## Autentica��o da interface

A stack Docker valida a infraestrutura completa, incluindo o proxy
seguro `/api`.

A aplica��o frontend mant�m o controle de sess�o existente. Em um
navegador sem sess�o v�lida, a interface pode redirecionar para
`/access-denied`.

O Compose n�o injeta credenciais de usu�rio nem c�digos de eleva��o no
bundle React. A `API_KEY` � uma credencial entre Nginx e FastAPI e n�o
substitui autentica��o de usu�rio.

A autentica��o real de usu�rios deve ser tratada separadamente da
infraestrutura Docker.
