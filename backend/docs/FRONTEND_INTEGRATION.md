# Integração segura do frontend

## Diagnóstico

Antes deste commit, o cliente Axios utilizava diretamente:

`http://127.0.0.1:8000`

Depois da proteção do backend com `X-API-Key`, enviar a chave por
uma variável `VITE_*` seria inseguro, porque variáveis desse tipo
são incorporadas ao JavaScript entregue ao navegador.

## Arquitetura local

O navegador chama somente URLs relativas:

`/api/...`

O Vite atua como proxy de desenvolvimento:

```text
Browser -> Vite /api -> FastAPI
                    + X-API-Key
```

A chave fica em `AUNERON_API_KEY`, sem prefixo `VITE_`, e é lida
somente pelo processo Node do Vite.

O bundle React não recebe essa variável.

## Variáveis locais

No diretório `frontend`, use `.env.local`:

```text
AUNERON_BACKEND_URL=http://127.0.0.1:8000
AUNERON_API_KEY=<mesma API_KEY do backend local>
VITE_ELEVATED_DEV_CODE=<credencial local de desenvolvimento>
```

`.env.local` nunca deve ser versionado.

## Produção

O build continua utilizando URLs relativas `/api`.

Em produção, o servidor que publica o frontend deve encaminhar
`/api` para o FastAPI. Enquanto a autenticação do backend for
baseada na API key de serviço, a injeção de `X-API-Key` deve ser
feita no lado servidor, por reverse proxy ou BFF.

Nunca publique a API key como `VITE_API_KEY`, JavaScript,
localStorage, sessionStorage ou qualquer outro conteúdo entregue ao
navegador.

A autenticação de usuário definitiva deve substituir essa ponte por
sessão segura ou tokens de curta duração com autorização no
backend.

## Request ID

O cliente Axios envia `X-Request-ID` em cada chamada. O backend
preserva IDs válidos e devolve o mesmo cabeçalho.

Erros HTTP exibidos ao usuário podem incluir esse identificador
como referência para investigação nos logs estruturados.

## Tratamento de falhas

A camada HTTP diferencia:

- ausência de conexão com o backend;
- HTTP 401 por falha na credencial do proxy;
- HTTP 503 quando a autenticação da API está indisponível;
- mensagens `detail` retornadas pelo FastAPI.

## E2E

O script:

```powershell
python .\scripts\e2e_frontend.py
```

inicia um FastAPI de teste na porta 8001 e um Vite de teste na
porta 5174. Em seguida abre Chromium com Playwright e confirma que
Dashboard e Clientes carregam dados através de `/api`.

O script usa `auneron_test`, não grava dados e encerra os dois
processos ao final.

Antes da primeira execução local:

```powershell
python -m playwright install chromium
```
