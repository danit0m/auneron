import axios from "axios";

export const REQUEST_ID_HEADER =
  "X-Request-ID";

function createRequestId(): string {
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

  if (error.response.status === 401) {
    return withRequestReference(
      "A API recusou a credencial de acesso. " +
        "Verifique a configuração segura do proxy.",
      error,
    );
  }

  if (error.response.status === 503) {
    return withRequestReference(
      "A autenticação da API está indisponível no backend.",
      error,
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

  return withRequestReference(
    fallbackMessage,
    error,
  );
}

const api = axios.create({
  baseURL: "/api",
  timeout: 15000,
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

export default api;
