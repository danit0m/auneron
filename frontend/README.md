# Auneron Frontend

Frontend do Auneron construído com React, TypeScript e Vite.

## Desenvolvimento

```powershell
npm ci
Copy-Item .env.example .env.local
npm run dev
```

Exemplo:

```text
AUNERON_BACKEND_URL=http://127.0.0.1:8000
AUNERON_API_KEY=<mesma API_KEY configurada no backend>
```

Essas variáveis pertencem ao processo Node do Vite e não ao bundle React.

## Autenticação

O navegador usa `/api` e mantém a sessão individual por cookie
`HttpOnly`.

Fluxo:

```text
/login
  -> POST /api/auth/login
  -> cookie HttpOnly
  -> GET /api/auth/me
  -> rotas protegidas
```

Ao recarregar a aplicação, `AuthProvider` restaura a sessão com
`/auth/me`.

O logout chama `/auth/logout`.

## Segurança da integração

Em desenvolvimento, Vite encaminha `/api` ao FastAPI e injeta
`X-API-Key` no processo Node.

Em produção, Nginx/reverse proxy exerce a mesma função.

A API key nunca deve ser publicada como `VITE_*`, JavaScript,
`localStorage` ou `sessionStorage`.

O frontend aplica RBAC para navegação, enquanto o FastAPI aplica a
autorização efetiva nas rotas de negócio.

## Elevação

AI Operations e outros recursos administrativos sensíveis podem exigir
revalidação da senha.

O frontend usa `/auth/elevate` e `/auth/elevation/revoke`. A elevação é
mantida no servidor e restaurada por `/auth/me`.

Não existe `VITE_ELEVATED_DEV_CODE`.

## Scripts

```powershell
npm run dev
npm run lint
npm run build
npm run preview
```

## E2E

O E2E é coordenado pelo backend:

```powershell
cd ..\backend
python .\scripts\e2e_frontend.py
```

Na primeira execução:

```powershell
python -m playwright install chromium
```

O E2E cria um usuário descartável, realiza login real, valida Dashboard,
reload de sessão e Clientes, e remove o usuário ao final.

## Produção

```powershell
docker build -f frontend/Dockerfile -t auneron-frontend:local .
```

A imagem final usa Nginx para:

- servir o SPA;
- encaminhar `/api` ao backend;
- adicionar `X-API-Key` no servidor;
- preservar `X-Request-ID`;
- encaminhar o cookie de sessão.

`AUNERON_API_KEY` é necessária no container Nginx em runtime, não durante
o build React.

Consulte:

```text
../backend/docs/FRONTEND_INTEGRATION.md
../backend/docs/API_SECURITY.md
../backend/docs/AUTHENTICATION.md
../backend/docs/DEPLOYMENT.md
```
