// Fatia 2C -- tipos do frontend para a classificacao comportamental do
// cliente, espelhando o contrato read-only da Fatia 2B
// (AccountClassificationResponse / ClientClassificationDetail em
// backend/app/schemas/account.py). Nao ha logica de negocio aqui --
// apenas o formato dos dados.

export type ClientClassificationLabel =
  | "PAGAMENTO_REGULAR"
  | "ATRASO_RECORRENTE"
  | "INSUFFICIENT_DATA";

export interface ClientClassificationDetail {
  label: ClientClassificationLabel;
  reason: string | null;
  rule_version: string;
  classified_at: string;
  resolved_occurrences: number;
  late_occurrences: number | null;
  late_ratio: number | null;
  minimum_required_occurrences: number;
  late_ratio_threshold: number;
  analysis_scope: string;
  period_start: string | null;
  period_end: string | null;
}

export interface AccountClassificationResponse {
  account_id: number;
  email: string | null;
  status: "not_classified_yet" | "classified";
  classification: ClientClassificationDetail | null;
}

// Estado de UI -- estende a resposta do backend com os dois estados que
// só existem no frontend (carregando, falha de rede/HTTP/404). O
// contrato UX exige que esses dois nunca sejam confundidos com
// "not_classified_yet", que só existe quando o backend responde 200
// com status="not_classified_yet" explicitamente.
export type ClassificationUIState =
  | { kind: "loading" }
  | { kind: "request_failed" }
  | { kind: "resolved"; data: AccountClassificationResponse };
