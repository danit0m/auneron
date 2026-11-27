"""Erros do Gateway: codigos estaveis, sem texto livre e sem conteudo de entrada/saida."""

from __future__ import annotations

# codigo -> status HTTP. O corpo de erro e sempre {"error": {"code": <codigo>}}.
ERROR_STATUS: dict[str, int] = {
    "bad_request": 400,
    "invalid_json": 400,
    "invalid_contract": 422,
    "not_found": 404,
    "method_not_allowed": 405,
    "request_timeout": 408,
    "payload_too_large": 413,
    "header_too_large": 431,
    "unsupported_media_type": 415,
    "rate_limited": 429,
    "busy": 429,
    "deadline_exceeded": 504,
    "unavailable": 503,
    "model_missing": 503,
    "model_mismatch": 502,
    "invalid_output": 502,
    "output_too_large": 502,
    "output_truncated": 502,
    "upstream_error": 502,
    "internal_error": 500,
}


class GatewayError(Exception):
    """Erro com codigo estavel. Nunca carrega texto do Ollama nem da entrada."""

    def __init__(self, code: str, *, retry_after: int | None = None) -> None:
        if code not in ERROR_STATUS:
            raise ValueError("codigo de erro desconhecido")
        super().__init__(code)
        self.code = code
        self.status = ERROR_STATUS[code]
        self.retry_after = retry_after

    def __str__(self) -> str:  # nunca incluir nada alem do codigo
        return self.code
