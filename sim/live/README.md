# sim/live: harness LIVE do laboratório `auneron_sim` (SIM-1.5A)

Versiona o que, no PRE-LIVE do SIM-1.5, rodou como ~30 scripts externos. **Não altera** a semântica do Driver, do Collector, do Evaluator nem do Oracle: orquestra-os sobre os containers do lab.

## O que existe

| Módulo | Papel |
|---|---|
| `env.py` | instância do lab, `compose` **com o override** (guarda estendida antes de qualquer comando), `docker exec`/`cp` sempre com o prefixo `auneron-sim-`, entradas em volumes **nativos** |
| `fingerprint.py` | impressão digital de **conteúdo** (contagem + md5) de todas as tabelas, SQL somente leitura |
| `judges.py` | critérios de PASS/FAIL **puros** (topologia, sonda, recusa, pureza, controle positivo, DR-3…DR-8, teardown, distinção de classes) |
| `scenarios.py` | cenários e Oracles **técnicos** `[SIM-HARNESS]` (nada da Nova Horizonte), incl. o cenário do floor |
| `flow.py` | Hooks do `RunOrchestrator` por `docker exec`, ativação do floor com o override, pipeline completo, O-1 |
| `gates.py` | as fases (`prep`, `provision`, `compose-config`, `build`, `up-ext`, `dr1`…`dr8`, `c0`, `c0b`, `teardown`) |
| `container/*.py` | scripts que rodam **dentro** dos containers, entregues por stdin (não entram nas imagens) |
| `cli.py` | `python -m sim.live.cli <fase> --instance <id>` |

## Sessão típica

```
# sessão 1 (C0b)    stack descartável próprio
prep -> up-base -> provision -> compose-config -> build -> up-ext -> stage-secrets -> c0b -> reset
# sessão 2 (DR)     stack descartável próprio
prep -> up-base -> provision -> compose-config -> up-ext -> stage-base -> dr1 -> c0 -> dr2 -> dr3 -> dr4
      -> dr5 -> dr6 -> dr7 -> dr8 -> teardown
```

`prep` guarda o manifesto aprovado do candidato (lido do **índice**) e o `HEAD` esperado. `teardown` reconcilia contra eles: 0 resíduos Docker, índice e bytes idênticos ao aprovado, `HEAD` inalterado, 23 arquivos do SIM-1.4 intactos, snapshot DEV idêntico e `auneron_test` intacto.

## Regras que o código impõe

- Nada fora do prefixo `auneron-sim-` / `auneron_sim_`. DEV, `auneron_test` e qualquer stack externo continuam proibidos.
- O harness **não corrige** o ambiente: fase com `FAIL` registra a evidência e o operador para.
- Cada instrumento tem **controle positivo**: a sonda de isolamento acusa vazamentos plantados, e a impressão digital acusa a escrita do NBA. Um `PASS` de instrumento cego não vale.
- `pg_stat_user_tables` é proibido como detector de escrita (cego a conexões em pool).
- O limite de O-1 (≤ 1 s) é lido do candidato e **nunca** relaxado aqui: excedê-lo é `THRESHOLD_REVIEW_REQUIRED` e volta ao PO.
- Segredos sintéticos por instância, fora do repositório, nunca impressos.

## Fronteiras de claim

Os cenários técnicos provam que os **instrumentos** funcionam contra o produto real. Não são resultado de cenário: nunca `SIMULATION VERIFIED` nem `OPERATIONALLY OBSERVED`.
