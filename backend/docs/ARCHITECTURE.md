# Arquitetura do Auneron

## Visão geral

O Auneron está organizado em três camadas principais:

```text
Frontend React/Vite
        |
        | HTTP /api
        v
FastAPI + agentes/orquestrador
        |
        | SQLAlchemy / Psycopg
        v
PostgreSQL 17
```

## Frontend

Responsabilidades:

- navegação e apresentação;
- controle de permissões da interface;
- dashboards, clientes, Brain e centro executivo;
- geração e propagação de `X-Request-ID`;
- tratamento de falhas HTTP;
- integração com a API somente por URLs relativas `/api`.

O controle de permissões no React melhora a experiência de uso, mas não
deve ser considerado uma barreira de segurança suficiente para dados ou
operações sensíveis.

## Borda HTTP

### Desenvolvimento

```text
Browser -> Vite /api -> FastAPI
                    + X-API-Key
```

O processo Node do Vite conhece a API key; o bundle React não.

### Produção

```text
Browser -> HTTPS -> Nginx/reverse proxy -> FastAPI
                                      + X-API-Key
```

A API key permanece no lado servidor.

Essa chave é uma credencial de serviço. Ela não substitui autenticação
individual de usuário.

## Backend

O FastAPI contém:

- endpoints públicos de infraestrutura;
- routers de negócio protegidos por `X-API-Key`;
- serviços de aplicação;
- agentes e orquestrador;
- SQLAlchemy;
- configuração por ambiente;
- observabilidade estruturada;
- headers HTTP de segurança;
- rate limiting de login e elevação;
- liveness em `/health` e readiness em `/ready`;
- manutenção periódica de sessões expiradas/revogadas.

A aplicação testa a conectividade com o banco no startup e libera o pool
no shutdown. O resultado da checagem inicial é registrado, mas `/health`
permanece independente do banco; disponibilidade do PostgreSQL é refletida
por `/ready`.

## Banco de dados

Persistência principal:

```text
PostgreSQL 17
```

Evolução de schema:

```text
Alembic
```

O SQLite presente como artefato histórico não é a persistência principal
do runtime atual.

A aplicação utiliza pool de conexões com limites e timeouts definidos por
configuração. Migrações Alembic utilizam conexões separadas do pool da API.

## Ambientes

Ambientes reconhecidos pela configuração incluem desenvolvimento, teste e
produção.

Proteções relevantes:

- o ambiente de teste só pode utilizar o banco `auneron_test`;
- o banco `auneron_test` não pode ser usado como banco normal;
- produção exige PostgreSQL e configuração explícita da segurança;
- produção rejeita API key fraca/placeholder, debug e echo de SQL;
- CORS de produção aceita somente origens HTTPS explícitas quando cross-origin;
- segredos não devem existir no código-fonte ou no bundle React.

## Testes

A suíte possui dois níveis principais.

### Backend

Pytest valida endpoints, banco, constraints, segurança, configuração,
observabilidade, rate limiting, liveness/readiness e manutenção de sessões.

### E2E

Playwright abre um navegador real e valida:

```text
Browser -> Vite -> FastAPI -> PostgreSQL de teste
```

O E2E utiliza `auneron_test`.

## Observabilidade

Toda requisição possui `X-Request-ID`.

Logs HTTP estruturados incluem, entre outros:

- método;
- caminho;
- status;
- duração;
- ambiente;
- request ID.

Credenciais, URLs de banco, cookies, tokens e outros campos sensíveis
devem permanecer mascarados.

Eventos de autenticação registram sucesso/falha, elevação, logout e excesso
de tentativas sem incluir senha, token bruto, cookie ou API key.

## Limites atuais

A arquitetura atual implementa autenticação individual real de
usuário no backend.

O navegador autentica por `POST /auth/login` e recebe um cookie de sessão
`HttpOnly`. O token bruto não é exposto ao JavaScript; o PostgreSQL armazena
somente o hash do token.

A `X-API-Key` permanece como credencial de serviço entre Vite/Nginx e
FastAPI. Ela não identifica o usuário e, sozinha, não concede acesso às
rotas de negócio.

O FastAPI aplica RBAC server-side às rotas protegidas. Operações
administrativas sensíveis exigem também uma elevação temporária vinculada
à sessão atual.

O frontend replica a matriz de permissões para navegação e experiência de
uso, mas a fronteira efetiva de segurança permanece no backend.

O rate limiter de autenticação atual é em memória e por processo. A topologia
atual utiliza um processo backend; antes de escalar horizontalmente, o limiter
deve migrar para um armazenamento compartilhado.

O `backend/docker-compose.yml` é uma topologia local de desenvolvimento.
Produção deve usar HTTPS, segredos gerenciados e PostgreSQL não exposto
publicamente.
