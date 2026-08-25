class ApprovalError(Exception):
    """Erro-base do domínio de aprovação e autonomia."""


class ApprovalNotFoundError(ApprovalError):
    """Solicitação de aprovação inexistente ou indisponível."""


class ApprovalValidationError(ApprovalError):
    """Entrada viola o contrato do domínio de aprovação."""


class ApprovalConflictError(ApprovalError):
    """A operação conflita com uma aprovação persistida."""


class ApprovalStateError(ApprovalError):
    """Operação incompatível com o estado da aprovação."""


class ApprovalAuthorizationError(ApprovalError):
    """Ator sem autoridade para decidir a aprovação."""


class ApprovalIdempotencyConflictError(ApprovalConflictError):
    """Chave idempotente foi reutilizada para outra ação."""


class ApprovalExpiredError(ApprovalStateError):
    """Solicitação expirou antes da decisão humana."""


class ApprovalElevationRequiredError(ApprovalAuthorizationError):
    """Decisão sensível exige autenticação elevada recente."""

class ApprovalRequiredError(ApprovalStateError):
    """A política exige aprovação humana antes da execução."""


class ApprovalConsumptionConflictError(ApprovalConflictError):
    """Consumo persistido diverge da ação aprovada/executada."""
