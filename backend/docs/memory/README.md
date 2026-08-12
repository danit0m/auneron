# Auneron Memory System V1

**Commit:** 21 — Memory System
**Phase:** 21A.8 — Architecture Freeze
**Status:** Arquitetura V1 congelada para implementação

## Objetivo

O Memory System é a camada de memória empresarial persistente do Auneron. Ele registra o que o sistema sabe, quando a informação é válida, de onde veio, qual sua confiança, sua importância e como evoluiu.

```text
PERCEBER
   ↓
ENTENDER
   ↓
LEMBRAR
   ↓
DECIDIR
   ↓
AGIR
   ↓
ACOMPANHAR
```

Memória não é verdade absoluta. Cada memória deve preservar proveniência, confiança, validade temporal, estado e evidências quando existirem.

## Princípios V1

1. PostgreSQL é a fonte de verdade.
2. Estrutura e busca relacional/textual vêm antes de embeddings.
3. Brain, agentes, routers e orchestrator não gravam diretamente nas tabelas.
4. `MemoryService` é a fronteira de domínio.
5. `MemoryRepository` persiste e consulta, mas não decide RBAC nem faz `commit`.
6. Conteúdo existente não é editado; conhecimento muda por `supersede()` ou `invalidate()`.
7. `recall()` operacional retorna somente memória ativa e temporalmente válida por padrão.
8. Histórico exige acesso explícito.
9. Proveniência e `confidence` são obrigatórias.
10. Conteúdo recuperado é dado não confiável, nunca instrução de sistema.
11. Não existe `DELETE` operacional comum na V1.
12. Vector DB, aprendizado autônomo e execução autônoma ficam fora do Commit 21.

## Documentos

- `MEMORY_ARCHITECTURE.md`
- `MEMORY_DATA_MODEL.md`
- `MEMORY_SERVICE.md`
- `MEMORY_REPOSITORY.md`
- `MEMORY_SECURITY.md`
- `MEMORY_API.md`
- `MEMORY_THREAT_MODEL.md`
- `MEMORY_KNOWLEDGE_COMPATIBILITY.md`

Método geral do projeto:

```text
backend/docs/ENGINEERING_METHOD.md
```

## Escopo do Commit 21

```text
21A — Architecture & Freeze
21B — Database & Alembic
21C — Memory Service
21D — Retrieval & Indexing
21E — API & Security
21F — Tests & Operations
21G — Brain Integration
```

## Tipos

```text
fact
event
observation
decision
summary
```

## Escopos

```text
global
account
user
```

## Lifecycle

```text
active
  ├── superseded
  ├── invalidated
  ├── archived
  └── expired
```

Estados finais não são reativados na V1.

## Não objetivos

O Commit 21 não implementa Work Manager, integrações externas, hard delete administrativo, políticas completas de retenção, treinamento de modelos, vector search ou autonomia.

## Regra do Architecture Freeze

Depois do freeze, qualquer implementação que altere invariantes, schema, lifecycle, autorização ou contrato público deve primeiro atualizar conscientemente a documentação correspondente.


## Compatibilidade com `knowledge`

O sistema legado `knowledge` permanece ativo durante o Commit 21. Ele representa sinais/insights operacionais produzidos por agentes e já é consumido por rotas do Brain e pelo Executive Service.

O Memory System V1 não substitui, migra nem modifica `knowledge` durante 21B–21F.

A integração será deliberada em 21G, por meio de uma ponte unidirecional controlada (`knowledge` → `MemoryService`) para itens selecionados. Não haverá dual-write automático na fase inicial.

Detalhes: `MEMORY_KNOWLEDGE_COMPATIBILITY.md`.
