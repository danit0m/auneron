# Validação de Backup e Restauração

## Histórico (2026-08-05) — superseded

> Historical validation superseded by the Production Recovery Drill
> executed against baseline `b9f83ec89727038fc684f241bf4617488ead25d9`
> (Pilot Action Space PR-4, ver seção abaixo). Mantida aqui só como
> registro de proveniência do mecanismo — não é evidência válida para
> o schema/baseline atual.

- PostgreSQL 17, banco `auneron`, usuário `auneron`.
- Backup via `pg_dump` formato customizado (`.dump`), armazenado
  localmente em `C:\Users\Tomaz\Documents\AuneronBackups`, verificado
  por tamanho/data, checksum SHA-256 e `pg_restore --list`.
- Restaurado em banco isolado `auneron_restore_test`.
- Resultado: `accounts` 24 registros, `knowledge` 88 registros,
  revisão Alembic `558931d55c94` (migration inicial do projeto —
  anterior a praticamente todo o schema atual: Work, Approval, Skill,
  Memory, receivables lifecycle, Outcome, Customer 360, Action Space,
  NBA), 0 referências órfãs, sequences válidas, banco principal não
  alterado.
- Conclusão de então: mecanismo pg_dump/pg_restore validado em
  princípio — mas nunca testado contra o schema atual (26 tabelas,
  head `c4718a49cebf`).

## Production Recovery Drill V1 (PR-4)

Baseline Git: `b9f83ec89727038fc684f241bf4617488ead25d9`
PostgreSQL: `17.10` (postgres:17-alpine — imagem idêntica em dev e produção)
Origem (sanitizada, sem segredo): `host=127.0.0.1 port=5433 database=auneron_test`
Banco de recovery: `auneron_recovery_drill`

### Layer A — Database Recovery Verification

- Tamanho do artefato de backup: `164066 bytes`
- Duração do backup: `1.60 s`
- Duração do restore: `2.82 s`
- Tabelas verificadas (dinâmico, `table_type='BASE TABLE'`): `26`
- Cardinalidades origem == destino: `PASS` (fail-closed — qualquer
  divergência teria interrompido `verify_recovery.py` com
  `RecoveryVerificationFailedError`; execução terminou com sucesso)
- Revisão Alembic origem == destino: `c4718a49cebf` (idêntica nos
  dois lados)
- Integridade referencial: garantida pelo exit code do `pg_restore`
  (sem a flag que desabilita revalidação de constraints durante a
  carga) — nenhum checador de FK próprio foi construído.
- Duração da verificação: `13.00 s`
- Resultado do cleanup (`DROP DATABASE auneron_recovery_drill`, com
  guard reaplicado): sucesso — confirmado por consulta independente
  (`SELECT datname FROM pg_database WHERE datname='auneron_recovery_drill'`)
  retornando zero linhas após o cleanup.
- Banco de origem (`auneron_test`): não alterado — `pg_dump` é
  somente-leitura por natureza; confirmado adicionalmente pela
  suíte completa do backend permanecer verde após o drill.

**Database Recovery Layer A: PASS**

### Layer B — Application Recovery Smoke

**Application Recovery Layer B: BLOCKED — missing maintenance-worker
isolation prerequisite.**

Nenhum mecanismo existe hoje para subir o backend sem que os 10
maintenance loops (`app/main.py:lifespan()`) iniciem automaticamente
— incluindo contra os 1300+ testes desta sessão, que já rodam o
`lifespan()` completo sem nenhuma supressão. Subir um backend
temporário contra `auneron_recovery_drill` sem esse isolamento
transformaria um smoke de recuperação em uma execução operacional
capaz de modificar o snapshot restaurado. Registrado como pré-requisito
ausente no Production Readiness Gap Register (P2 — Isolated
Application Recovery Smoke), com Survey/Freeze próprios — não
implementado nesta fatia.

## Conclusão

O backup do Auneron (schema atual, 26 tabelas) pode ser utilizado
para restaurar integralmente a estrutura, os dados e o histórico do
Alembic (Layer A). A recuperação completa da aplicação (Layer B)
permanece bloqueada por um pré-requisito de isolamento ainda não
construído.
