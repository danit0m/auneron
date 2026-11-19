# Painel de operações governadas: regra de contagem

Endpoint: `GET /dashboard/governed-operations-summary`.
Código: `app/services/governed_operations_summary_service.py`.

O painel é puramente projetivo: lê dado já persistido e não cria autoridade,
execução ou tabela.

## Disposições de account.mark_overdue (KPI-2 / KPI-3)

Contam eventos de conta com `new_status = 'atrasado'` e `occurred_at` no
período (intervalo semiaberto). A origem é identificada pelo prefixo de
`AccountEvent.idempotency_key`. Cada evento cai em no máximo uma origem.

| Origem | Prefixo da chave | Campo da API |
| --- | --- | --- |
| Autônoma L3 (política, sem humano no ato) | `account_event:effect:policy_account_mark_overdue:` | `autonomous_dispositions` |
| Manual (humano tomou a iniciativa e aprovou) | `account_event:effect:human_account_mark_overdue:approval:` | `manual_human_dispositions` |
| Agente com aprovação humana (OverdueDetectionAgent pediu, pessoa aprovou, dispatch executou) | `account_event:effect:account.mark_overdue:approval:` | `agent_initiated_human_approved_dispositions` |

Campo derivado: `human_governed_dispositions = manual_human_dispositions +
agent_initiated_human_approved_dispositions`. O agente nunca executa sem
aprovação humana, por isso a origem "agente" é sempre uma disposição com
aprovação humana, mas fica separada para não esconder quem teve a iniciativa.

Taxa autônoma: `autonomous_disposition_rate = autonomous / (autonomous +
human_governed)`, ou `null` se o denominador for zero. Efeito da regra: as
execuções do agente aprovadas por pessoa entram no denominador (antes ficavam
fora da contagem), o que reduz a taxa autônoma quando existem. L3 não muda.

Ficam fora, deliberadamente: alteração administrativa (chave nula),
`account.mark_paid` e qualquer outro produtor com prefixo distinto.

## Demais indicadores

- KPI-1 `eligible_accounts_identified`: contas com vencimento no período que
  não estavam pagas até o vencimento. A data de pagamento é a data efetiva
  `account_payments.paid_on` (decisão da diretoria); o evento "pago" em
  `account_events` fica para rastreabilidade e só indica que o título foi pago.
  Elegível = não paga, ou `paid_on` posterior ao vencimento (pagamento no dia do
  vencimento não conta). Um registro tardio (ex.: `paid_on` 10/11, evento
  14/11, vencimento 12/11) não é "pago depois do vencimento".
- `eligible_accounts_unknown_payment_date`: título pago (evento "pago" ou status
  "pago") sem registro em `account_payments`. Situação desconhecida: não entra
  como elegível nem como não elegível; é contado à parte (a tela mostra "N sem
  data de pagamento").
- Detecção até pagamento (`/outcomes/.../episodes`, `derived`):
  `days_detection_to_payment_registration` mede o tempo até o REGISTRO do
  pagamento (evento "pago"), não até a data efetiva. O nome antigo
  `days_detection_to_payment` permanece como alias com o mesmo valor. A medida
  baseada na data efetiva (`paid_on`) está PENDENTE DE DEFINIÇÃO própria,
  incluindo o caso de pagamento anterior à detecção; não existe ainda.
- KPI-4 `autonomous_effect_verification`: consumos de autoridade L3 de
  `account.mark_overdue` e seu resultado de verificação de efeito. Cobre só a
  origem autônoma; `total` deve igualar `autonomous_dispositions`.
- `pending_overdue_accounts_now`: contas em aberto e vencidas agora.
