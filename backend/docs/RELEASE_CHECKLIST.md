# Checklist de release

Use este checklist antes de publicar uma versão do Auneron.

## 1. Git

```powershell
git status
git log -5 --oneline
```

O código destinado ao release deve estar revisado e o working tree deve
estar limpo no momento da publicação.

## 2. Segredos

Confirme:

- nenhum `.env` está versionado;
- nenhum `.env.local` está versionado;
- `API_KEY` real não aparece no diff;
- `DATABASE_URL` real não aparece no diff;
- segredos de produção estão no gerenciador da plataforma.

## 3. Dependências

Backend:

```powershell
python -m pip check
```

Frontend:

```powershell
Push-Location ..\frontend
npm ci
Pop-Location
```

## 4. Frontend

```powershell
Push-Location ..\frontend
npm run lint
npm run build
Pop-Location
```

## 5. Backend

Na pasta `backend`:

```powershell
python -m compileall -q app tests scripts
powershell -ExecutionPolicy Bypass -File .\scripts\test.ps1
```

## 6. E2E

```powershell
python .\scripts\e2e_frontend.py
```

Valide:

```text
E2E frontend: OK
Dashboard via /api: HTTP 200
Clientes via /api: HTTP 200
```

## 7. PostgreSQL

```powershell
python .\scripts\db_diagnostics.py
```

Confirme banco, application name, timeouts e pool.

## 8. Alembic

Antes do deploy:

```powershell
python -m alembic heads
python -m alembic check
```

No ambiente de destino, faça backup antes de qualquer migração que altere
dados ou schema.

Depois:

```text
python -m alembic upgrade head
python -m alembic current
```

## 9. Containers

Na raiz do repositório:

```powershell
docker build -f backend/Dockerfile -t auneron-backend:release .
docker build -f frontend/Dockerfile -t auneron-frontend:release .
```

Nenhuma API key real deve ser usada como `--build-arg` no frontend.

## 10. CI

O GitHub Actions do commit de release deve estar verde.

O workflow atual valida:

- PostgreSQL 17;
- Python 3.11;
- Node 22;
- lint;
- build;
- Alembic;
- pytest;
- diagnóstico do banco;
- Playwright E2E;
- limpeza do banco de testes.

## 11. Produção

Confirme:

- `APP_ENV=production`;
- PostgreSQL não está exposto publicamente;
- HTTPS está ativo;
- `API_KEY` possui pelo menos 32 caracteres;
- `DATABASE_APPLICATION_NAME` identifica o ambiente;
- health check aponta para `/health`;
- frontend encaminha `/api` ao backend;
- reverse proxy injeta `X-API-Key` no lado servidor.

## 12. Pós-deploy

Valide:

```text
GET /health
```

Depois:

- carregue Dashboard;
- carregue Clientes;
- confirme que as chamadas `/api` retornam sucesso;
- confirme HTTP 401 para acesso direto protegido sem credencial;
- confirme request IDs nos logs;
- confirme que a API key não existe nos assets JavaScript;
- acompanhe erros e latência após a publicação.

## 13. Rollback

Antes do release, defina:

- versão anterior da imagem backend;
- versão anterior da imagem frontend;
- backup do banco compatível;
- procedimento de rollback de schema quando aplicável.

Evite downgrade destrutivo automático de banco. Mudanças irreversíveis
devem possuir plano explícito de recuperação.
