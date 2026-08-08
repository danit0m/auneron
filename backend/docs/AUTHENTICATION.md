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

## Endpoints

```text
POST /auth/login
GET  /auth/me
POST /auth/logout
POST /auth/elevate
POST /auth/elevation/revoke
```

Todos passam primeiro pela credencial de serviço `X-API-Key`.

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
