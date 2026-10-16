# sim/rehearsal: fixture `[SIM-HARNESS]` do SR-1 (SIM-1.5F)

Gera, de forma **deterministica**, uma populacao tecnica que reproduz os **picos** do `nh-small` (rajada de 105 criacoes,
dia de 448 operacoes, aprovacoes, escalonamentos, fatos observados pos-floor, restart com 900 contas, varredura final de
>= 2.000 itens, slots tardios 20:59/22:30/23:59/00:00, rejeicoes de C-AUTH-2, grupos concorrentes e replays) em 21 dias
virtuais, **sem usar a populacao empresarial**.

| Modulo | Papel |
|---|---|
| `profile.py` + `profiles/nh_peak_profile_v1.json` | perfil de carga **so de inteiros**, fixado por sha256 |
| `extract_profile.py` | utilitario de autoria (unico que le a agenda do NH, so para contar). O gerador **nao** o importa |
| `schedule.py` | cronograma (PRNG proprio SplitMix64) -> operacoes da agenda publica e linha do tempo das contas |
| `oracle.py` | Oracle tecnico **por desenho** (regras publicas), ja no primeiro snapshot observavel (TA-1) + verificador TA-1 independente |
| `generate.py` | escrita canonica do bundle: `public_agenda.json`, `world.json`, `manifest.json`, `oracle.json`, `fixture_manifest.json` |
| `contamination.py` | marcadores, disjuncao com outra agenda, varredura de arvores (texto, `.gz`, SQLite) e juiz do check P14 |
| `cli.py` | `generate`, `verify`, `scan` |

Identidade: `scenario_id = sim-harness-sr1-s151001`, seed `151001`, marcadores `SIM-HARNESS-SR1` e
`sim-harness-sr1.example.com`, refs `SH-*`. Nunca `nh-*`.

Nada aqui e executado contra o laboratorio por este pacote; o SR-1 usa o bundle via `python -m sim.runner.cli`.
