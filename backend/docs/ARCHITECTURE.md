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
- observabilidade estruturada.

A aplicação testa a conectividade com o banco no startup e libera o pool
no shutdown.

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
- produção exige configuração explícita da segurança;
- segredos não devem existir no código-fonte.

## Testes

A suíte possui dois níveis principais.

### Backend

Pytest valida endpoints, banco, constraints, segurança, configuração e
observabilidade.

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

## Limites atuais

A arquitetura atual ainda não implementa autenticação individual real de
usuário no backend. A sessão e a elevação existentes no frontend possuem
escopo de desenvolvimento/interface.

Antes de disponibilizar o Auneron publicamente para múltiplos usuários,
é necessário implementar autenticação e autorização no backend, além de
HTTPS obrigatório.
