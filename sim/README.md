# SIM: Nova Horizonte (laboratório de simulação do Auneron)

> **SIMULATION VERIFIED ≠ OPERATIONALLY OBSERVED.** Todo resultado obtido com
> este harness vale só para uma população sintética conhecida e nunca é
> evidência operacional.

Este diretório implementa o **SIM-1.2** (Design Freeze v2 aprovado):

- a empresa fictícia Nova Horizonte Distribuidora;
- um gerador determinístico do seu mundo financeiro de 180 dias;
- um Oracle independente.

O harness não importa `backend/` nem `frontend/`, não usa Docker nem banco e não altera o produto.

## Estrutura

| Caminho | Conteúdo |
|---|---|
| `company/nova_horizonte/` | Entradas normativas: empresa, população, calendário (fictício e normativo), catálogo de casos, gramática de nomes |
| `generator/` | Gerador determinístico: canonicalização, sub-streams, simulação do mundo, agenda pública, casos, CLI |
| `oracle/` | Réplica versionada das regras do produto, expectativas `policy`/`ideal`, validação e invalidação |
| `scenarios/<variante>-s<seed>/` | Artefatos congelados: `manifest.json`, `public_agenda.json`, `world.json`, `oracle.json` |
| `tests/` | Gate T-1..T-10 |

Fases seguintes (stack `auneron_sim`, Driver, Evaluator) **não** estão aqui.

## Separação do Oracle

- `public_agenda.json` é a única entrada do futuro Driver, lida só via `PublicAgendaReader`, dia a dia.
- `world.json` e `oracle.json` nunca chegam ao Driver nem ao stack do produto.
- Uma violação de separação torna o cenário **INVALID**.
- Qualquer `HARNESS_ERROR` invalida o run inteiro.

## Semântica de tempo

- `Dn` = D0 + n dias, com n de 0 a 179 (D0 = 2026-01-05, go-live).
- "180 dias" = D0 mais 179 avanços.
- O calendário em `calendar.yaml` é **normativo e fictício** e não reproduz o calendário bancário brasileiro real.

## Uso (a partir da raiz do repositório)

Crie um venv isolado e instale as dependências do harness. Se o proxy da máquina interceptar TLS, use `--use-feature=truststore`:

```bash
python -m venv sim/.venv
sim/.venv/Scripts/python -m pip install --use-feature=truststore -r sim/requirements.txt
```

Gerar e verificar um cenário:

```bash
sim/.venv/Scripts/python -m sim.generator.cli generate --variant nh-standard --seed 340001
sim/.venv/Scripts/python -m sim.generator.cli verify sim/scenarios/nh-standard-s340001
```

Imprimir os hashes sem gravar nada (prova de determinismo entre sistemas operacionais):

```bash
sim/.venv/Scripts/python -m sim.generator.cli hashes --variant nh-small
```

Rodar o gate T-1..T-10:

```bash
sim/.venv/Scripts/python -m pytest sim/tests -q
```

## Mudanças de regra

O mundo e as regras replicadas estão congelados no Design Freeze v2. Qualquer mudança relevante exige uma revisão explícita do design ou da versão do cenário. Nunca se ajusta o gerador em silêncio.
