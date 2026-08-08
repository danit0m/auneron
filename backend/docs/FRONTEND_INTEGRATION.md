# Integração segura do frontend

## Arquitetura

O navegador chama somente URLs relativas:

```text
/api/...
```

Em desenvolvimento:

```text
Browser
  |
  | cookie HttpOnly de usuário
  v
Vite /api
  |
  | + X-API-Key no processo Node
  v
FastAPI
  |
  v
PostgreSQL
```

Em produção, o Nginx/reverse proxy exerce o mesmo papel do Vite.

A `X-API-Key` continua sendo uma credencial de serviço. Ela nunca é
publicada no bundle React e não representa a identidade do usuário.

A identidade individual é mantida por uma sessão de usuário em cookie
`HttpOnly`.

## Variáveis locais

No diretório `frontend`, use `.env.local`:

```text
AUNERON_BACKEND_URL=http://127.0.0.1:8000
AUNERON_API_KEY=<mesma API_KEY do backend local>
```

`.env.local` nunca deve ser versionado.

Não existe credencial `VITE_*` para login ou elevação.

## Login e restauração de sessão

O frontend utiliza:

```text
POST /api/auth/login
GET  /api/auth/me
POST /api/auth/logout
```

O Axios usa `withCredentials: true`.

Após login, o token de sessão fica somente no cookie `HttpOnly`. O
frontend recebe os dados do usuário e as datas da sessão, mas não o token
bruto.

Ao iniciar ou recarregar a aplicação, o `AuthProvider` consulta
`GET /auth/me`. Uma sessão válida restaura o usuário e suas permissões.

Usuários sem sessão são direcionados para `/login`. A rota originalmente
solicitada é preservada para retorno após autenticação.

## RBAC

O frontend utiliza a mesma matriz de papéis e permissões do backend para
controlar navegação e experiência de usuário.

A proteção visual não é a fronteira de segurança. O FastAPI também exige
sessão e permissão em cada router ou endpoint de negócio.

## Acesso elevado

Recursos administrativos sensíveis usam:

```text
POST /api/auth/elevate
POST /api/auth/elevation/revoke
```

A elevação exige novamente a senha da conta. O backend grava
`elevated_until` na sessão atual.

Não existe `VITE_ELEVATED_DEV_CODE`, `localStorage` ou `sessionStorage`
para conceder acesso elevado.

Após `F5`, a elevação ainda válida é restaurada por `GET /auth/me`.

## Request ID

O cliente Axios envia `X-Request-ID` em cada chamada. O backend preserva
IDs válidos e devolve o mesmo cabeçalho.

Erros HTTP exibidos ao usuário podem incluir esse identificador como
referência para investigação nos logs estruturados.

## Tratamento de falhas

A camada HTTP diferencia:

- ausência de conexão com o backend;
- HTTP 401 por ausência/expiração de sessão ou credencial inválida;
- HTTP 403 por permissão insuficiente ou elevação ausente;
- HTTP 503 quando a credencial de serviço não está configurada;
- mensagens `detail` retornadas pelo FastAPI.

## E2E

O script:

```powershell
python .\scripts\e2e_frontend.py
```

inicia FastAPI na porta 8001 e Vite na porta 5174 usando `auneron_test`.

O E2E cria um usuário `developer` descartável, realiza login real pela
interface, valida Dashboard, recarrega a página para provar a restauração
da sessão e valida Clientes. O usuário e suas sessões são removidos no
final.

Antes da primeira execução local:

```powershell
python -m playwright install chromium
```
