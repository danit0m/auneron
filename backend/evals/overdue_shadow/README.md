# Avaliacao do shadow de vencidos - conjuntos de casos

- `cases_v1.json` (gerador `generate_cases.py`): conjunto de AJUSTE.
  Usado para medir o prompt v1; pode orientar a correcao do prompt.
- `cases_v2.json` (gerador `generate_cases_v2.py`): conjunto de PROVA,
  inedito e selado. Nao use para ajustar prompt; se for usado assim,
  deixa de ser prova e um v3 deve ser gerado.

Ambos seguem a mesma regra de rotulo (`mark_overdue` sse status `aberto`
e `due_date < business_date`, estrito). Os arquivos sao `-text` no
`.gitattributes`: o SHA-256 selado depende dos bytes exatos.

Rodada real (a partir de `backend/`, pasta de saida NOVA a cada rodada):

    python -m scripts.eval_overdue_shadow --cases evals/overdue_shadow/cases_v1.json --expected-sha256 <sha v1> --out-dir <pasta nova>   # ajuste
    python -m scripts.eval_overdue_shadow --cases evals/overdue_shadow/cases_v2.json --expected-sha256 <sha v2> --out-dir <pasta nova>   # prova
