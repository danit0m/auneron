# sim/stack: laboratório isolado `auneron_sim` (SIM-1.4)

Stack **descartável e isolado** para rodar o Auneron sob relógio virtual (libfaketime). Base: Design Freeze SIM-1.4 V1.1.

O laboratório não executa cenário nenhum. A execução da Nova Horizonte é escopo do SIM-1.5/1.6.

## Fronteiras

- **Tudo é próprio do lab:**
  - projeto Compose `auneron-sim`;
  - containers `auneron-sim-*`;
  - volumes e rede `auneron_sim_*`;
  - imagens `auneron-sim-{base,backend,postgres}:<sha12>`;
  - portas `127.0.0.1:8100` (API) e `127.0.0.1:5434` (Postgres).
- **Nunca toca** DEV, `auneron_test`, nem qualquer stack ou banco fora desse prefixo. Duas guardas protegem isso:
  - **Guarda externa** (`lab/guard.py`): roda antes de todo comando e valida o `docker compose config` resolvido. Qualquer violação aborta a execução, sem tentar corrigir o ambiente.
  - **Perfil `production` do produto:** `EXPECTED_DATABASE_NAME=auneron_sim` e `EXPECTED_DATABASE_HOST=sim-postgres`.
- **Imagens:** construídas de `git archive <commit>` (fora do repo), nunca da árvore de trabalho. A base e a derivada passam C1–C5 de forma independente.
- **Invariantes:**
  - SIM-CLOCK-1: o relógio fica num volume nativo;
  - SIM-CLOCK-2: `FAKETIME_NO_CACHE=1`;
  - SIM-MAINT-1: `MAINTENANCE_ENABLED=true` só neste stack.
- **Segredos:** sintéticos, gerados por instância e gravados em `AUNERON_SIM_HOME` (default `~/.auneron-sim`). Ficam fora do repo e nunca são impressos.
- **SQL:** só leitura, em transação `READ ONLY`, e só para infraestrutura do lab (D-1.4.1-1). Não é evidência de negócio.
- **Floor de evidência:** só em D14 e só pelo procedimento real: `evidence_floor_preflight.py` (`capture`/`check`) seguido da recriação do `sim-backend`.

## Uso (fase a fase)

```bash
python -m pytest sim/tests -q
python -m sim.stack.lab.cli guard --new
python -m sim.stack.lab.cli snapshot-before
python -m sim.stack.lab.cli build
python -m sim.stack.lab.cli up
python -m sim.stack.lab.cli provision
python -m sim.stack.lab.cli rbac
python -m sim.stack.lab.cli probe
python -m sim.stack.lab.cli clock
python -m sim.stack.lab.cli quiescence
python -m sim.stack.lab.cli floor
python -m sim.stack.lab.cli restart
python -m sim.stack.lab.cli isolation
python -m sim.stack.lab.cli teardown
python -m sim.stack.lab.cli snapshot-after
python -m sim.stack.lab.cli summary
```

A evidência de cada fase fica em `<AUNERON_SIM_HOME>/<instance>/evidence.json`.

### Rodada canônica (LIVE GATE)

```
reset → snapshot-pre-canonical → s1-reuse (ou build) → up → provision → rbac → probe
      → clock → quiescence → floor → restart → isolation → teardown → snapshot-after
```

A rodada é **sem intervenção manual** e para no primeiro FAIL ou ABORT.

- `reset` destrói só o lab descartável (`down -v`, com guarda) e arquiva a tentativa anterior.
- `s1-reuse` só é aceito com prova mecânica de que os insumos das imagens não mudaram:
  - image IDs iguais aos do S-1;
  - Dockerfiles derivados sem `COPY`/`ADD` e anteriores ao build;
  - C1–C5 verificados de novo.

  Caso contrário, rode `build`.
- `snapshot-after` é comparado tanto com o snapshot anterior à 1ª operação live quanto com o pre-snapshot canônico.

O Clock Controller aceita uma tolerância de 1 s contra retrocesso. É uma propriedade mecânica, porque o offset do libfaketime é um inteiro de segundos. Qualquer retrocesso material é recusado.
