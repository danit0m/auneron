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

## Cenários v1 e v2

| Versão | Diretórios | Situação |
|---|---|---|
| `sim.scenario.v1` (SIM-1.2) | `scenarios/nh-*-s340001/` | **Histórico e imutável.** Presume `PUT status` e o F1, e a Discovery do SIM-1.3 mostrou que isso não é executável contra o produto (G-SIM-14, G-SIM-17). Continua sendo gerado byte a byte idêntico (T-16). |
| `sim.scenario.v2` (SIM-1.3) | `scenarios/nh-*-v2-s340001/` | Corredores públicos reais (detalhes abaixo). Entradas próprias em `company/nova_horizonte/v2/`. |

O que muda no v2:
- a baixa usa `mark_paid` governado: o gerente pede, o coordenador decide e o gerente executa;
- o atraso usa o corredor humano de `mark_overdue`; o F1 fica OUT/GAP;
- a expiração é derivada de `expires_at`;
- um `mark_overdue` expirado ou rejeitado deixa o episódio sem saída.

```bash
sim/.venv/Scripts/python -m sim.generator.cli generate --variant nh-standard --scenario-version v2
sim/.venv/Scripts/python -m sim.generator.cli hashes --variant nh-small --scenario-version v2
```

Claim atual: *scenario artifacts VERIFIED / deterministic*. *SIMULATION VERIFIED* só vale depois de o Auneron executar o mundo, e nunca é *OPERATIONALLY OBSERVED*.

## Mudanças de regra

O mundo e as regras replicadas estão congelados no Design Freeze v2. Qualquer mudança relevante exige uma revisão explícita do design ou da versão do cenário. Nunca se ajusta o gerador em silêncio.
