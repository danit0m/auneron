# Memory Architecture V1

**Status:** Frozen

## 1. Fronteiras

```text
Browser / Agent / Internal Component
                │
                ▼
        Authentication / RBAC
                │
                ▼
          MemoryService
        ┌───────┼────────┐
        │       │        │
 validation  scope   lifecycle
        │       │        │
        └───────┼────────┘
                ▼
        MemoryRepository
        ┌───────┼───────────┐
        │       │           │
     queries  locking   persistence
        │       │           │
        └───────┼───────────┘
                ▼
           SQLAlchemy
                │
                ▼
           PostgreSQL
       ┌────────┴─────────┐
       │                  │
 memory_items      memory_evidence
```

Brain, agentes e orchestrator nunca acessam `MemoryRepository` diretamente.

## 2. Memória versus trabalho

```text
MEMORY
"O que sabemos?"

WORK MANAGER
"O que precisa ser feito?"
```

Tarefas, ações e workflows pertencem ao Commit 22.

## 3. Semântica

Uma memória é informação registrada com:

- tipo;
- conteúdo;
- proveniência;
- confiança;
- importância;
- validade temporal;
- estado;
- evidência opcional.

`fact` é diferente de `observation`. Inferência não deve ser tratada automaticamente como fato.

## 4. Escopos

### Global
`scope_type=global`, sem `account_id` e sem `subject_user_id`.

### Account
`scope_type=account`, com `account_id`.

### User
`scope_type=user`, com `subject_user_id`.

User scope é contexto operacional, não mecanismo genérico de perfil pessoal.

## 5. Supersession

Conteúdo antigo não é editado.

```text
Memory 100
saldo = 10.000
status = superseded
        ↓
Memory 145
saldo = 6.000
status = active
supersedes_memory_id = 100
```

## 6. Evidência

Relações V1:

```text
supports
contradicts
context
```

Evidência contraditória não invalida automaticamente a memória e não recalcula `confidence` automaticamente.

## 7. Proveniência

Toda memória possui:

```text
source_type
source_reference
```

Tipos iniciais:

```text
database
upload
user
agent
system
api
derived
```

## 8. Importância e confiança

Ambas variam de `0.000` a `1.000`, mas representam conceitos diferentes.

`confidence` é obrigatória e não possui default silencioso.

## 9. Tempo

Valid time:

```text
valid_from
valid_until
```

System time:

```text
created_at
updated_at
```

## 10. Recall padrão

```text
status = active
AND valid_from <= as_of
AND (valid_until IS NULL OR valid_until > as_of)
```

Default `as_of`: agora em UTC.

## 11. Ranking

Sem busca textual:

```text
importance DESC
confidence DESC
valid_from DESC
id DESC
```

Com busca textual:

```text
text_rank DESC
importance DESC
confidence DESC
valid_from DESC
id DESC
```

## 12. Paginação

Keyset/cursor pagination:

```text
default = 20
maximum = 100
```

Cursor opaco, vinculado aos filtros da consulta e protegido contra adulteração.

## 13. Manutenção

Expiração em lotes com transações curtas e `FOR UPDATE SKIP LOCKED`.

A correção de leitura não depende do worker, pois `recall()` sempre aplica `valid_until`.

## 14. Regra transacional

Nenhuma chamada de IA, HTTP externo ou operação externa lenta dentro de transação de banco.

## 15. Compatibilidade com `knowledge`

O Auneron já possui a tabela/serviço `knowledge`, usada por agentes, rotas do Brain e Executive Service.

`knowledge` e Memory V1 possuem responsabilidades diferentes:

```text
knowledge
→ sinal/insight operacional existente
→ resolved/reopen/delete
→ compatibilidade com agentes atuais

memory
→ memória empresarial persistente
→ provenance/confidence/evidence/temporalidade
→ immutable content + lifecycle auditável
```

Durante 21B–21F:

- `knowledge` não é alterada;
- não há migração automática dos registros existentes;
- não há dual-write;
- Memory usa tabelas, models, repository e service próprios;
- agentes atuais continuam funcionando com `KnowledgeService`.

Em 21G, uma ponte controlada poderá promover itens selecionados de `knowledge` para Memory por meio de `MemoryService`. Essa ponte será unidirecional na V1 e preservará proveniência, por exemplo com referência lógica `knowledge:<id>`.

A eventual depreciação de `knowledge` não faz parte do Commit 21 e só poderá ocorrer depois que todas as dependências atuais forem migradas e testadas.

## 16. Extensões futuras

Vector search, retenção, audit ledger, confidence policy, semantic deduplication, links com Work Manager e políticas de autonomia poderão ser adicionados sem quebrar a fronteira do `MemoryService`.
