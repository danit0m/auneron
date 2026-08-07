# Segurança da API

## Escopo

Os endpoints públicos são:

- `GET /`
- `GET /health`
- documentação OpenAPI em ambientes onde ela estiver habilitada.

Os endpoints dos routers de negócio exigem a chave da API:

- `/accounts`
- `/brain`
- `/brain/executive`
- `/dashboard`
- `/orchestrator`
- `/upload`

## Cabeçalho

A credencial é enviada em:

`X-API-Key`

O valor nunca deve ser versionado no Git.

## Configuração local

Adicione ao arquivo `backend/.env`:

`API_KEY=<chave aleatória com pelo menos 32 caracteres>`

Para testes, adicione ao arquivo `backend/.env.test`:

`TEST_API_KEY=<chave aleatória de teste com pelo menos 32 caracteres>`

O script `scripts/test.ps1` converte temporariamente
`TEST_API_KEY` em `API_KEY` durante a suíte e restaura o ambiente
ao terminar.

## Comportamento

- chave correta: acesso permitido;
- chave ausente ou incorreta: HTTP 401;
- autenticação não configurada: HTTP 503;
- em `production`, `API_KEY` é obrigatória;
- qualquer chave configurada precisa ter ao menos 32 caracteres.

A comparação da credencial usa `secrets.compare_digest`.

## CI

A chave presente no GitHub Actions é exclusivamente uma
credencial fictícia do ambiente descartável de testes. Ela não
deve ser reutilizada em desenvolvimento, homologação ou produção.

## Limite deste mecanismo

A API key protege o acesso ao backend, mas não representa
autenticação individual de usuários. Uma SPA entregue ao navegador
não consegue manter uma API key como segredo.

Antes de uma exposição pública multiusuário, o Auneron deve adotar
autenticação de usuários com sessões seguras ou tokens de curta
duração, autorização por perfil e HTTPS obrigatório.
