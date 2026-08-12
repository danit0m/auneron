class MemoryError(Exception):
    """Erro-base de domínio do Memory System."""


class MemoryNotFoundError(MemoryError):
    """Memória inexistente ou não acessível."""


class MemoryValidationError(MemoryError):
    """Entrada viola contrato do domínio de memória."""


class MemoryConflictError(MemoryError):
    """Existe memória ativa conflitante para a mesma chave."""


class MemoryStateError(MemoryError):
    """Transição de lifecycle inválida."""


class MemoryAuthorizationError(MemoryError):
    """Ator não autorizado para a operação."""


class EvidenceDuplicateError(MemoryError):
    """Evidence equivalente já registrada."""


class InvalidCursorError(MemoryError):
    """Cursor de recall inválido."""
