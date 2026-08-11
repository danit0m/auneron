# Autenticação de usuários

## Separação de credenciais

O Auneron mantém duas camadas independentes:

1. `X-API-Key`: credencial de serviço entre Vite/Nginx e FastAPI.
2. Sessão de usuário: cookie `HttpOnly` emitido após login.

A API key nunca é enviada ao JavaScript e não substitui a identidade do
usuário.

## Senhas

Senhas não são armazenadas em texto puro.

O backend usa `hashlib.scrypt` com salt aleatório por usuário. O valor
persistido contém somente parâmetros do algoritmo, salt e hash.

## Sessões

`POST /auth/login` cria um token aleatório. O token bruto fica somente no
cookie `HttpOnly`. O PostgreSQL armazena apenas o SHA-256 do token.

Configuração padrão:

```text
AUTH_COOKIE_NAME=auneron_session
AUTH_SESSION_TTL_MINUTES=480
AUTH_ELEVATION_TTL_MINUTES=10
```

Em `production`, o cookie é `Secure`. Em todos os ambientes ele usa
`HttpOnly`, `SameSite=Strict` e `Path=/`.

Sessões expiradas são removidas automaticamente. Sessões revogadas ainda
válidas podem ser mantidas por uma janela curta para diagnóstico e depois
também são removidas.

Configuração padrão da manutenção:

```text
AUTH_SESSION_CLEANUP_INTERVAL_SECONDS=3600
AUTH_REVOKED_SESSION_RETENTION_HOURS=24
```

A limpeza é executada na inicialização quando o PostgreSQL está disponível e
depois periodicamente durante o processo da API.

## Endpoints

```text
POST /auth/login
GET  /auth/me
POST /auth/logout
POST /auth/elevate
POST /auth/elevation/revoke
```

Todos passam primeiro pela credencial de serviço `X-API-Key`.

## Proteção contra tentativas repetidas

Login e elevação possuem rate limiting para reduzir brute force e password
spraying.

Configuração padrão:

```text
AUTH_LOGIN_ACCOUNT_MAX_FAILURES=5
AUTH_LOGIN_IP_MAX_FAILURES=25
AUTH_LOGIN_WINDOW_SECONDS=900
AUTH_ELEVATION_USER_MAX_FAILURES=5
AUTH_ELEVATION_IP_MAX_FAILURES=15
AUTH_ELEVATION_WINDOW_SECONDS=600
```

Quando o limite é atingido, a API responde `HTTP 429 Too Many Requests` e
inclui `Retry-After`.

Identificadores de conta e IP usados pelo limiter são mantidos somente como
SHA-256 em memória. Senhas, API keys e tokens de sessão não são armazenados
pelo limiter nem incluídos nos eventos de segurança.

O limiter atual é por processo. Antes de executar várias instâncias FastAPI
em paralelo, substitua-o por um armazenamento compartilhado apropriado.

## Papéis

```text
viewer
analyst
manager
executive
administrator
developer
```

A matriz de permissões é aplicada tanto no frontend quanto no backend.
O FastAPI é a fronteira de segurança.

Exemplos:

```text
clients.view
clients.manage
dashboard.view
imports.execute
executive.view
brain.view
administration.ai-operations
developer.ui-showcase
```

## Elevação

Recursos administrativos sensíveis exigem, além da permissão do papel,
uma sessão elevada.

`POST /auth/elevate` revalida a senha do usuário e grava
`elevated_until` na sessão atual.

A elevação padrão dura 10 minutos. Ela pode ser revogada antes do prazo
por `POST /auth/elevation/revoke`.

A elevação não é mantida em `localStorage` ou `sessionStorage`. Após
recarregar a página, o frontend recupera o estado pelo `/auth/me`.

## Criar o primeiro usuário

Nunca coloque a senha administrativa em `.env`, Compose, linha de comando
ou Git.

Com o backend local configurado e a migration aplicada:

```powershell
python -m scripts.create_user `
    --name "Administrador" `
    --email "admin@example.com" `
    --role administrator
```

O script solicita a senha duas vezes via `getpass`.

Com Docker Compose:

```powershell
docker compose exec backend `
    python -m scripts.create_user `
    --name "Administrador" `
    --email "admin@example.com" `
    --role administrator
```

Use um endereço real apropriado ao ambiente em vez de manter
`admin@example.com` em produção.

## Migration

A revisão `8b0c5f3e2a19` cria:

- `users`;
- `auth_sessions`;
- constraints de papel, e-mail e expiração;
- FK de sessão para usuário com `ON DELETE CASCADE`;
- índices operacionais de sessão.

Ela sucede `057e1ffeec3c`.

## Testes

A suíte valida login, cookie HttpOnly, senha inválida, usuário inativo,
`/auth/me`, logout, elevação, API key, sessão e RBAC.

O E2E realiza login real e prova que a sessão é restaurada após reload.
