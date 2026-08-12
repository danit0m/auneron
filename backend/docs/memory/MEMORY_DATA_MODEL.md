# Memory Data Model V1

**Status:** Frozen para o Commit 21B

## 1. Tabelas

```text
memory_items 1 ─── 0..N memory_evidence
```

## 2. `memory_items`

| Campo | Tipo lógico PostgreSQL | Null | Regra |
|---|---|---:|---|
| `id` | `BIGINT` | não | PK |
| `memory_type` | `VARCHAR(32)` | não | tipo V1 |
| `title` | `VARCHAR(200)` | não | título humano |
| `content` | `TEXT` | não | conteúdo imutável |
| `memory_key` | `VARCHAR(255)` | sim | chave canônica |
| `scope_type` | `VARCHAR(20)` | não | global/account/user |
| `account_id` | `INTEGER` | sim | FK para `accounts.id` |
| `subject_user_id` | `INTEGER` | sim | FK para `users.id` |
| `created_by_user_id` | `INTEGER` | sim | FK para `users.id` |
| `importance` | `NUMERIC(4,3)` | não | 0..1; default 0.500 |
| `confidence` | `NUMERIC(4,3)` | não | 0..1; sem default |
| `status` | `VARCHAR(20)` | não | default active |
| `status_reason` | `TEXT` | sim | motivo de transição |
| `status_changed_at` | `TIMESTAMPTZ` | não | timestamp de lifecycle |
| `valid_from` | `TIMESTAMPTZ` | não | início de validade |
| `valid_until` | `TIMESTAMPTZ` | sim | fim de validade |
| `source_type` | `VARCHAR(24)` | não | proveniência |
| `source_reference` | `VARCHAR(500)` | não | referência lógica |
| `supersedes_memory_id` | `BIGINT` | sim | self-FK |
| `context_data` | `JSONB` | não | default `{}` |
| `created_at` | `TIMESTAMPTZ` | não | criação |
| `updated_at` | `TIMESTAMPTZ` | não | atualização de metadado |

A auditoria 21B.1 confirmou que `accounts.id` e `users.id` usam `INTEGER`.

As PKs próprias do Memory permanecem `BIGINT`; somente as FKs que apontam para `accounts`/`users` usam `INTEGER`.

## 3. Valores permitidos

`memory_type`:

```text
fact event observation decision summary
```

`scope_type`:

```text
global account user
```

`status`:

```text
active superseded expired invalidated archived
```

`source_type`:

```text
database upload user agent system api derived
```

Preferir `CHECK` constraints na V1.

## 4. Integridade de escopo

```text
global:
account_id IS NULL
AND subject_user_id IS NULL

account:
account_id IS NOT NULL
AND subject_user_id IS NULL

user:
account_id IS NULL
AND subject_user_id IS NOT NULL
```

## 5. Delete behavior

```text
account_id              ON DELETE RESTRICT
subject_user_id         ON DELETE RESTRICT
created_by_user_id      ON DELETE SET NULL
supersedes_memory_id    ON DELETE RESTRICT
memory_id (evidence)    ON DELETE CASCADE
source_memory_id        ON DELETE SET NULL
```

### Racional

`account_id` e `subject_user_id` definem a identidade do escopo da memória.

`CASCADE` nesses vínculos permitiria que a exclusão física de uma entidade pai apagasse memória empresarial, proveniência e histórico. `SET NULL` também não é adequado porque violaria a integridade do escopo (`account` exige `account_id`; `user` exige `subject_user_id`).

Por isso, a V1 usa `RESTRICT` para impedir a exclusão do pai enquanto existir memória que dependa dele.

`created_by_user_id` usa `SET NULL` porque representa autoria, não propriedade do escopo.

`memory_evidence.memory_id` usa `CASCADE` porque evidence é parte integrante da memória pai. `source_memory_id` usa `SET NULL` porque a evidência deve continuar existindo mesmo se a referência lógica de origem deixar de existir.

`supersedes_memory_id` usa `RESTRICT` para evitar cascade destrutivo pela cadeia de supersession.

## 6. Constraints

