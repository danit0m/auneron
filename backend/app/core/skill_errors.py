class SkillError(Exception):
    """Erro-base de domínio do catálogo de Agent Skills."""


class SkillNotFoundError(SkillError):
    """Skill, versão ou binding inexistente."""


class SkillValidationError(SkillError):
    """Entrada viola o contrato de Agent Skills."""


class SkillConflictError(SkillError):
    """A operação conflita com identidade persistida."""


class SkillStateError(SkillError):
    """Operação incompatível com o lifecycle da skill."""


class SkillImmutableError(SkillStateError):
    """Tentativa de alterar ou excluir versão publicada."""

class SkillRuntimeError(SkillError):
    """Falha controlada na fronteira de execução de Agent Skills."""


class SkillHandlerNotAllowedError(SkillRuntimeError):
    """Handler não está registrado na allowlist de runtime."""


class SkillSchemaError(SkillRuntimeError):
    """Schema persistido não pode ser usado com segurança."""


class SkillInputValidationError(SkillValidationError):
    """Payload de entrada não satisfaz o contrato publicado."""


class SkillOutputValidationError(SkillRuntimeError):
    """Saída do handler não satisfaz o contrato publicado."""


class SkillOutputLimitError(SkillRuntimeError):
    """Saída serializada excede o limite publicado."""


class SkillExecutionTimeoutError(SkillRuntimeError):
    """Execução não concluiu dentro do timeout publicado."""


class SkillExecutionError(SkillRuntimeError):
    """Handler falhou sem expor detalhes internos ao chamador."""


class SkillIdempotencyConflictError(SkillConflictError):
    """Chave idempotente foi reutilizada para outro pedido."""


class SkillInvocationInProgressError(SkillConflictError):
    """Invocação idempotente equivalente ainda está em execução."""


class SkillRuntimeBusyError(SkillRuntimeError):
    """Runtime atingiu o limite de concorrência disponível."""

class SkillAuthorizationError(SkillError):
    """Ator autenticado não possui autoridade para executar a skill."""


class SkillScopeNotFoundError(SkillNotFoundError):
    """Recurso de escopo é inexistente ou não acessível ao ator."""
