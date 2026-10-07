"""
Impressao digital de CONTEUDO por tabela (contagem + md5 das linhas), instrumentacao SOMENTE LEITURA do lab.

`pg_stat_user_tables` foi DESCARTADO no PRE-LIVE: nao enxerga escritas de conexoes em pool (medido: o GET do NBA
fez `count(*)` 4 -> 5 e o pg_stat ficou em 4). Qualquer "delta zero" por ele seria vazio.
Nunca evidencia de negocio; nunca usado pelo Driver, pelo Collector ou pelo Evaluator.
"""

from __future__ import annotations

from sim.stack.lab import sqlro

FINGERPRINT_SQL = (
    "select table_name, "
    "(xpath('/row/c/text()', query_to_xml(format('select count(*) as c from %I', table_name), false, true, '')))[1]::text, "
    "(xpath('/row/m/text()', query_to_xml(format('select md5(coalesce(string_agg(x::text, chr(126) order by x::text), "
    "chr(32))) as m from %I x', table_name), false, true, '')))[1]::text "
    "from information_schema.tables where table_schema = 'public' and table_type = 'BASE TABLE' order by 1"
)


def fingerprint(runner, config) -> dict:
    """{tabela: [contagem, md5]} de todas as tabelas de `public`."""
    return {row[0]: [int(row[1]), row[2]] for row in sqlro.query(runner, config, FINGERPRINT_SQL)}


def delta(before: dict, after: dict) -> dict:
    """Tabelas cujo CONTEUDO mudou (contagem ou md5)."""
    out = {}
    for table in sorted(set(before) | set(after)):
        if before.get(table) != after.get(table):
            out[table] = {"count": [before.get(table, [None])[0], after.get(table, [None])[0]], "content_changed": True}
    return out
