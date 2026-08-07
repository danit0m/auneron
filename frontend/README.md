# Auneron Frontend

Frontend do Auneron construído com React, TypeScript e Vite.

## Desenvolvimento

```powershell
npm ci
Copy-Item .env.example .env.local
npm run dev
```

Exemplo de configuração local:

```text
AUNERON_BACKEND_URL=http://127.0.0.1:8000
AUNERON_API_KEY=<mesma API_KEY configurada no backend>
VITE_ELEVATED_DEV_CODE=<credencial exclusivamente local>
```

## Segurança da integração

O navegador utiliza URLs relativas `/api`.

Em desenvolvimento, o Vite encaminha essas chamadas ao FastAPI e
adiciona `X-API-Key` no processo Node. A chave não deve ser publicada
como variável `VITE_*`, pois variáveis desse tipo podem ser incorporadas
ao JavaScript entregue ao navegador.

Em produção, o build também continua usando `/api`. Um reverse proxy
ou BFF deve encaminhar essas chamadas para o FastAPI e adicionar a
credencial no lado servidor.

Consulte:

```text
../backend/docs/FRONTEND_INTEGRATION.md
../backend/docs/API_SECURITY.md
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

## Produção

A imagem de produção é multi-stage:

```powershell
docker build -f frontend/Dockerfile -t auneron-frontend:local .
```

A etapa Node gera os arquivos estáticos. A etapa final usa Nginx para:

- servir o SPA;
- encaminhar `/api` ao backend;
- adicionar `X-API-Key` no lado servidor;
- preservar o fluxo de `X-Request-ID`.

A variável `AUNERON_API_KEY` é necessária no container Nginx em runtime,
não durante o build do React.
