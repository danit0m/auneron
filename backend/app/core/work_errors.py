class WorkError(Exception):
    """Erro-base de domínio do Work Manager."""


class WorkNotFoundError(WorkError):
    """Item de trabalho inexistente ou não acessível."""


class WorkValidationError(WorkError):
    """Entrada viola o contrato do Work Manager."""


class WorkConflictError(WorkError):
    """A operação conflita com estado persistido."""


class WorkVersionConflictError(WorkConflictError):
    """A versão esperada não corresponde à versão atual."""

    def __init__(
        self,
        *,
        expected_version: int,
        current_version: int,
    ) -> None:
        self.expected_version = expected_version
        self.current_version = current_version
        super().__init__(
            "Conflito de versão: "
            f"esperada {expected_version}, "
            f"atual {current_version}."
        )


class WorkIdempotencyConflictError(WorkConflictError):
    """Uma chave idempotente foi reutilizada com outro pedido."""


class WorkStateError(WorkError):
    """Operação incompatível com o estado do item."""


class WorkAuthorizationError(WorkError):
    """Ator não autorizado para a operação."""
