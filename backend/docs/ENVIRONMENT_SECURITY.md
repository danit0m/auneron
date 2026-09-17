# Configuração segura de ambientes

O Auneron não mantém senhas de banco no código-fonte.

## Desenvolvimento local

1. Copie `backend/.env.example` para `backend/.env`.
2. Substitua `CHANGE_ME` pela senha local do papel PostgreSQL.
3. Mantenha `APP_ENV=development`.
4. Confirme que `DATABASE_URL` aponta para o banco `auneron`.

O arquivo `.env` é ignorado pelo Git.

## Testes locais

1. Copie `backend/.env.test.example` para `backend/.env.test`.
2. Substitua `CHANGE_ME` pela senha local do papel PostgreSQL.
3. Confirme que `TEST_DATABASE_URL` aponta para `auneron_test`.
4. Execute `powershell -ExecutionPolicy Bypass -File .\scripts\test.ps1`.

O script define `APP_ENV=test` e usa a URL de teste somente
durante a execução. Ao terminar, restaura as variáveis anteriores.

O arquivo `.env.test` é ignorado pelo Git.

## Proteções

A configuração rejeita:

- `APP_ENV=test` com banco diferente de `auneron_test`;
- banco `auneron_test` com ambiente diferente de `test`;
- `DATABASE_URL` ausente ou sem nome de banco.

Os testes também validam o nome do banco antes de executar
`TRUNCATE TABLE`.

## Papel `developer` em produção

O Auneron define 7 papéis RBAC (`viewer`, `analyst`, `manager`, `executive`,
`administrator`, `developer`, `system`). O papel `developer` permanece válido
e sem restrições adicionais em `development`/`test`. Em `APP_ENV=production`,
ele não constitui uma identidade interativa utilizável — é uma propriedade
operacional vigente, aplicada em três pontos independentes:

- `authenticate_user` recusa autenticar um usuário `developer` quando o
  ambiente é `production`;
- `create_session` recusa criar sessão para esse caso;
- `require_user_session` revalida o papel a cada requisição autenticada e
  recusa (HTTP 401) mesmo uma sessão `developer` pré-existente, criada antes
  de o ambiente se tornar `production`.

Adicionalmente, `scripts/create_user.py` recusa `--role developer` quando
`APP_ENV=production`, antes mesmo de solicitar senha. No startup, uma
verificação somente leitura (`check_production_developer_roles`) registra a
contagem de contas `developer` pré-existentes em produção, sem nunca
mutá-las, excluí-las ou bloquear o boot.

Nenhuma quarta garantia além dessas quatro existe hoje (as três de
enforcement de sessão/login + a de criação de usuário), e a observação de
startup é estritamente informativa.

## GitHub Actions

O PostgreSQL do workflow é um contêiner descartável criado para
cada execução. A senha definida no workflow pertence somente a
esse serviço temporário e não deve ser reutilizada em nenhum
ambiente real.

Credenciais de homologação e produção devem ser armazenadas no
gerenciador de segredos da plataforma de implantação.
