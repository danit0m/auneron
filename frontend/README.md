# Auneron Frontend

Frontend do Auneron construído com React, TypeScript e Vite.

## Desenvolvimento

Na pasta `frontend`:

```powershell
npm ci
Copy-Item .env.example .env.local
npm run dev
```

Exemplo de configuração local:

```text
AUNERON_BACKEND_URL=http://127.0.0.1:8000
AUNERON_API_KEY=<mesma API_KEY configurada no backend>
```

As duas variáveis são consumidas pelo processo Node/Vite. Elas não usam o
prefixo `VITE_`, portanto não devem ser incorporadas ao bundle entregue ao
navegador.

## Autenticação

O frontend usa autenticação real do backend.

Fluxo principal:

```text
Navegador
  -> /api/auth/login
  -> sessão opaca no backend
  -> cookie HttpOnly
  -> /api/auth/me restaura a sessão após reload
```

A senha não é armazenada pelo frontend. O token bruto da sessão permanece
somente no cookie HttpOnly e o backend mantém apenas o hash correspondente no
PostgreSQL.

O logout chama `/api/auth/logout` e revoga a sessão no servidor.

## Elevação de acesso

Operações administrativas usam elevação server-side.

A interface solicita novamente a senha da conta e chama `/api/auth/elevate`.
O backend valida a credencial, registra a elevação temporária na sessão e
aplica RBAC e permissões no servidor. O frontend não possui código secreto de
desenvolvimento para liberar áreas administrativas.

## Segurança da integração

O navegador utiliza URLs relativas `/api`.

Em desenvolvimento, o Vite encaminha essas chamadas ao FastAPI e adiciona
`X-API-Key` no processo Node. A API key não deve ser publicada em uma variável
com prefixo `VITE_`, porque esse tipo de variável pode ser incorporado ao
JavaScript entregue ao navegador.

Em produção, o build continua usando `/api`. O Nginx/reverse proxy encaminha
as chamadas para o FastAPI e injeta `X-API-Key` no lado servidor.

A autenticação do usuário e a API key cumprem papéis diferentes:

- `X-API-Key`: credencial de serviço entre proxy e backend;
- cookie de sessão: identidade do usuário;
- RBAC: autorização de negócio;
- elevação temporária: autorização adicional para operações sensíveis.

Consulte:

```text
../backend/docs/FRONTEND_INTEGRATION.md
../backend/docs/API_SECURITY.md
../backend/docs/AUTHENTICATION.md
../backend/docs/DEPLOYMENT.md
```

## Scripts

```powershell
npm run dev
npm run lint
npm run build
npm run preview
```

`npm run build` executa TypeScript antes da geração do bundle.

## E2E

O E2E é coordenado pelo backend:

```powershell
cd ..\backend
python .\scripts\e2e_frontend.py
```

Na primeira execução local:

```powershell
python -m playwright install chromium
```

O teste E2E cobre login real, restauração da sessão após reload, acesso ao
Dashboard e Clientes e uso do proxy `/api` sem publicar a API key no bundle.

## Produção

A imagem de produção é multi-stage:

```powershell
docker build -f frontend/Dockerfile -t auneron-frontend:local .
```

A etapa Node gera os arquivos estáticos. A etapa final usa Nginx para:

- servir o SPA;
- encaminhar `/api` ao backend;
- adicionar `X-API-Key` no lado servidor;
- preservar `X-Request-ID`;
- aplicar headers de segurança;
- manter a API key fora do bundle React.

A variável `AUNERON_API_KEY` é necessária no container Nginx em runtime, não
durante o build do React.