```text
0 <= importance <= 1
0 <= confidence <= 1
0 <= weight <= 1

valid_until IS NULL
OR valid_until > valid_from

supersedes_memory_id IS NULL
OR supersedes_memory_id <> id
```

Strings obrigatórias são validadas após trim no Service; checks simples no banco podem reforçar isso.

## 7. `memory_key`

Identificador técnico canônico, com alfabeto seguro como:

```text
[a-z0-9._:-]
```

Não é texto humano.

## 8. Unicidade ativa por escopo

Global:

```text
UNIQUE(memory_key)
WHERE scope_type='global'
AND status='active'
AND memory_key IS NOT NULL
```

Account:

```text
UNIQUE(account_id, memory_key)
WHERE scope_type='account'
AND status='active'
AND memory_key IS NOT NULL
```

User:

```text
UNIQUE(subject_user_id, memory_key)
WHERE scope_type='user'
AND status='active'
AND memory_key IS NOT NULL
```

## 9. Índices principais

```text
(status, memory_type, created_at)
(account_id, status, memory_type, valid_from)
(subject_user_id, status, memory_type, valid_from)
(source_type, source_reference)
```

Índice parcial para expiração:

```text
valid_until
WHERE status='active'
AND valid_until IS NOT NULL
```

## 10. Full-text search

Commit 21D deve adicionar/verificar busca PostgreSQL em:

```text
title + content
```

preferencialmente com GIN e configuração adequada a português quando validada no ambiente PostgreSQL alvo.

Embeddings não fazem parte do schema V1.

## 11. `context_data`

```text
JSONB NOT NULL DEFAULT '{}'
```

Regras de serviço/API:

```text
raiz = objeto
máximo = 32 KB serializado
profundidade <= 5
```

Dados essenciais para busca ou integridade devem ser colunas normais.

## 12. `memory_evidence`

| Campo | Tipo lógico | Null |
|---|---|---:|
| `id` | `BIGINT` | não |
| `memory_id` | `BIGINT` | não |
| `relation` | `VARCHAR(20)` | não |
| `source_type` | `VARCHAR(24)` | não |
| `source_reference` | `VARCHAR(500)` | não |
| `source_memory_id` | `BIGINT` | sim |
| `evidence_text` | `TEXT` | não |
| `evidence_hash` | `CHAR(64)` | não |
| `weight` | `NUMERIC(4,3)` | não |
| `observed_at` | `TIMESTAMPTZ` | sim |
| `created_by_user_id` | `INTEGER` | sim |
| `context_data` | `JSONB` | não |
| `created_at` | `TIMESTAMPTZ` | não |

`memory_id` usa `ON DELETE CASCADE`.

Relações:

```text
supports contradicts context
```

Deduplicação:

```text
UNIQUE(memory_id, evidence_hash)
```

Índices:

```text
(memory_id, created_at)
(source_memory_id)
```

## 13. Evidence lifecycle

Evidence é append-only na V1. Correções usam nova evidência, contradição e, quando necessário, `invalidate()` ou `supersede()`.

## 14. Requisitos da migration

21B deve provar:

- tabelas aditivas;
- sem reescrita destrutiva das tabelas existentes;
- FKs/checks/índices presentes;
- partial unique indexes presentes;
- deduplicação de evidência;
- upgrade em banco de teste novo;
- downgrade/upgrade somente em ambiente seguro;
- rollback de produção não depende de apagar memória real.


## 15. Convenção de timestamps confirmada no 21B.1

O projeto usa `DateTime(timezone=True)`/`TIMESTAMPTZ` com `server_default=func.now()` para timestamps de criação.

Não existe hoje um padrão global de `updated_at`.

Memory V1 introduzirá `updated_at` localmente com:

```text
DateTime(timezone=True)
server_default=func.now()
onupdate=func.now()
```

`status_changed_at` continua sendo atualizado explicitamente nas transições de lifecycle.

Não será criado trigger de banco para `updated_at` na V1.

## 16. Registro de models no Alembic

`migrations/env.py` importa `app.models`, e `app.models.__init__` registra explicitamente os models.

Portanto, os novos models Memory devem ser importados/exportados por `app/models/__init__.py` para fazer parte de `Base.metadata` e do autogenerate do Alembic.
