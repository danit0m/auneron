# Rollback do Auneron (Producao)

## Objetivo

Documentar o procedimento real de rollback da stack de producao
(`backend/docker-compose.prod.yml`), cobrindo imagem e banco. Nao existe
automacao de downgrade destrutivo — este documento e um roteiro manual.

## Como as imagens sao versionadas

Cada build gera duas tags para a mesma imagem:

- `auneron-backend:prod` / `auneron-frontend:prod` — a tag que os
  servicos realmente usam para rodar (`image:` no compose);
- `auneron-backend:<hash-do-commit>` / `auneron-frontend:<hash-do-commit>`
  — uma tag extra, fixada no commit git que gerou o build, que serve
  como alvo de rollback.

O hash vem da variavel de ambiente `GIT_SHA`, lida pelo
`docker-compose.prod.yml` como `${GIT_SHA:-local}`. Se `GIT_SHA` nao for
exportada antes do build, a tag extra vira `:local` e perde valor como
alvo de rollback — por isso o passo abaixo e obrigatorio a cada deploy.

## Deploy com versionamento correto

Na pasta `/opt/auneron` do servidor:

```bash
git pull
export GIT_SHA=$(git rev-parse --short HEAD)
echo "Deploy do commit: $GIT_SHA"   # anote esse hash antes de seguir

cd backend
docker compose -f docker-compose.prod.yml build
docker compose -f docker-compose.prod.yml up -d
docker compose -f docker-compose.prod.yml ps
```

Guarde o valor impresso em `Deploy do commit: ...` (num bloco de notas,
por exemplo). Ele e a referencia usada no rollback caso o deploy
apresente problema.

## Backup do banco antes de toda migration

Antes de rodar um deploy que inclua uma nova migration Alembic, gere um
backup do banco (mesmo padrao usado na Fatia 1 — Adaptive Memory):

```bash
STAMP=$(date +%Y%m%d_%H%M%S)
docker exec auneron-postgres-prod pg_dump -U auneron -d auneron -F c \
    -f "/tmp/backup_${STAMP}.dump"
docker cp "auneron-postgres-prod:/tmp/backup_${STAMP}.dump" \
    "./backups/backup_${STAMP}.dump"
sha256sum "./backups/backup_${STAMP}.dump" > \
    "./backups/backup_${STAMP}.dump.sha256"
```

Confirme o hash antes de continuar o deploy. Sem backup validado, nao
prossiga com a migration.

## Rollback de imagem (backend/frontend)

Use o hash de commit anotado no ultimo deploy bom conhecido:

```bash
cd /opt/auneron/backend
export OLD_SHA="<hash-do-commit-anterior-bom>"

docker tag "auneron-backend:${OLD_SHA}" auneron-backend:prod
docker tag "auneron-frontend:${OLD_SHA}" auneron-frontend:prod

docker compose -f docker-compose.prod.yml up -d --no-build \
    backend frontend traefik

docker compose -f docker-compose.prod.yml ps
```

Isso so funciona se a imagem antiga ainda existir localmente (ver secao
"Cuidado com limpeza de imagens" abaixo). Este passo nao reexecuta o
servico `migration` — schema fica como esta, a nao ser que a secao
seguinte seja aplicada.

## Rollback de schema (somente se necessario)

Regra permanente: nunca automatizar downgrade destrutivo de banco.

- Se a migration aplicada no deploy problematico so adicionou
  estrutura nova (tabela ou coluna aceitando nulo, por exemplo), sem
  alterar ou remover dados existentes, `alembic downgrade -1` pode ser
  avaliado pontualmente — revise a funcao `downgrade()` daquela
  revisao especifica antes de rodar, para confirmar que ela e mesmo
  segura.
- Se houver qualquer alteracao ou remocao de dados na migration, ou
  qualquer duvida sobre o que a `downgrade()` faz, restaure o backup
  (`pg_restore`) em vez de rodar downgrade automatico.

## Cuidado com limpeza de imagens

Nunca execute `docker image prune` (ou `-a`) sem antes confirmar que as
ultimas 3 a 5 imagens tagueadas por commit ainda existem:

```bash
docker images | grep -E "auneron-backend|auneron-frontend"
```

Se o alvo do rollback for removido por engano, a unica saida e
reconstruir a imagem antiga a partir do commit git correspondente
(`git checkout <hash-do-commit-anterior-bom>` seguido de um novo
build), o que funciona mas demora mais que um `docker tag` direto.
