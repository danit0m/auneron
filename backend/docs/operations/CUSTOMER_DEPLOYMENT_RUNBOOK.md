# Customer Deployment Runbook

## Escopo

Procedimento operacional para colocar em produção um deployment
dedicado a **um único cliente externo**, sob o modelo congelado em
"External MVP Decision Freeze":

```
1 customer organization
  → 1 isolated application deployment
  → 1 isolated PostgreSQL database
  → customer-specific secrets
  → customer-specific domain
```

Este runbook cobre exclusivamente esse modelo. Não é o procedimento de
desenvolvimento local (ver `docs/DOCKER_COMPOSE.md`) nem um guia
genérico de deploy independente de provedor (ver `docs/DEPLOYMENT.md`,
que continua sendo a referência de arquitetura/segredos/CORS/TLS em
nível de aplicação). Este documento formaliza o contrato específico de
**um deployment por cliente** usando `backend/docker-compose.prod.yml`.

## Pré-condições

- Servidor dedicado a este cliente (não compartilhado com outro
  cliente no mesmo host Docker — os nomes de container/volume/rede do
  compose são fixos, não parametrizados por cliente além do domínio).
- Registro DNS tipo A do domínio do cliente apontando para o IP
  público deste servidor, já propagado.
- Acesso de shell ao servidor para preencher `secrets/*.txt` e rodar
  `docker compose`.

## Identidade do deployment

```
APP_ENV=production
```

Hardcoded no `docker-compose.prod.yml` (serviços `migration` e
`backend`). O backend recusa subir com `DEBUG=true`,
`DATABASE_ECHO=true` ou `MAINTENANCE_ENABLED=false` quando
`APP_ENV=production` — essas três proteções já são impostas pelo
código (`Settings.validate_environment`), não dependem de disciplina
operacional.

```
replicas == 1
```

Restrição **operacional**, não uma trava de infraestrutura. Nenhum
`deploy.replicas` está declarado no compose e o Dockerfile do backend
roda `uvicorn` sem `--workers` — mas nada impede um operador de
executar `docker compose -f docker-compose.prod.yml up --scale
backend=N`. Não escale este deployment além de 1 instância de
`backend`. O rate limiter de login/elevação é por processo e a
segurança de concorrência de 1 dos 10 maintenance loops ainda não está
provada sob múltiplas instâncias (ver G4 em
`PRODUCTION_READINESS_GAP_REGISTER.md`).

## Domínio do cliente

```
CUSTOMER_DOMAIN=<FQDN único do cliente, sem protocolo>
```

Contrato:

- exatamente um FQDN canônico por deployment;
- somente HTTPS (`CORS_ORIGINS` deriva de `https://${CUSTOMER_DOMAIN}`,
  validado pelo backend, que rejeita HTTP e wildcard em produção);
- **sem alias `www` implícito** — o Traefik só responde ao FQDN
  literal configurado;
- aliases adicionais ou redirects (por exemplo `www.` → apex, ou um
  segundo domínio) estão **fora deste contrato**. Se um cliente
  precisar disso, é uma extensão a ser desenhada separadamente, nunca
  uma heurística automática.

`CUSTOMER_DOMAIN` é obrigatório e fail-fast: o compose usa
`${CUSTOMER_DOMAIN:?Defina CUSTOMER_DOMAIN}` tanto no label do Traefik
quanto em `CORS_ORIGINS` de `migration` e `backend` — subir a stack
sem essa variável definida falha imediatamente, sem criar um
deployment mal configurado.

## TLS / ACME

```
ACME_EMAIL=<e-mail real do operador>
```

Obrigatório e fail-fast (`${ACME_EMAIL:?...}` no serviço `traefik`).
Let's Encrypt usa esse e-mail para avisos de expiração de certificado.
O Traefik emite o certificado automaticamente via desafio HTTP para o
`CUSTOMER_DOMAIN` configurado, assim que o DNS estiver propagado.

## Docker secrets obrigatórios

Preencha `secrets/postgres_password.txt`, `secrets/database_url.txt` e
`secrets/api_key.txt` (nunca commitados — `.gitignore` já cobre
`secrets/*`). Consulte os `*.example` correspondentes para o formato.

```
postgres_password.txt  → senha bruta do papel PostgreSQL "auneron"
database_url.txt        → postgresql+psycopg://auneron:<mesma senha>@postgres:5432/auneron
api_key.txt              → chave de serviço, gerada com
                          python -c "import secrets; print(secrets.token_urlsafe(48))"
```

**Consistência obrigatória:** a senha embutida em `database_url.txt`
precisa ser exatamente a mesma senha bruta de `postgres_password.txt`
— são a mesma credencial em dois formatos. Nenhuma checagem cruzada
automática existe entre os dois arquivos; confirme manualmente antes
do primeiro `up`.

## Migrations

O serviço `migration` roda `python -m alembic upgrade head` uma única
vez (`restart: "no"`) e o `backend` só inicia depois que `migration`
termina com sucesso (`depends_on: condition:
service_completed_successfully`). Esse ordenamento é imposto pelo
grafo de dependências do próprio Compose — não depende de o operador
executar os passos na ordem certa manualmente.

## Backup pré-migration

Antes de qualquer deploy que inclua uma nova migration Alembic:

