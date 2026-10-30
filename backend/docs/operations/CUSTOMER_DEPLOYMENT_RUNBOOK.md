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
EXPECTED_DATABASE_NAME=<nome do banco deste deployment>
EXPECTED_DATABASE_HOST=<host do PostgreSQL deste deployment>
```

Identidade positiva mínima de produção (G3). Obrigatórias e
fail-fast em `production` (`${EXPECTED_DATABASE_NAME:?...}`/
`${EXPECTED_DATABASE_HOST:?...}` no compose) — o backend recusa subir
se `DATABASE_URL` apontar para um nome ou host diferente do declarado
aqui, ou se qualquer uma das duas variáveis estiver ausente. Essa
comparação é puramente sintática (parsing de `DATABASE_URL`, sem
conexão de rede) e roda antes de qualquer tentativa de conexão real
com o PostgreSQL.

Para a topologia atual (`postgres` como serviço interno do Compose):

```
EXPECTED_DATABASE_NAME=auneron
EXPECTED_DATABASE_HOST=postgres
```

Se este cliente usar um PostgreSQL externo (gerenciado, VM dedicada
etc.), `EXPECTED_DATABASE_HOST` deve ser o hostname real desse
serviço — nenhum valor é fixado no código, o contrato funciona igual
nos dois casos.

**Limite importante:** esta é uma identidade **declarada pelo
operador**, não uma prova criptográfica ou física de que o processo
está de fato conectado ao servidor PostgreSQL correto. G3 detecta
`DATABASE_URL` apontando para um nome/host diferente do declarado —
não detecta `DATABASE_URL` e `EXPECTED_DATABASE_*` configurados
incorretamente **juntos** (por exemplo, ambos apontando para o
deployment errado por engano do operador). `EXPECTED_DATABASE_PORT`
não é validado nesta fase — pode ser adicionado depois sem alterar
este modelo.

```
replicas == 1
```

Restrição **operacional**, não uma trava de infraestrutura. Nenhum
`deploy.replicas` está declarado no compose e o Dockerfile do backend
roda `uvicorn` sem `--workers`. O `container_name` fixo do serviço
`backend` (`auneron-backend-prod`) tende a fazer o Compose recusar
`--scale backend=N` com N>1 (semântica do Compose, não exercitada
neste repositório) — mas isso é um efeito colateral, não um controle
desenhado: outro arquivo de compose, um override ou `docker run`
manual contornam essa barreira. Não escale este deployment além de 1
instância de `backend`. O rate limiter de login/elevação é por
processo e a segurança de concorrência de 7 dos 10 maintenance loops
ainda não está provada sob múltiplas instâncias (ver "G4 —
Multi-instance Maintenance Concurrency Verification" em
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
evidência dos drills: V1/V2 no schema Alembic `c4718a49cebf` (26
tabelas) e, em 2026-10-30, um drill efêmero e sintético em CI no head
`b428d1a6c903` (37 tabelas, Layer A + Layer B PASS) — ver a seção
"Current-Schema Recovery Drill (AREA1, CI efêmero)" daquele documento
e seus limites (não é restore nem deploy de produção).

## Subir a stack

```bash
export CUSTOMER_DOMAIN=cliente.exemplo.com
export ACME_EMAIL=operador@auneron.com.br
export EXPECTED_DATABASE_NAME=auneron
export EXPECTED_DATABASE_HOST=postgres

cd backend
# Identidade de build: SHA completo (40 hex) + GIT_DIRTY do checkout
# real, pela mesma ferramenta usada no CI (somente stdlib + git; não
# importa o pacote `app`). Saída: linhas `GIT_SHA=<40 hex>` e
# `GIT_DIRTY=<true|false>`; exit 0 = OK, 3 = git indisponível.
CLAIMS="$(python3 scripts/verify_build_identity.py claims)" || exit 1
export GIT_SHA="$(printf '%s\n' "$CLAIMS" | sed -n 's/^GIT_SHA=//p')"
export GIT_DIRTY="$(printf '%s\n' "$CLAIMS" | sed -n 's/^GIT_DIRTY=//p')"
echo "GIT_SHA=${GIT_SHA} GIT_DIRTY=${GIT_DIRTY}"

docker compose -f docker-compose.prod.yml up -d --build
docker compose -f docker-compose.prod.yml ps
```

Não use `git rev-parse --short HEAD`: `app/core/build_identity.py`
exige SHA de 40 hex, e `GIT_DIRTY` ausente cai no default `unknown` do
compose — o backend sobe mesmo assim (a identidade de build é
diagnóstica, logada no startup, não bloqueia o boot), mas o deployment
fica sem identidade válida. Para um deploy de cliente, espere
`GIT_DIRTY=false`; `true` significa que a imagem foi construída de um
checkout com alterações não commitadas e não corresponde a nenhum
commit.

Anote os valores impressos de `GIT_SHA` e `GIT_DIRTY` — `GIT_SHA` é a
referência de rollback (ver `ROLLBACK.md`).

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
Antes de cada deploy, confirme que `GIT_SHA` (SHA completo, 40 hex) e
`GIT_DIRTY` foram exportados via `scripts/verify_build_identity.py
claims` (ver "Subir a stack") e que as últimas imagens tagueadas por
commit ainda existem localmente.

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
- replicas > 1 do backend: FORA DE ESCOPO — ver G4 (Multi-instance
  Maintenance Concurrency Verification)
- self-service onboarding: FORA DE ESCOPO
- self-service / suportado password recovery: FORA DE ESCOPO
- alias www ou qualquer domínio adicional além de CUSTOMER_DOMAIN: FORA DE ESCOPO
- account.mark_paid como pagamento externamente observado
  (payment_observed): FORA DE ESCOPO — o corredor governado do
  primeiro MVP externo é account.mark_overdue; mark_paid permanece
  Human-Asserted/Human-Governed (Nível 1)
```
