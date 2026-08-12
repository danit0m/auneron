# MemoryService V1 Contract

**Status:** Frozen

## 1. Papel

`MemoryService` é a fronteira oficial de domínio para escrita, leitura, autorização e lifecycle.

Responsável por:

```text
validation
normalization
authorization
scope
provenance
confidence
deduplication
lifecycle
transactions
evidence
domain errors
```

Não é responsável por SQL arbitrário, raciocínio do Brain, tarefas, APIs externas, embeddings ou treinamento.

## 2. Operações públicas

```text
remember()
get()
recall()
supersede()
invalidate()
archive()
expire()
add_evidence()
```

Não existe `delete()` operacional geral.

## 3. `remember()`

Valida tipo, título/conteúdo, escopo, FKs, importance/confidence, proveniência, temporalidade, `memory_key`, limites e conflitos.

Toda memória nova nasce `active`.

### Duplicado equivalente

Resultado idempotente:

```text
created = false
duplicate = true
memory = existente
```

### Conflito

Mesma chave/escopo ativa com conhecimento diferente não é sobrescrita.

O chamador deve usar `supersede()`.

## 4. `get()`

Sempre valida autorização. Conhecer o ID não concede acesso.

## 5. `recall()`

Filtros conceituais:

```text
scope
memory_types
statuses
memory_key
source_types
min_importance
min_confidence
as_of
created_after
created_before
text_query
limit
cursor
sort
```

Default:

```text
status=active
as_of=agora UTC
limit=20
```

## 6. `supersede()`

Operação atômica:

```text
BEGIN
lock old
validate old active
validate actor
validate replacement
same scope
same memory_key
mark old superseded
status_reason/status_changed_at
flush lifecycle update
create replacement active
set supersedes_memory_id
add evidence
COMMIT
```

A ordem **old → superseded antes do INSERT da replacement** é obrigatória quando existe `memory_key`, porque os partial unique indexes permitem somente uma memória `active` por chave/escopo.

A atualização intermediária não fica visível como estado final para outros callers: toda a operação permanece na mesma transação. Se a criação da replacement ou evidence falhar, o rollback restaura a memória antiga como `active`.

Qualquer falha → rollback total.

Reason obrigatório.

## 7. `invalidate()`

```text
active → invalidated
```

Reason obrigatório. Conteúdo não é apagado.

## 8. `archive()`

```text
active → archived
```

Reason opcional na V1.

## 9. `expire()`

```text
active → expired
```

Sem endpoint HTTP público na V1. Pode ser usado pela manutenção.

## 10. `add_evidence()`

Valida memória, autorização, relação, proveniência, `weight`, hash, payload e duplicidade.

Evidence duplicada é idempotente.

Contradição não altera status nem `confidence` automaticamente.

## 11. Imutabilidade

Campos como conteúdo, tipo, escopo, key, origem e autoria de criação não são editados normalmente.

Não existe:

```text
update_memory(content=...)
```

## 12. State machine

Permitido:

```text
active → superseded
active → invalidated
active → archived
active → expired
```

Estados finais não voltam a `active` na V1.

## 13. Transações

Service controla `BEGIN/COMMIT/ROLLBACK`.

Repository não faz `commit`.

Nunca chamar IA, HTTP externo ou operação externa lenta dentro da transação.

## 14. Actor context

```text
actor_type: user | agent | system
user_id?
agent_name?
role?
permissions
request_id
```

`system` e `developer` não são bypass.

## 15. Confidence

Obrigatória, sem default silencioso.

Agent/derived devem fornecer confidence explicitamente.

## 16. Erros de domínio

Conceitos esperados:

```text
MemoryNotFoundError
MemoryValidationError
MemoryConflictError
MemoryDuplicateError
MemoryScopeError
MemoryStateError
MemoryAuthorizationError
EvidenceDuplicateError
InvalidCursorError
```

Erros SQL não vazam para API.

## 17. Logging

Pode registrar IDs, tipos, escopo, ator, request ID, contagem e duração.

Não registrar conteúdo, evidence text, context JSON ou segredos.
