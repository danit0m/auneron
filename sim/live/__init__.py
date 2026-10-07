"""
SIM-1.5A: harness LIVE versionado do laboratorio `auneron_sim` (substitui os ~30 scripts externos usados no
PRE-LIVE). Nao altera a semantica do Driver, do Collector, do Evaluator nem do Oracle: apenas os orquestra
sobre os containers do lab, com os instrumentos JA CORRIGIDOS no PRE-LIVE (impressao digital de conteudo,
juizes com controle positivo, teardown com a classificacao correta de nao rastreados).

Fica FORA de `sim/stack/` de proposito: o gate do SIM-1.4 proibe ali literais de cenario e imports de
`sim.oracle`, e o compilador do plano de coleta vive em `sim.oracle`.
"""
