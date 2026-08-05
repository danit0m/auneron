# Migração SQLite para PostgreSQL

Data: 2026-08-05

## Origem

- Banco: `backend/auneron.db`
- Tabela `accounts`: 24 registros
- Tabela `knowledge`: 88 registros
- Total: 112 registros

## Destino

- PostgreSQL 17
- Banco: `auneron`
- Usuário da aplicação: `auneron`

## Tratamento de inconsistências

Foram identificados 8 registros da tabela `knowledge` associados ao
`account_id = 16`.

A conta 16 não existia mais no SQLite. Para preservar os registros sem
inventar dados da conta excluída, essas referências foram migradas como
`account_id = NULL`.

Registros afetados:

- knowledge 14
- knowledge 15
- knowledge 16
- knowledge 17
- knowledge 18
- knowledge 19
- knowledge 20
- knowledge 21

## Resultado

- Accounts migradas: 24
- Knowledge migradas: 88
- Referências órfãs restantes: 0
- SQLite original preservado
