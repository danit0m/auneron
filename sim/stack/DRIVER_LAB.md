# sim/stack: integração do Driver e do Evidence Collector (SIM-1.5)

Integração **aditiva** ao laboratório do SIM-1.4. Os 23 arquivos do SIM-1.4 continuam byte a byte como foram provados; um teste estático confere os hashes.

## O que existe aqui

- `docker-compose.sim.driver.yml`: override com `sim-driver` e `sim-collector` e a rede `auneron_sim_driver` (`internal: true`).
  - O `sim-backend` fica nas duas redes; o `sim-postgres` não entra na rede do Driver.
  - Driver e Collector falam com `http://sim-backend:8000`.
  - Proibidos: `network_mode: host`, `extra_hosts`, `host.docker.internal`, portas publicadas, socket do Docker e bind mounts.
- `driver.Dockerfile` e `collector.Dockerfile`: `python:3.11-slim` e só `sim/driver` ou `sim/collector`, sem pip.
- `lab/driver_lab.py`:
  - guarda do compose estendido (reaproveita a guarda do SIM-1.4);
  - manifesto do Driver e captura do `version_id` (fail closed);
  - segredos mínimos por container;
  - sonda de isolamento (DR-1) e o juiz da sonda;
  - medição O-1 e o juiz do limiar.
- `lab/run_orchestrator.py`: ordem do harness por slot e por dia.
  - Por slot: Collector pré, Driver, Collector pós.
  - Por dia: barreira diária às 18:00, depois a coleta `after_daily_barrier`.
  - D14: ativação do floor.
  - D90: restart e barreira extraordinária.
  - Fim: `end_of_run`.

## Não executado nesta fase

Nenhum Docker foi usado no CODE APPLY: build das imagens, `compose up` do override e os ensaios DR-1…DR-8 dependem do gate live (não autorizado). O orquestrador é exercitado só por testes estáticos com hooks injetados.
