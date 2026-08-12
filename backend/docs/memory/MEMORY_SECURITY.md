# Memory Security & RBAC V1

**Status:** Frozen

## 1. Modelo de segurança

```text
Authentication
      ↓
RBAC
      ↓
Scope authorization
      ↓
MemoryService
      ↓
MemoryRepository
      ↓
PostgreSQL constraints
```

Existir no banco não significa estar acessível.

## 2. Permissões V1

```text
memory:read
memory:create
memory:evidence
memory:supersede
memory:invalidate
memory:archive
memory:history
memory:read_user_scope
memory:read_global
memory:manage_global
```

A implementação deve reutilizar a estrutura de RBAC server-side já existente no Auneron, sem criar um segundo sistema de autorização.

## 3. Matriz proposta

| Role | Read | Create | Evidence | Supersede | Invalidate | Archive | History | Manage global |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| `viewer` | sim | não | não | não | não | não | não | não |
| `analyst` | sim | sim | sim | não | não | não | sim | não |
| `manager` | sim | sim | sim | sim | sim | sim | sim | não |
| `executive` | sim | sim | sim | sim | sim | sim | sim | não |
| `administrator` | sim | sim | sim | sim | sim | sim | sim | sim |
| `developer` | sim | sim | sim | sim | sim | sim | sim | sim |

## 4. Viewer

Acesso operacional somente leitura.

Por padrão vê:

```text
active
+
temporalmente válida
+
escopo autorizado
```

Histórico exige `memory:history`.

## 5. Analyst

Pode criar memória e evidence, mas não altera lifecycle de memória estabelecida na V1.

## 6. Manager / Executive

Podem `supersede`, `invalidate` e `archive`.

## 7. Global scope

Leitura global exige capacidade apropriada.

Escrita/lifecycle global exige:

```text
memory:manage_global
```

Agentes não criam memória global arbitrariamente.

## 8. Account scope

Exige:

1. permissão da operação;
2. autorização para o `account_id` alvo.

Role elevado sozinho não deve substituir verificação de escopo.

## 9. User scope

É contexto operacional, não mecanismo de perfil pessoal irrestrito.

Não deve ser usado casualmente para armazenar:

- características pessoais sensíveis;
- perfil psicológico;
- dados médicos;
- credenciais;
- dados pessoais desnecessários.

Manager não recebe acesso irrestrito a todos os users apenas pelo cargo.

## 10. Autoria não é propriedade

`created_by_user_id` registra quem criou.

Acesso é determinado por:

```text
scope + RBAC + scope authorization
```

## 11. Agents

Agents chamam `MemoryService`, nunca `MemoryRepository`.

Usam contexto explícito e least privilege.

Exemplo:

```text
memory:read
memory:create
memory:evidence
```

sem permissões administrativas quando não necessárias.

## 12. `system` e `developer`

Nenhum deles significa bypass de auth/scope/policy.

## 13. Proveniência de IA

Memória de agent deve permanecer distinguível:

```text
source_type = agent
source_reference = <agent_name>
```

Se houve solicitação humana, o iniciador pode ser registrado separadamente em contexto sem falsificar autoria.

## 14. Histórico

Pedir `superseded`, `expired`, `invalidated` ou `archived` sem `memory:history` gera erro explícito; o sistema não reduz silenciosamente a consulta para `active`.

## 15. IDOR

Conhecer `memory_id` não concede acesso.

Objeto fora do escopo pode responder `404` para não revelar existência.

Ausência de permissão geral da operação responde `403`.

## 16. Evidências

Evidence herda autorização da memória pai.

Referência para outra memória não pode revelar conteúdo protegido da memória fonte.

## 17. Lifecycle reasons

```text
invalidate → reason obrigatório
supersede  → reason obrigatório
archive    → reason opcional na V1
```

## 18. Hard delete

Não existe DELETE operacional comum.

Hard delete/purge/retention override ficam para governança futura e podem exigir elevação.

Entidades que definem o escopo de Memory também não podem apagar memória por efeito colateral. A V1 protege `account_id` e `subject_user_id` com `ON DELETE RESTRICT`.

Consequência operacional: tentativa de excluir uma entidade pai ainda referenciada por Memory deve ser convertida pela camada de aplicação em conflito controlado, e não em exclusão em cascata nem erro SQL exposto.

## 19. Conteúdo não confiável

HTML, JavaScript, prompt text, URLs e conteúdo externo são tratados como dados.

Frontend escapa por padrão.

Memória recuperada nunca recebe automaticamente autoridade de system prompt.

## 20. Segredos

Não armazenar deliberadamente em memory fields:

```text
passwords
API keys
session tokens
private keys
```

Release Guard e política de segredos existentes continuam valendo.

## 21. Limites

```text
title            200 chars
content          10.000 chars
memory_key       255 chars
source_reference 500 chars
reason           2.000 chars
evidence_text    10.000 chars
text_query       500 chars
context_data     32 KB
JSON depth       5
evidence/create  20
page limit       100
```

## 22. Logging

Eventos possíveis:

```text
memory.created
memory.superseded
memory.invalidated
memory.archived
memory.evidence_added
memory.access_denied
memory.conflict
```

Nunca logar corpo completo de memória/evidence ou segredos.

## 23. Invariantes

- autenticação obrigatória;
- API key de serviço permanece server-side;
- sessão identifica o usuário;
- RBAC precede operação;
- scope explícito;
- ID não é autorização;
- histórico separado;
- user scope protegido;
- global write privilegiado;
- agents seguem least privilege;
- developer não tem bypass;
- memory content é untrusted data;
- lifecycle preserva histórico;
- logs não expõem conteúdo sensível.
