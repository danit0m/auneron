# Validação de Backup e Restauração

Data: 2026-08-05

## Banco principal

- PostgreSQL 17
- Banco: `auneron`
- Usuário da aplicação: `auneron`

## Backup

O backup foi criado com `pg_dump` no formato customizado `.dump`.

Local de armazenamento:

`C:\Users\Tomaz\Documents\AuneronBackups`

O arquivo foi verificado por:

- tamanho e data de criação;
- checksum SHA-256;
- leitura do catálogo com `pg_restore --list`.

## Teste de restauração

O backup foi restaurado em um banco isolado:

`auneron_restore_test`

## Resultado

- `accounts`: 24 registros
- `knowledge`: 88 registros
- revisão Alembic: `558931d55c94`
- referências órfãs: 0
- sequências de IDs: válidas
- banco principal: não alterado

Após a validação, o banco temporário de restauração foi removido.

## Conclusão

O backup do Auneron pode ser utilizado para restaurar integralmente
a estrutura, os dados, os índices, as sequências e o histórico do Alembic.
