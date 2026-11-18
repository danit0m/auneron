# Avaliacao do shadow de vencidos - conjuntos de casos

- `cases_v1.json` (gerador `generate_cases.py`): conjunto de AJUSTE.
  Usado para medir o prompt v1; pode orientar a correcao do prompt.
- `cases_v2.json` (gerador `generate_cases_v2.py`): conjunto de PROVA,
  inedito e selado. Nao use para ajustar prompt; se for usado assim,
  deixa de ser prova e um v3 deve ser gerado.

Ambos seguem a mesma regra de rotulo (`mark_overdue` sse status `aberto`
e `due_date < business_date`, estrito). Os arquivos sao `-text` no
`.gitattributes`: o SHA-256 selado depende dos bytes exatos.

O avaliador roda com RACIOCINIO LIGADO (`think=true`, 4096 tokens de saida (era 2048; a v1 de 18/11 truncou 13/80 casos nesse limite),
amostragem 0,6 / 0,95 / 20 com seed 0, timeout 300 s por caso, 1 tentativa),
as mesmas configuracoes do shadow (perfil `local-qwen3-4b`). O texto do
raciocinio nunca e gravado (so `thinking_length` por caso). Cada conjunto
tem 80 casos: estime de ~35 a ~70 min por conjunto (30 a 50 s por caso a
~19 tok/s) e ate ~2,5 h no pior caso (todos os casos no limite de 4096
tokens; ~4,8 h). O primeiro caso inclui a carga a frio (~33 s).

Rodada real (a partir de `backend/`, pasta de saida NOVA a cada rodada):

    python -m scripts.eval_overdue_shadow --cases evals/overdue_shadow/cases_v1.json --expected-sha256 <sha v1> --out-dir <pasta nova>   # ajuste
    python -m scripts.eval_overdue_shadow --cases evals/overdue_shadow/cases_v2.json --expected-sha256 <sha v2> --out-dir <pasta nova>   # prova
