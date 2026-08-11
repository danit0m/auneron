# Segurança da API

## Camadas

O Auneron usa duas camadas independentes:

1. `X-API-Key`: credencial de serviço entre Vite/Nginx e FastAPI.
2. Sessão de usuário: identidade individual e autorização.

A API key sozinha não concede acesso aos dados de negócio.

## Endpoints públicos

Permanecem públicos:

- `GET /`
- `GET /health`: liveness do processo, sem depender do PostgreSQL;
- `GET /ready`: readiness; retorna `503` quando o PostgreSQL não está
  disponível;
- documentação OpenAPI em ambientes onde estiver habilitada.

Em `production`, Swagger, ReDoc e o schema OpenAPI ficam desabilitados.

## Credencial de serviço

O proxy envia:

```text
X-API-Key
```

A chave nunca deve ser versionada ou incorporada ao JavaScript.

Configuração local no `backend/.env`:

```text
API_KEY=<chave aleatória com pelo menos 32 caracteres>
```

Para testes:

```text
TEST_API_KEY=<chave aleatória de teste com pelo menos 32 caracteres>
```

O script `scripts/test.ps1` converte temporariamente `TEST_API_KEY` em
`API_KEY` durante a suíte e restaura o ambiente ao terminar.

Comportamento da camada de serviço:

- chave correta: a requisição pode avançar para a autenticação do usuário;
- chave ausente ou incorreta: HTTP 401;
- chave não configurada: HTTP 503;
- em `production`, `API_KEY` é obrigatória.

A comparação usa `secrets.compare_digest`.

Em `production`, a configuração também falha no startup quando:

- a API key é ausente, placeholder ou possui baixa diversidade;
- `DATABASE_URL` não aponta para PostgreSQL;
- `DEBUG=true`;
- `DATABASE_ECHO=true`;
- `CORS_ORIGINS` usa wildcard, loopback ou HTTP em uma origem cross-origin.

Para frontend e API no mesmo host, prefira `CORS_ORIGINS` vazio.

## Rate limiting de autenticação

`POST /auth/login` e `POST /auth/elevate` possuem proteção por conta/usuário
e por IP. Ao atingir o limite, a resposta é `HTTP 429` com `Retry-After`.

O limiter não registra senha, e-mail bruto, IP bruto, API key ou token de
sessão. Os identificadores internos de conta/IP são hashes SHA-256.

A implementação atual é local ao processo FastAPI; escalabilidade horizontal
exige um store compartilhado antes de usar múltiplas instâncias.

## Sessão e autorização

Os routers de negócio exigem uma sessão válida além da API key.

A matriz principal é:

```text
dashboard                  -> dashboard.view
accounts GET               -> clients.view
accounts POST/PUT/DELETE   -> clients.manage
upload                     -> imports.execute
executive                  -> executive.view
brain                      -> brain.view
orchestrator decisions     -> executive.view
orchestrator operations    -> administration.ai-operations + elevação
```

Ausência ou expiração de sessão retorna HTTP 401.

Usuário autenticado sem a permissão necessária retorna HTTP 403.

Endpoints administrativos elevados também retornam HTTP 403 quando a
sessão não está temporariamente elevada.

## Browser

A SPA nunca recebe a API key.

```text
Browser -> /api -> Vite/Nginx -> FastAPI
                    + X-API-Key
```

O navegador envia somente o cookie `HttpOnly` de sessão.

## CI

A chave presente no GitHub Actions é uma credencial fictícia do ambiente
descartável de testes. Ela não deve ser reutilizada em desenvolvimento,
homologação ou produção.

O CI também valida que `users` e `auth_sessions` ficam vazios após os
testes e o E2E.

## Headers HTTP

A API adiciona:

```text
X-Content-Type-Options: nosniff
Referrer-Policy: strict-origin-when-cross-origin
X-Frame-Options: DENY
Permissions-Policy: ...
```

Em `production`, o backend também envia HSTS. O Nginx que serve a SPA aplica
headers equivalentes e uma Content Security Policy para os assets do frontend.

## Produção

Exija HTTPS. O cookie de sessão usa `Secure` em `APP_ENV=production`.

Não exponha PostgreSQL diretamente à Internet e não publique a API key em
variáveis `VITE_*`, HTML, JavaScript, `localStorage` ou `sessionStorage`.

Monitore separadamente `/health` e `/ready`: liveness não deve reiniciar um
processo saudável apenas porque o banco ficou temporariamente indisponível.