```bash
STAMP=$(date +%Y%m%d_%H%M%S)
docker exec auneron-postgres-prod pg_dump -U auneron -d auneron -F c \
    -f "/tmp/backup_${STAMP}.dump"
docker cp "auneron-postgres-prod:/tmp/backup_${STAMP}.dump" \
    "./backups/backup_${STAMP}.dump"
sha256sum "./backups/backup_${STAMP}.dump" > \
    "./backups/backup_${STAMP}.dump.sha256"
```

Confirme o hash antes de prosseguir. Sem backup validado, não avance a
migration. Este procedimento manual é distinto dos scripts
`backup_postgres.py`/`restore_postgres.py`/`verify_recovery.py`, que
são ferramentas de comprovação do mecanismo pg_dump/pg_restore contra
um banco de drill isolado (`auneron_recovery_drill`) — nunca usadas
para restaurar produção. Ver `DATABASE_BACKUP_VALIDATION.md` para a
evidência do drill.

## Subir a stack

```bash
export CUSTOMER_DOMAIN=cliente.exemplo.com
export ACME_EMAIL=operador@auneron.com.br
export GIT_SHA=$(git rev-parse --short HEAD)

cd backend
docker compose -f docker-compose.prod.yml up -d --build
docker compose -f docker-compose.prod.yml ps
```

Anote o valor impresso por `GIT_SHA` — é a referência de rollback (ver
`ROLLBACK.md`).

## Primeiro administrador

Depois que `backend` estiver saudável:

```bash
docker compose -f docker-compose.prod.yml exec backend \
    python -m scripts.create_user \
    --name "Administrador" \
    --email "email-real-do-operador" \
    --role administrator
```

A senha é solicitada via `getpass` — nunca em variável de ambiente,
argumento de linha de comando ou arquivo. `create_user.py` recusa
`--role developer` quando `APP_ENV=production`.

## Limitação de password recovery — decisão explícita

```
Self-service password recovery:
  OUT OF SCOPE para o primeiro MVP externo.

reset_test_password.py:
  HISTÓRICO / UNTRACKED — nunca fez parte do controle de versão deste
  repositório, não possui guard de ambiente ou role, e NÃO é um
  mecanismo aprovado de produção. Permanece intocado.

Redefinição direta via SQL:
  NÃO é um procedimento aprovado — criaria uma operação privilegiada
  fora das abstrações da aplicação.

Recuperação de senha de usuário existente:
  requer um mecanismo operacional governado próprio, ainda não
  construído, antes de poder ser anunciado ou suportado como
  capacidade de produção.
```

Até essa decisão futura ser tomada, o primeiro cliente externo opera
sem um caminho suportado de "esqueci minha senha" para usuários já
criados — apenas a criação do administrador inicial via
`create_user.py` está coberta por este runbook.

## Verificação de saúde

```
GET /health   → liveness do processo, não depende do PostgreSQL
GET /ready    → readiness, HTTP 503 sem PostgreSQL, HTTP 200 quando pronto
```

## Verificação pós-deploy

- `/login` abre sem sessão via o domínio HTTPS do cliente;
- login real com o administrador criado abre o Dashboard;
- `F5` restaura a sessão;
- Clientes carrega;
- `/api/dashboard/` sem cookie retorna 401;
- API key não aparece em nenhum asset JavaScript;
- headers de segurança do Nginx presentes na resposta pública;
- certificado TLS válido para `CUSTOMER_DOMAIN` (Let's Encrypt).

Ver também `RELEASE_CHECKLIST.md` para a lista completa usada em toda
publicação do Auneron.

## Rollback

Ver `ROLLBACK.md` para o procedimento completo (imagens tagueadas por
`GIT_SHA`, regra de nunca automatizar downgrade destrutivo de schema).
Antes de cada deploy, confirme que `GIT_SHA` foi exportado e que as
últimas imagens tagueadas por commit ainda existem localmente.

## Decommission

Recursos exclusivos deste deployment de cliente, a remover num
decommission completo:

```
containers:  auneron-postgres-prod, auneron-backend-prod,
             auneron-frontend-prod, auneron-traefik
volumes:     auneron_postgres_prod_data, auneron_letsencrypt
secrets:     secrets/postgres_password.txt, secrets/database_url.txt,
             secrets/api_key.txt (arquivos no host, fora do Git)
redes:       auneron_db_prod, auneron_app_prod, auneron_web_prod
DNS:         registro A de CUSTOMER_DOMAIN apontando para este servidor
```

Confirme backup final do banco antes de remover o volume
`auneron_postgres_prod_data` — a remoção do volume é destrutiva e
irreversível.

## Exclusões explícitas deste contrato

```
- shared multi-tenancy (banco compartilhado entre clientes): FORA DE ESCOPO
- replicas > 1 do backend: FORA DE ESCOPO — ver G4
- self-service onboarding: FORA DE ESCOPO
- self-service / suportado password recovery: FORA DE ESCOPO
- alias www ou qualquer domínio adicional além de CUSTOMER_DOMAIN: FORA DE ESCOPO
- account.mark_paid como pagamento externamente observado
  (payment_observed): FORA DE ESCOPO — o corredor governado do
  primeiro MVP externo é account.mark_overdue; mark_paid permanece
  Human-Asserted/Human-Governed (Nível 1)
```
