import axios from "axios";

import type {
  ApprovalPendingCountResponse,
} from "../types/approval";
import type {
  EscalationObservationListResponse,
  HumanAssessmentObservationResponse,
  HumanAssessmentRequest,
} from "../types/escalationObservation";

export const REQUEST_ID_HEADER =
  "X-Request-ID";

export const IDEMPOTENCY_KEY_HEADER =
  "Idempotency-Key";

function createOpaqueId(): string {
  if (
    typeof crypto !== "undefined" &&
    typeof crypto.randomUUID === "function"
  ) {
    return crypto.randomUUID();
  }

  return [
    "web",
    Date.now().toString(36),
    Math.random()
      .toString(36)
      .slice(2, 12),
  ].join("-");
}

function createRequestId(): string {
  return createOpaqueId();
}

/**
 * Gera uma chave de intencao para operacoes que aceitam
 * Idempotency-Key (ex.: POST de human-assessment). Uma nova escolha do
 * operador gera uma chave nova; um retry da MESMA intencao (timeout/
 * falha de rede) deve reenviar a MESMA chave, nunca gerar outra.
 */
export function createIdempotencyKey(): string {
  return createOpaqueId();
}

function getResponseRequestId(
  error: unknown,
): string | null {
  if (!axios.isAxiosError(error)) {
    return null;
  }

  const value =
    error.response?.headers?.[
      REQUEST_ID_HEADER.toLowerCase()
    ];

  return typeof value === "string"
    ? value
    : null;
}

function withRequestReference(
  message: string,
  error: unknown,
): string {
  const requestId =
    getResponseRequestId(error);

  return requestId
    ? `${message} Referência: ${requestId}.`
    : message;
}

/**
 * Erro de rede/timeout genuino (sem resposta HTTP) -- diferente de um
 * 4xx/409, que e uma resposta recebida com conteudo de erro. Usado
 * para decidir quando um resultado e "ambiguo" (nao se sabe se o
 * servidor processou antes da conexao cair) em vez de simplesmente
 * "erro".
 */
export function isNetworkFailure(error: unknown): boolean {
  return (
    axios.isAxiosError(error) && !error.response
  );
}

export function getApiErrorMessage(
  error: unknown,
  fallbackMessage: string,
): string {
  if (!axios.isAxiosError(error)) {
    return fallbackMessage;
  }

  if (!error.response) {
    return (
      "Não foi possível conectar ao backend. " +
      "Verifique se a API está em execução."
    );
  }

  const detail =
    error.response.data &&
    typeof error.response.data === "object" &&
    "detail" in error.response.data
      ? error.response.data.detail
      : null;

  if (typeof detail === "string") {
    return withRequestReference(
      detail,
      error,
    );
  }

  if (error.response.status === 401) {
    return withRequestReference(
      "Sua sessão expirou ou a credencial de acesso foi recusada.",
      error,
    );
  }

  if (error.response.status === 503) {
    return withRequestReference(
      "A autenticação da API está indisponível no backend.",
      error,
    );
  }

  return withRequestReference(
    fallbackMessage,
    error,
  );
}

const api = axios.create({
  baseURL: "/api",
  timeout: 15000,
  withCredentials: true,
  headers: {
    Accept: "application/json",
  },
});

api.interceptors.request.use(
  (config) => {
    if (
      !config.headers.has(
        REQUEST_ID_HEADER,
      )
    ) {
      config.headers.set(
        REQUEST_ID_HEADER,
        createRequestId(),
      );
    }

    return config;
  },
);

/**
 * Human Attention Indicator (light): count only, never the full
 * ApprovalListResponse -- this is meant to be polled from the Sidebar,
 * so it must never fetch full request payloads just to count them.
 */
export async function fetchApprovalsPendingCount(): Promise<number> {
  const response =
    await api.get<ApprovalPendingCountResponse>(
      "/approvals/pending-count",
    );

  return response.data.count;
}

/**
 * VALUE-2.4 -- historico de observations de um episodio de
 * escalonamento. after_id/observationType sao opcionais; sem eles, a
 * primeira pagina (id ASC, ordem estavel de append, nunca
 * "cronologica") e devolvida.
 */
export async function fetchEscalationObservations(
  workItemId: number,
  params?: {
    afterId?: number;
    observationType?: "observed_fact" | "human_assessment";
  },
): Promise<EscalationObservationListResponse> {
  const response =
    await api.get<EscalationObservationListResponse>(
      `/work-items/${workItemId}/escalation-observations`,
      {
        params: {
          after_id: params?.afterId,
          observation_type: params?.observationType,
        },
      },
    );

  return response.data;
}

/**
 * VALUE-3.2B -- registra uma declaracao humana sobre um episodio de
 * escalonamento. idempotencyKey e obrigatorio aqui (nao opcional como
 * no contrato HTTP) porque o chamador e sempre responsavel por decidir
 * se esta e uma nova intencao (nova chave) ou um retry da mesma
 * intencao (mesma chave) -- nunca deve ser esquecido por omissao.
 */
export async function submitHumanAssessment(
  workItemId: number,
  payload: HumanAssessmentRequest,
  idempotencyKey: string,
): Promise<{
  observation: HumanAssessmentObservationResponse;
  duplicate: boolean;
}> {
  const response =
    await api.post<HumanAssessmentObservationResponse>(
      `/work-items/${workItemId}/human-assessment`,
      payload,
      {
        headers: {
          [IDEMPOTENCY_KEY_HEADER]: idempotencyKey,
        },
      },
    );

  return {
    observation: response.data,
    duplicate: response.status === 200,
  };
}

export default api;
