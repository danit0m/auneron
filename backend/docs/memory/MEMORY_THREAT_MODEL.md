# Memory Threat Model & Failure Modes V1

**Status:** Frozen

## 1. Objetivos

Proteger:

```text
confidentiality
integrity
availability
quality of knowledge
```

Princípio:

```text
MEMORY != TRUTH
```

## 2. Matriz principal

| Ameaça | Impacto | Probabilidade inicial | Defesa |
|---|---|---|---|
| memória falsa | alto | média | provenance + confidence + evidence |
| vazamento de scope | alto | baixa | Service + scope explícito + testes |
| prompt injection | alto | média futura | memória = untrusted data |
| stored XSS | alto | baixa | escaping |
| duplicação | médio | média | key/fingerprint/unique |
| race condition | alto | média | locks + unique constraints |
| supersede parcial | alto | baixa | transação atômica |
| DB indisponível | médio | baixa | falha explícita/503 |
| worker de expiração parado | médio | média | filtro temporal no recall |
| crescimento excessivo | médio | média | índices + cursor + archive |
| payload abusivo | médio | média | limites |
| histórico indevido | médio | baixa | permission separada |
| secret leakage | alto | baixa | guardrails + logging policy |
| inferência recursiva | alto | média futura | provenance graph |
| cursor manipulado | baixo | média | query binding + HMAC |

## 3. Memória falsa

`memory_type`, `source_type`, `confidence` e evidence devem permanecer explícitos.

Não existe default silencioso de confidence.

## 4. Inferência recursiva

Memórias derivadas preservam origem.

Uma memory não pode usar a si mesma diretamente como evidence.

Ciclos maiores serão controlados quando traversal de provenance for implementado.

## 5. Prompt injection

Texto recuperado é dado.

Nunca se transforma automaticamente em system instruction.

## 6. Stored XSS

Backend armazena texto; frontend escapa por padrão.

Nada de raw HTML como comportamento normal.

## 7. Scope leak / IDOR

ID não concede acesso.

Service autoriza; Repository exige scope explícito.

Testes devem provar isolamento account/user.

## 8. Evidence/source leak

Evidence herda acesso do pai.

`source_reference` não pode guardar token, senha ou URL com secret.

## 9. Log leakage

Nunca logar:

```text
content
evidence_text
context_data
password
session token
API key
private key
```

## 10. Duplicação

Proteções:

```text
memory_key
canonical fingerprint
partial unique indexes
evidence_hash
```

## 11. Corrida de insert

Um insert vence; outro recebe unique violation convertida em domain conflict.

## 12. Corrida de supersede

`SELECT ... FOR UPDATE`.

Segundo caller aguarda e revalida estado.

## 13. Falha parcial

Supersede inteiro em uma transação.

Falhou qualquer etapa → rollback.

## 14. Deadlock

Transações curtas, lock order determinística e nenhuma chamada externa durante a transação.

## 15. DB offline

Não responder sucesso sem persistência.

## 16. Commit realizado, resposta perdida

Retry deve ser seguro via idempotência/duplicate detection.

## 17. Worker de expiração parado

Recall ainda aplica `valid_from`/`valid_until`; portanto leitura continua correta.

## 18. Múltiplos workers

`FOR UPDATE SKIP LOCKED`.

## 19. Timezone

`TIMESTAMPTZ`, datetimes aware e UTC internamente.

## 20. Lifecycle inválido

Estados finais não são reativados.

## 21. Mutação silenciosa

Sem PUT/PATCH de conteúdo.

Mudança → supersede.

Erro → invalidate.

## 22. Hard delete

Fora da API operacional V1.

A exclusão física de entidades pai não pode apagar Memory por cascade. `account_id` e `subject_user_id` usam `ON DELETE RESTRICT`; a aplicação deve tratar a violação de integridade como conflito controlado.

## 23. Crescimento

Preparação via índices, cursor, archive e limites. Retention policy completa fica para commit futuro.

## 24. Payload abuse

Limites de texto, JSON, depth, evidence e page size.

## 25. Cursor

Opaco, validado, query-bound, tamper-resistant/HMAC e nunca usado como autorização.

## 26. Schema drift

Request schema + Service + DB constraints.

CI deve executar Alembic em banco de teste novo.

Schema tests verificam constraints e índices, não apenas migration success.

## 27. Migration safety

Tabelas de memória são aditivas.

Rollback de produção deve preferir application rollback + forward-fix, não downgrade destrutivo de dados reais.

## 28. Importance/confidence abuse

Valores permanecem explícitos e auditáveis.

Policies automáticas sofisticadas ficam para futuro.

## 29. Contradições

Evidence `contradicts` não invalida nem recalcula confidence automaticamente.

## 30. Evidence correction

Evidence é append-only na V1.

Correção por nova evidence e, quando necessário, invalidate/supersede.

## 31. Internal actors

`system`, `developer` e agents não são bypass.

## 32. Testes derivados

Obrigatórios:

```text
account isolation
user isolation
history authorization
IDOR
HTML armazenado como texto
prompt-like content como dado
duplicate remember
active-key conflict
duplicate evidence
atomic supersede
concurrent supersede
expiration
historical as_of
invalid temporal range
invalid confidence
invalid importance
invalid weight
invalid scope
invalid cursor
cursor/query mismatch
payload limits
transaction rollback
DB failure behavior
```

## 33. Regra de autoridade de IA

```text
AI OUTPUT
    ↓
proposal / data / inference

POLICY + SERVICE + PERMISSION
    ↓
authority
```

Output de IA sozinho não autoriza mudança de estado.
