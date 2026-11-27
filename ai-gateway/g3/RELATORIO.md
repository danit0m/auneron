# RELATORIO: preparacao da G3 (NAO executada)

Pasta: `C:\Users\Tomaz\AppData\Local\Temp\claude\3f8f7fcbff3437eb\e022271a-4bb1-4a75-85a7-edf63593d450\scratchpad\g3-prep`. Base lida: commit `1bea10d` (so `git show`) e as pastas `aval-qwen-*` de Downloads. Nada fora desta pasta foi escrito; nenhum modelo, rede, GPU, nvidia-smi, `.env` ou VPS foi tocado. Python 3.11.9, so biblioteca padrao.

## Achados principais

1. O prompt/esquema de hoje sao byte a byte os das rodadas de 18/11 (hash do modulo igual ao gravado); v1 e v2 conferem com os hashes das NOTAS.
2. Com seed 0, o braco B e deterministico: 67/67 respostas de 2048 repetidas em 4096 com os mesmos tokens. Truncados em 2048 terminaram em 2062-3347 tokens, todos certos: "invalida" era orcamento, nao erro.
3. Velocidade real ~90 tok/s (nao 19): rodada de 80 casos = ~19 min. Cauda: 1/160 chamadas truncou em 4096 (IC95 0,02% a 3,4%).
4. Nao existe medida do prompt v2 sem raciocinio nem do modelo instruct-2507. A "melhora" de 16/11 para 18/11 mudou 4 coisas juntas; nao prova efeito do raciocinio. Este e o maior buraco.
5. v1 e ajuste; v2 foi prova uma vez e agora e desenvolvimento. O v3 e a unica prova possivel. No shadow real so entram contas ja marcadas pela regra, entao a classe 50/50 nao e a de producao.
6. Zero eventos em 148 casos ainda deixa o limite superior de ~2,5%. McNemar com 148 pares so detecta diferencas de ~5-8 pontos: empate e o resultado provavel e o protocolo ja pre-registra o desempate (A prevalece).

## Entregas

| Arquivo | O que e |
|---|---|
| `ANALISE.md` | releitura dos resultados, confusores, IC, riscos, como reduzir truncamento sem tocar no prompt |
| `PROTOCOLO_G3_v1.md` | desenho original, INTACTO (sha256 e968b7c2...) |
| `PROTOCOLO_G3_v2.md` | emenda de 2026-11-24: C6, novo desempate, McNemar descritivo, relatorio obrigatorio, bloqueio+liberacao |
| `g3eval/gen_v3.py`, `sealed/cases_v3.json` | gerador deterministico e o v3 selado (168 casos, somente leitura) |
| `g3eval/verify_v3.py` | verificador independente (2a implementacao, sem datetime) |
| `g3eval/evaluator.py`, `fake.py`, `transport.py`, `report.py`, `plan.py`, `cli.py`, `frozen.py`, `stats.py` | avaliador offline com transporte injetavel, limites, retomada, so metricas, relatorio comparativo |
| `config/blackout.json`, `config/authorization.TEMPLATE.json` | janela de bloqueio; modelo de autorizacao invalido de proposito |
| `tests/` (89 testes), `mutation/run_mutations.py` + `RESULTS.txt` | suite unittest e 36 mutacoes |
| `data/` | copias de v1, v2 e do modulo do shadow (hash fixado) so como entrada de testes |
| `work/` | scripts de analise (an.py, an2.py, power_table.py, mk_frozen.py) e copias de apoio |

## Testes e mutacao (executados aqui)

- `python -m unittest discover -s tests` (PYTHONPATH = raiz do pacote e `tests`): 103 testes, OK, ~9 s (apos a emenda v2).
- `python mutation/run_mutations.py`: baseline OK; 47 mutantes, 47 mortos, 0 sobreviventes, 0 invalidos (`mutation/RESULTS.txt`), incluindo C6, desempate (a)(b)(c), liberacao e recursos. Mortos = a suite falhou; nao prova que cada teste e o "certo", so que a mudanca nao passa despercebida.
- `gen_v3 --check` confere o arquivo; `verify_v3` passou: 168 casos, sem sobreposicao com v1/v2/exemplos do prompt, core balanceado 60/60, bordas cobertas.

## O que NAO foi feito ou verificado

- Nenhuma chamada a Ollama, nenhum nvidia-smi (so o parser e testado com texto de exemplo), nenhuma medida do braco A, nenhum tempo real.
- O cliente HTTP real nao existe: `RealOllamaTransport` aceita um `http` injetado e tem 3 travas fechadas (build, flag, autorizacao); nunca foi construido fora de teste com a trava aberta por mock.
- Nao se verificou se o Ollama aceita a requisicao do instruct sem `think` (item aberto, protocolo secao 11).
- Estimativa de tempo de A (3,5 s/caso) e de outro modelo; `prompt_tokens` (~650-700) e estimativa.

## Riscos e pontos para revisao

- Os limites C2 (<=1/72) e C3 (<=3/168) sao propostas minhas; precisam de aprovacao antes do selo.
- Abstencao mapeada para `no_action` e politica; os 20 casos nao decidem aceite. Se o PO preferir que decidam, mudar antes de rodar.
- A leitura da janela (24/11 18h a 26/11 00h) e a mais conservadora de um texto ambiguo; confirmar.
- O autor conhece o v3 e a regra: o selo protege contra ajuste futuro, nao contra vies do autor na escolha dos casos.
- `work/` contem copias de v1/v2/modulo (de `git show`); sao dados de repositorio, sem segredo. Remover se o PO nao quiser.

## Emenda v2 (2026-11-24)

Aplicada pre-resultado; o v3 (6305167845...) e prompt/opcoes nao mudam. C1-C5 aprovados; C6 novo e obrigatorio; desempate = erros nos 168, depois p95, p50, instruct; McNemar so descritivo; sem vencedor se ninguem passa; relatorio com p50/p95/max e RAM/VRAM antes/pico/depois; blackout ate 2026-11-26T22:00 mais liberacao assinada (24 h da 1bea10d, G1, autorizacao expressa). Detalhes em `PROTOCOLO_G3_v2.md`.
