# MemoryRepository V1 Contract

**Status:** Frozen

## 1. Papel

Camada de persistência e consulta via SQLAlchemy.

Faz queries, inserts, flush/refresh, locking e manutenção em lote.

Não decide RBAC, lifecycle, raciocínio e não faz `commit()`.

## 2. Interface conceitual

```text
insert_memory()
get_by_id()
find_active_by_key()
search()
count()
lock_by_id()
lock_active_by_key()
update_status()
insert_evidence()
list_evidence()
expire_due_batch()
```

Não existem `execute_sql()`, `query_anything()` ou `list_all()` irrestritos.

## 3. Escopo explícito

Toda busca recebe `MemoryScope`.

```text
global
account:<id>
user:<id>
```

Nunca `scope=None` significando tudo.

## 4. `MemoryQuery`

```text
scope
memory_types
statuses
memory_key
source_types
min_importance
min_confidence
valid_at
created_after
created_before
text_query
limit
cursor
sort
```

Sem fragmentos SQL fornecidos pelo caller.

## 5. Validade operacional

```text
status='active'
AND valid_from <= :as_of
AND (valid_until IS NULL OR valid_until > :as_of)
```

## 6. Ordenação

Default:

```text
importance DESC
confidence DESC
valid_from DESC
id DESC
```

Com full-text:

```text
text_rank DESC
importance DESC
confidence DESC
valid_from DESC
id DESC
```

Sort permitido apenas por enum interna:

```text
relevance newest oldest importance confidence
```

## 7. Cursor pagination

```text
default=20
max=100
```

Cursor:

- opaco;
- contém posição determinística;
- vinculado ao fingerprint da query;
- protegido contra adulteração, preferencialmente HMAC;
- não é autorização.

`total_count` não é calculado por padrão.

## 8. Concorrência

Criação concorrente é protegida por partial unique indexes.

`supersede()` bloqueia a versão atual com:

```text
SELECT ... FOR UPDATE
```

Segundo caller espera e revalida estado.

## 9. Lock discipline

Transações curtas, ordem determinística e nenhuma chamada externa com locks mantidos.

## 10. Expiração

Lotes limitados usando:

```text
FOR UPDATE SKIP LOCKED
```

Leitura permanece correta mesmo se manutenção estiver atrasada.

## 11. Evidence

Evidence não é eager-loaded automaticamente no `recall()`.

Acesso a evidence é solicitado depois de autorização da memória pai.

## 12. JSONB

V1 não expõe linguagem livre de consulta sobre `context_data`.

## 13. Logging

Metadados de query são permitidos; corpos de memória/evidência e segredos não.

## 14. Testes obrigatórios

- isolamento global/account/user;
- active/valid default;
- `as_of`;
- ordenação determinística;
- cursor sem skip/duplicate;
- cursor incompatível com query;
- active-key uniqueness;
- supersede concorrente;
- evidence duplicate;
- expiration batch.
