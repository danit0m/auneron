# Checklist de release

Use este checklist antes de publicar uma versão do Auneron.

## 1. Git

```powershell
git status
git log -5 --oneline
```

O working tree deve estar limpo no momento da publicação.

## 2. Segredos

Confirme:

- nenhum `.env` ou `.env.local` está versionado;
- nenhuma `API_KEY` real aparece no diff;
- nenhuma `DATABASE_URL` real aparece no diff;
- nenhuma senha de usuário aparece no diff;
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

Confirme que não existem referências a:

```text
VITE_ELEVATED_DEV_CODE
ELEVATION_SESSION_KEY
```

## 5. Backend

```powershell
python -m compileall -q app tests scripts migrations
powershell -ExecutionPolicy Bypass -File .\scripts\test.ps1
```

A suíte deve validar autenticação e RBAC, incluindo API key sem sessão,
permissão insuficiente e elevação.

## 6. E2E

```powershell
python .\scripts\e2e_frontend.py
```

Valide:

```text
E2E frontend: OK
Login real via /api/auth/login: HTTP 200
Dashboard autenticado via /api: HTTP 200
Sessão restaurada após reload: OK
Clientes autenticado via /api: HTTP 200
```

## 7. PostgreSQL

```powershell
python .\scripts\db_diagnostics.py
```

Confirme banco, application name, timeouts e pool.

## 8. Alembic

```powershell
python -m alembic heads
python -m alembic check
```

No destino:

```text
python -m alembic upgrade head
python -m alembic current
```

Faça backup antes de migrações em banco real.

## 9. Containers

```powershell
docker build -f backend/Dockerfile -t auneron-backend:release .
docker build -f frontend/Dockerfile -t auneron-frontend:release .
```

Nenhuma API key real deve ser `--build-arg` do frontend.

Valide também:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\compose_smoke.ps1
```

O smoke deve confirmar:

```text
Backend sem API key: HTTP 401
Frontend /api/health: HTTP 200
Frontend /api/dashboard/ sem sessao: HTTP 401
Frontend /api/auth/me sem sessao: HTTP 401
COMPOSE SMOKE TEST: OK
```

## 10. CI

O GitHub Actions do commit de release deve estar verde.

O workflow valida:

- PostgreSQL 17;
- Python 3.11;
- Node 22;
- lint e build;
- Alembic;
- pytest/RBAC;
- diagnóstico do banco;
- Playwright E2E com login real;
- limpeza de `accounts`, `knowledge`, `users` e `auth_sessions`.

## 11. Produção

Confirme:

- `APP_ENV=production`;
- PostgreSQL não está exposto publicamente;
- HTTPS está ativo;
- `API_KEY` possui pelo menos 32 caracteres;
- reverse proxy injeta `X-API-Key`;
- cookie de sessão é `HttpOnly`, `SameSite=Strict` e `Secure`;
- existe pelo menos um operador válido;
- TTL de sessão e elevação estão definidos;
- `DATABASE_APPLICATION_NAME` identifica o ambiente;
- health check aponta para `/health`.

## 12. Pós-deploy

Valide:

- `/login` abre sem sessão;
- login válido abre o Dashboard;
- `F5` restaura a sessão;
- Clientes carrega;
- logout invalida a sessão;
- `/api/dashboard/` sem cookie retorna 401;
- usuário sem permissão recebe 403;
- recurso administrativo exige elevação;
- `X-Request-ID` aparece nos logs;
- API key não existe nos assets JavaScript.

## 13. Rollback

Antes do release, defina:

- imagem backend anterior;
- imagem frontend anterior;
- backup do banco compatível;
- procedimento de rollback de schema quando aplicável.

Evite downgrade destrutivo automático de banco.
