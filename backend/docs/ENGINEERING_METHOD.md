# Auneron Engineering Method

**Status:** Método oficial de construção para mudanças arquiteturalmente relevantes
**Aplicação principal:** roadmap 21–30

## 1. Princípio

```text
DESIGN
   ↓
THREAT MODEL
   ↓
ARCHITECTURE FREEZE
   ↓
IMPLEMENT
   ↓
VALIDATE
   ↓
RELEASE
```

Decisões importantes não devem existir apenas em conversa ou código. Elas precisam estar versionadas no repositório.

## 2. Sequência padrão

```text
1. Objective & Scope
2. Architecture
3. Data Model
4. Service Contracts
5. Repository / Persistence
6. Security & RBAC
7. API Contract
8. Threat Model & Failure Modes
9. Architecture Freeze
10. Database / Migrations
11. Implementation
12. Tests
13. Operational Validation
14. Documentation Review
15. Controlled Staging
16. Commit
17. Push
18. CI Confirmation
```

Nem todo commit exige todos os artefatos, mas qualquer etapa relevante omitida deve ser uma decisão consciente.

## 3. Design

Antes do código:

- definir objetivo;
- definir escopo;
- definir não objetivos;
- definir fronteiras;
- evitar antecipar funcionalidades de commits futuros;
- preferir comportamento determinístico e policy explícita antes de comportamento autônomo.

## 4. Data Model

Antes da migration definir:

- entidades;
- tipos;
- relacionamentos;
- lifecycle;
- FKs e delete behavior;
- constraints;
- índices;
- temporalidade;
- retenção/impacto de exclusão.

Integridade crítica deve ser reforçada pelo PostgreSQL quando prático.

## 5. Service Contract

Definir:

- operações públicas;
- invariantes;
- transações;
- idempotência;
- state transitions;
- domain errors;
- responsabilidades e não responsabilidades.

## 6. Repository

Definir:

- scope explícito;
- filtros;
- locking;
- ordering;
- pagination;
- concorrência;
- responsabilidades de persistência.

Repository não é escape hatch de SQL.

## 7. Security

Definir antes da API:

- autenticação;
- RBAC;
- scope authorization;
- least privilege;
- dados sensíveis;
- logging;
- elevation/admin boundaries;
- capabilities de actors internos.

## 8. API

Congelar:

- endpoints;
- requests/responses;
- campos server-owned;
- limites;
- pagination;
- status HTTP;
- error format;
- cache/logging.

Router deve ser camada fina sobre domain services.

## 9. Threat Model

Perguntar:

```text
O que pode dar errado?
Como detectar?
Como limitar impacto?
Como recuperar?
Qual teste prova a mitigação?
```

Revisar confidencialidade, integridade, disponibilidade, concorrência, partial failures, autorização, payload abuse, secrets, banco, migration e ameaças específicas de IA.

## 10. Architecture Freeze

No freeze:

- consolidar decisões em docs versionados;
- marcar status/versão;
- registrar invariantes;
- registrar não objetivos;
- registrar itens adiados.

Mudança posterior que altere contrato congelado exige atualização consciente da documentação.

Freeze evita drift acidental; não impede evolução justificada.

## 11. Implementação

Implementar em fases pequenas e testáveis.

Preferência:

```text
database
→ service/domain
→ retrieval
→ API/security
→ integrations
```

Evitar refactors não relacionados no mesmo commit.

## 12. Validation Pyramid

Usar conforme aplicável:

```text
static checks
unit tests
schema/database tests
integration tests
security tests
E2E
Docker smoke
release guards
CI
```

Teste local verde não equivale a release concluído.

## 13. Migration Discipline

Antes de migration em dados relevantes:

- entender a operação;
- garantir backup quando necessário;
- validar no banco isolado de teste;
- `alembic heads`;
- `alembic current`;
- aplicar;
- confirmar current=head;
- verificar dados críticos.

Rollback de produção não deve assumir downgrade destrutivo seguro.

## 14. Secret Discipline

Nunca commitar:

```text
real .env
passwords
API keys
session tokens
private keys
production credential URLs
```

Exemplos usam placeholders.

Release Guard deve continuar impedindo secrets server-side no bundle frontend.

## 15. Controlled Staging

Para commits sensíveis, evitar `git add .`.

Stage explícito e revisar:

```text
git status --short
git diff --cached --name-only
git diff --cached --check
git diff --cached --stat
```

ZIPs, audits e helpers temporários não entram por acidente.

## 16. Critério de conclusão

Um commit não termina apenas porque `git commit` funcionou.

A evidência normalmente inclui:

```text
tests green
migration state correto
frontend checks green
Docker/smoke green quando aplicável
Release Guard green
working tree clean
push successful
CI successful
```

Nunca declarar CI verde sem evidência.

## 17. Documentation as Product

Docs são especificação, não decoração histórica.

Mudança de comportamento que altera contrato documentado deve atualizar o documento correspondente.

## 18. Regra de autoridade da IA

```text
AI OUTPUT
   ↓
proposal / data / inference

POLICY + SERVICE + PERMISSION
   ↓
authority
```

Model output sozinho não autoriza ação.

Essa regra é crítica principalmente em Agent Skills, Approval & Autonomy, Proactivity, Learning e Integrations.

## 19. Definition of Done

Um roadmap commit está concluído quando:

1. escopo planejado implementado;
2. arquitetura congelada e código concordam;
3. migrations corretas;
4. testes relevantes verdes;
5. checks operacionais/security verdes;
6. docs atuais;
7. apenas arquivos previstos commitados;
8. working tree limpo;
9. push concluído;
10. CI confirmada com sucesso.

## 20. Resumo

```text
Understand
   ↓
Design
   ↓
Model
   ↓
Secure
   ↓
Threat-model
   ↓
Freeze
   ↓
Implement
   ↓
Test
   ↓
Operate
   ↓
Review
   ↓
Commit
   ↓
CI
```
