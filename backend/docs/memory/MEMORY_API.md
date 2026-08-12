# Memory API V1 Contract

**Status:** Frozen

## 1. Endpoints

```text
POST   /memories
GET    /memories
GET    /memories/{memory_id}

POST   /memories/{memory_id}/supersede
POST   /memories/{memory_id}/invalidate
POST   /memories/{memory_id}/archive

POST   /memories/{memory_id}/evidence
GET    /memories/{memory_id}/evidence

GET    /memories/{memory_id}/history
```

Não existem na V1:

```text
DELETE /memories/{id}
PUT /memories/{id}
PATCH /memories/{id}
POST /memories/{id}/expire
bulk delete/update
admin purge
```

## 2. Criar memória

```http
POST /memories
```

Permissão: `memory:create`.

Exemplo:

```json
{
  "memory_type": "fact",
  "title": "Status de pagamento",
  "content": "A Fornecedora Sul possui pagamento vencido.",
  "memory_key": "payment_status",
  "scope": {
    "type": "account",
    "account_id": 42
  },
  "importance": 0.8,
  "confidence": 1.0,
  "valid_from": "2026-08-11T12:00:00Z",
  "valid_until": null,
  "source": {
    "type": "database",
    "reference": "accounts:42"
  },
  "context_data": {
    "currency": "BRL"
  },
  "evidence": [
    {
      "relation": "supports",
      "source_type": "database",
      "source_reference": "accounts:42",
      "evidence_text": "Registro financeiro indica pagamento vencido.",
      "weight": 1.0
    }
  ]
}
```

Campos controlados pelo servidor incluem `id`, `status`, timestamps, `created_by_user_id`, `supersedes_memory_id` e `evidence_hash`.

### Resultado

Nova memória:

```text
201 Created
```

Duplicado equivalente:

```text
200 OK
created=false
duplicate=true
```

Conflito de chave ativa com conteúdo diferente:

```text
409 Conflict
code=memory_active_key_conflict
```

## 3. Recall

```http
GET /memories
```

Permissão: `memory:read`.

Filtros V1:

```text
scope_type
account_id
subject_user_id
memory_type
status
memory_key
source_type
min_importance
min_confidence
valid_at
created_after
created_before
q
limit
cursor
sort
```

Escopo é obrigatório.

Exemplos:

```text
GET /memories?scope_type=global
GET /memories?scope_type=account&account_id=42
GET /memories?scope_type=user&subject_user_id=7
```

Combinação de scope inválida → `422`.

Status default: `active`.

Histórico exige `memory:history`.

## 4. Busca textual

Parâmetro `q`, máximo 500 caracteres.

## 5. Paginação

```text
default limit=20
max limit=100
```

Resposta:

```json
{
  "items": [],
  "page": {
    "limit": 20,
    "has_more": true,
    "next_cursor": "opaque-value"
  }
}
```

Cursor inválido → `400` com `invalid_cursor`.

## 6. Sort

Permitidos:

```text
relevance
newest
oldest
importance
confidence
```

Sem nomes arbitrários de coluna.

## 7. Get one

```http
GET /memories/{memory_id}
```

`200` ou `404` para inexistente/inacessível conforme política de segurança.

## 8. Supersede

```http
POST /memories/{memory_id}/supersede
```

Permissão: `memory:supersede`.

Request inclui novo conteúdo, importance, confidence, validade, source, reason, context e evidence.

`scope` e `memory_key` são herdados e não podem ser trocados.

Sucesso:

```text
201 Created
```

Estado inválido:

```text
409 Conflict
code=invalid_memory_state
```

## 9. Invalidate

```http
POST /memories/{memory_id}/invalidate
```

Permissão: `memory:invalidate`.

```json
{
  "reason": "A origem continha valor incorreto devido a falha de importação."
}
```

Reason obrigatório, 1..2000 caracteres.

## 10. Archive

```http
POST /memories/{memory_id}/archive
```

Permissão: `memory:archive`.

Reason opcional.

## 11. Add evidence

```http
POST /memories/{memory_id}/evidence
```

Permissão: `memory:evidence`.

```json
{
  "relation": "supports",
  "source_type": "database",
  "source_reference": "payments:991",
  "source_memory_id": null,
  "evidence_text": "Pagamento confirmado em 12/08/2026.",
  "weight": 1.0,
  "observed_at": "2026-08-12T09:00:00Z",
  "context_data": {}
}
```

Nova → `201`.

Duplicada → `200`, `duplicate=true`.

## 12. List evidence

```http
GET /memories/{memory_id}/evidence
```

Acesso deriva da memória pai.

Não expor conteúdo de `source_memory_id` protegido.

## 13. History

```http
GET /memories/{memory_id}/history
```

Permissão: `memory:history`.

Retorna a cadeia do mesmo conceito/escopo.

Recall operacional e histórico permanecem separados.

## 14. Limites de payload

| Campo | Máximo |
|---|---:|
| title | 200 chars |
| content | 10.000 chars |
| memory_key | 255 chars |
| source_reference | 500 chars |
| reason | 2.000 chars |
| evidence_text | 10.000 chars |
| q | 500 chars |
| context_data | 32 KB |
| JSON depth | 5 |
| evidence no create | 20 |
| limit | 100 |

## 15. Erro padrão

```json
{
  "error": {
    "code": "memory_active_key_conflict",
    "message": "Já existe uma memória ativa para esta chave e escopo.",
    "request_id": "abc-123"
  }
}
```

Sem SQL, stack trace ou secrets.

## 16. HTTP mapping

| Situação | HTTP |
|---|---:|
| request malformado | 400 |
| não autenticado | 401 |
| sem permissão | 403 |
| inexistente/inacessível | 404 |
| conflito | 409 |
| payload grande | 413 |
| schema inválido | 422 |
| rate limit futuro | 429 |
| erro interno | 500 |
| DB obrigatória indisponível | 503 |

## 17. Cache e logs

Preferir `Cache-Control: no-store` nos endpoints sensíveis inicialmente.

Não logar bodies completos de escrita.

## 18. Browser path

```text
Browser
   ↓
Nginx/Vite adiciona API key server-side
   ↓
FastAPI
   ↓
sessão do usuário
   ↓
RBAC
```

Nenhum secret server-side novo vai para o bundle do frontend.
