# Segurança da API

## Camadas

O Auneron usa duas camadas independentes:

1. `X-API-Key`: credencial de serviço entre Vite/Nginx e FastAPI.
2. Sessão de usuário: identidade individual e autorização.

A API key sozinha não concede acesso aos dados de negócio.

## Endpoints públicos

Permanecem públicos:

- `GET /`
- `GET /health`
- documentação OpenAPI em ambientes onde estiver habilitada.

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

## Produção

Exija HTTPS. O cookie de sessão usa `Secure` em `APP_ENV=production`.

Não exponha PostgreSQL diretamente à Internet e não publique a API key em
variáveis `VITE_*`, HTML, JavaScript, `localStorage` ou `sessionStorage`.
