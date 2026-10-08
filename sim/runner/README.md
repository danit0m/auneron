# sim/runner: plano de controle do run (SIM-1.5E / SIM-1.5F)

Governa **um** run (NH-small D0..D179 ou o ensaio SR-1 de 21 dias) sem reimplementar nada do que ja foi provado: o floor
(D14) e o restart (D90/D17) continuam sendo `RunOrchestrator` + `flow.build_hooks` (SIM-1.5A/1.5C). O runner chama
`orchestrator.run(first_day=d, last_day=d)` **um dia por vez**, decora os hooks e fecha cada dia com o registro diario e o
ledger.

| Modulo | Papel |
|---|---|
| `rim.py` | *Run Identity Manifest* (esquema fechado, sem floats/horarios/caminhos) e `run_id = RUN-<rim12>-A<nn>`; `params.balance_mode` (`report` so em `REHEARSAL`) |
| `registry.py` | registro do host: aloca `A<nn>` (monotonico, nunca reutilizado), um run por vez, colisao de `rim12` = STOP |
| `lifecycle.py` | maquina de estados por *replay*: `CREATED -> PREFLIGHTED -> RUNNING -> COMPLETED/ABORTED/INVALID`; terminais absorventes |
| `ledger.py` | JSONL append-only com hash encadeado + ancora (alteracao, reordenacao, truncamento) |
| `interlock.py` | token `D0_AUTHORIZATION.json` (unico caminho para `RUNNING`). **Nao ha codigo que emita token para `kind = RUN`** |
| `taxonomy.py` | classe de falha -> estado terminal (desconhecida = erro) |
| `daily.py` | registro diario encadeado e julgamento (relogio, barreira, O-1, balanco de massa `enforce`/`report`, topologia, identidade, **crescimento**, disco e **projecao de disco**, autoridade) |
| `disk.py` | guarda de disco fail-closed (preflight, aviso, abort entre dias, projecao abaixo da reserva operacional) |
| `growth.py` | modelo de crescimento: ESTIMATIVA com amostras, unidade, periodo, premissas, margem e limitacoes; dado ausente/invalido reprova |
| `authority.py` / `offline.py` | Tier 1/2 (invariantes PUBLICOS, sem Oracle) e adjudicacao offline de status |
| `preflight.py` | P1..P14 como julgamento puro (P14 = contaminacao pela fixture) |
| `package.py` / `claim.py` | pacote selavel (`evidence_digest.txt`) e claim gerada do estado do run |
| `results.py` | digest FINAL dos resultados (`evaluation.json`, `surprises.json`, `claim.json`) vinculado ao ledger e ao digest do pacote |
| `bundle.py` / `declare.py` | bundle de cenario (NH ou fixture SR-1) e parte declarada do RIM |
| `runner.py` | `NhRunner`: laco do run, R0, abort com preservacao, parada controlada |
| `sched.py` | adaptador do Windows Task Scheduler (SIM-1.5G): XML deterministico, validacao semantica, auditoria encadeada, reconciliacao, fatos do P15 |
| `detach.py` / `detach_probe.py` | lancador padrao `launch_detached` (Task Scheduler), monitor, `stop`/`kill`/`tail`; WMI (`launch`) so como mecanismo REJEITADO (DETACH-1 FAIL); sonda DETACH-2 (fatos incrementais, juiz de 12 criterios) |
| `live.py` / `lab_ports.py` / `nh_phases.py` / `harness_side.py` | ligacao ao lab (portas finas) e lado do harness no host. **Somente `harness_side` e `nh_phases` leem o Oracle** |
| `host.py` | trava do runner, maquina acordada, `StopFlag` (sinal ou arquivo `STOP`), `HeartbeatWriter` |
| `cli.py` | `python -m sim.runner.cli ...` |

## Regras que o codigo impoe

* **R0**: nenhum resume. Run `RUNNING` encontrado na partida e selado como `ABORTED(UNCLEAN_EXIT)`; retry = novo `run_id`.
* **INTERLOCK-D0**: sem token valido do PO o run nao sai de `PREFLIGHTED`.
* **Oracle**: o runner nunca recebe expectativa, verdade ou ID de caso. O tripwire so conhece invariantes publicos.
* **Balanco de massa**: `enforce` e obrigatorio no `RUN`; `report` (so `REHEARSAL`) nunca aceita o desvio em silencio (registro diario, ledger, `surprises`, claim).
* **Resultados**: o selo do pacote nao muda; qualquer alteracao de `evaluation.json`/`surprises.json`/`claim.json` e detectada por `verify-results`.
* **Disco**: projecao abaixo da reserva operacional => parada controlada com preservacao; o `RUN` so abre com modelo de crescimento validado e espaco suficiente.
* **Fixture x NH**: pacote de `RUN` com qualquer marcador da fixture e `INVALID(FIXTURE_CONTAMINATION)`.
* **Claim**: gerada do estado do ledger; nunca emite a expressao de verificacao.

## O que NAO foi exercitado ao vivo (primeira execucao real = SR-1)

Os adaptadores `lab_ports.py`/`nh_phases.py`/`detach.py` so foram conferidos com duplos (comandos e parsing); **nenhum processo
destacado foi criado**. O modelo de balanco de massa vem do produto falso e do Discovery; o SR-1 o confirma.


## Task Scheduler (SIM-1.5G, DETACH-2 - candidato, NAO provado)

* Padrao: `launch` registra `\AuneronSim\<run_id>` (InteractiveToken, LeastPrivilege, usuario atual, sem gatilhos, sem reinicio,
  `IgnoreNew`, limite explicito), valida o XML exportado, inicia e espera o heartbeat. Sem senha/S4U/elevacao; privilegio
  diferente do medido => falha fechada. WMI so com `--mechanism wmi --allow-rejected-wmi` (reprovado no DETACH-1).
* `sched-list`, `sched-audit-verify`, `sched-reconcile [--apply [--confirm]]` e `sched-probe --phase launch|monitor|finish
  --level L1|L2` (sonda DETACH-2: exige autorizacao do PO para criar a tarefa temporaria).
* Trilha `sched_audit.jsonl` (hash encadeado, ancoras em dois lugares) em `<run_id>.proc/`, fora do pacote: R0, RIM, ledger e
  selos nao mudam. Evidencias sao preservadas ANTES de remover; remocao nao verificada bloqueia novos ensaios (P15).
* Logoff e reinicio continuam encerrando o processo e o Docker Desktop: condicao operacional proibida; vale o R0 (sem retomada).
* Limites de execucao: sondas L1 `PT30M`, L2 `PT2H`; SR-1 `PT7H`; NH `PT14H` (reservado ao futuro RUN).
* Nada disto foi executado de verdade: os scripts PowerShell foram so verificados sintaticamente e os testes usam duplos.
