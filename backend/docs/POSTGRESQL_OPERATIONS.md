# Operação do PostgreSQL

## Diagnóstico da auditoria

A auditoria do Commit 15 encontrou PostgreSQL 17 com:

- `max_connections = 100`;
- `statement_timeout = 0`;
- `lock_timeout = 0`;
- `idle_in_transaction_session_timeout = 0`;
- `application_name` vazio;
- aplicação usando `QueuePool`;
- `pool_pre_ping` já habilitado.

O objetivo deste commit é aplicar proteção por sessão da aplicação,
sem modificar parâmetros globais do servidor PostgreSQL.

## Configuração recomendada

Valores padrão do backend:

```text
DATABASE_POOL_SIZE=5
DATABASE_MAX_OVERFLOW=5
DATABASE_POOL_TIMEOUT=10
DATABASE_POOL_RECYCLE=900
DATABASE_CONNECT_TIMEOUT=5
DATABASE_STATEMENT_TIMEOUT_MS=30000
DATABASE_LOCK_TIMEOUT_MS=5000
DATABASE_IDLE_TRANSACTION_TIMEOUT_MS=60000
DATABASE_APPLICATION_NAME=auneron-api
```

Esses valores podem ser ajustados por ambiente.

## Pool

Cada processo da aplicação pode abrir, no pico:

```text
DATABASE_POOL_SIZE + DATABASE_MAX_OVERFLOW
```

Com os valores padrão, são até 10 conexões por processo.

Ao definir a quantidade de workers, mantenha uma reserva para
migrações, administração e outras aplicações:

```text
(pool_size + max_overflow) * workers + reserva
    < max_connections
```

Para um servidor com `max_connections = 100`, quatro workers com
pool máximo de 10 conexões consomem no máximo 40 conexões, deixando
margem operacional.

`pool_pre_ping` continua habilitado para detectar conexões
interrompidas antes de entregá-las à aplicação.

`pool_use_lifo` permite reutilizar primeiro as conexões mais
recentes do pool.

`pool_reset_on_return=rollback` garante limpeza transacional ao
devolver uma conexão ao pool.

## Timeouts

`DATABASE_CONNECT_TIMEOUT` limita quanto tempo uma nova conexão pode
esperar pelo PostgreSQL.

`DATABASE_STATEMENT_TIMEOUT_MS` limita a duração de comandos SQL
executados pela sessão da aplicação.

`DATABASE_LOCK_TIMEOUT_MS` impede espera indefinida por locks.

`DATABASE_IDLE_TRANSACTION_TIMEOUT_MS` encerra sessões que ficam
paradas mantendo uma transação aberta por tempo excessivo.

As configurações são aplicadas quando cada conexão física é criada.
Nenhum `ALTER SYSTEM` é executado.

## Application name

Cada conexão recebe:

```text
application_name = auneron-api
```

Isso permite identificar conexões do backend em `pg_stat_activity`.

Em produção, um nome específico por serviço ou ambiente pode ser
usado, por exemplo:

```text
DATABASE_APPLICATION_NAME=auneron-api-production
```

## Transações

A dependência `get_db()` executa `rollback()` quando uma exceção
escapa da operação e sempre fecha a sessão.

Os endpoints que realizam persistência continuam responsáveis pelo
`commit()` explícito.

## Ciclo de vida

No startup, o backend testa a conectividade com o banco e registra
o resultado no log estruturado.

No shutdown, `engine.dispose()` fecha o pool do processo.

Uma indisponibilidade no startup não derruba o processo
imediatamente; o endpoint `/health` permanece disponível e pode
indicar estado `degraded`.

## Diagnóstico seguro

Execute na pasta `backend`:

```powershell
python .\scripts\db_diagnostics.py
```

O script mostra banco, usuário, driver, pool e parâmetros ativos da
sessão, mas não imprime `DATABASE_URL`, senha ou API key.

## Migrações

O Alembic continua usando `NullPool` para migrações. As migrações
não compartilham o `QueuePool` da API.

Antes de deploy:

```powershell
python -m alembic upgrade head
python -m alembic current
python -m alembic check
```
