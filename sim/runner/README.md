# sim/runner: plano de controle do run (SIM-1.5E)

Governa **um** run do `nh-small` (D0..D179) sem reimplementar nada do que ja foi provado: o floor (D14) e o restart (D90)
continuam sendo `RunOrchestrator` + `flow.build_hooks` (SIM-1.5A/1.5C). O runner chama `orchestrator.run(first_day=d,
last_day=d)` **um dia por vez**, decora os hooks e fecha cada dia com o registro diario e o ledger.

| Modulo | Papel |
|---|---|
| `rim.py` | *Run Identity Manifest* (esquema fechado, sem floats/horarios/caminhos) e `run_id = RUN-<rim12>-A<nn>` |
| `registry.py` | registro do host: aloca `A<nn>` (monotonico, nunca reutilizado), um run por vez, colisao de `rim12` = STOP |
| `lifecycle.py` | maquina de estados por *replay*: `CREATED -> PREFLIGHTED -> RUNNING -> COMPLETED/ABORTED/INVALID`; terminais absorventes |
| `ledger.py` | JSONL append-only com hash encadeado + ancora (alteracao, reordenacao, truncamento) |
| `interlock.py` | token `D0_AUTHORIZATION.json` (unico caminho para `RUNNING`). **Nao ha codigo que emita token para `kind = RUN`** |
| `taxonomy.py` | classe de falha -> estado terminal (desconhecida = erro) |
| `daily.py` | registro diario encadeado e julgamento (relogio, barreira, O-1, balanco de massa, topologia, identidade, disco, autoridade) |
| `disk.py` | guarda de disco fail-closed (preflight, aviso, abort entre dias) |
| `authority.py` | Tier 1 (livro-razao factual do Driver) e Tier 2 (tripwire de invariantes PUBLICOS); **sem Oracle** |
| `offline.py` | adjudicacao offline de status HTTP |
| `preflight.py` | P1..P13 como julgamento puro |
| `package.py` / `claim.py` | pacote selavel (`evidence_digest.txt`) e claim gerada do estado do run |
| `runner.py` | `NhRunner`: laco de 180 dias, R0, abort com preservacao |
| `live.py` / `lab_ports.py` / `nh_phases.py` / `harness_side.py` / `declare.py` | ligacao ao lab (portas finas) e lado do harness no host. **Somente `harness_side` e `nh_phases` leem o Oracle** |
| `host.py` | trava do runner, maquina acordada, parada do operador |
| `cli.py` | `python -m sim.runner.cli ...` |

## Regras que o codigo impoe

* **R0**: nenhum resume. Run `RUNNING` encontrado na partida e selado como `ABORTED(UNCLEAN_EXIT)`; retry = novo `run_id`.
* **INTERLOCK-D0**: sem token valido do PO o run nao sai de `PREFLIGHTED`.
* **Oracle**: o runner nunca recebe expectativa, verdade ou ID de caso. O tripwire so conhece invariantes publicos.
* **Fail-closed**: fato ausente, classe desconhecida, medicao de disco falha ou cadeia quebrada reprovam; nada e "consertado" no meio do run.
* **Claim**: gerada do estado do ledger; nunca emite a expressao de verificacao.

## O que NAO foi exercitado ao vivo (primeira execucao real = SR-1)

Os adaptadores `lab_ports.py`/`nh_phases.py` so foram conferidos com duplos (comandos e parsing). O modelo de balanco de massa
(`accounts`, `approvals`, `work_items` do produto x sucessos do Driver) vem do produto falso e do Discovery; o SR-1 o confirma.
