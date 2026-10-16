"""Erros do plano de controle. Todos falham FECHADO: nenhum e capturado para seguir em frente."""

from __future__ import annotations


class RunnerError(RuntimeError):
    """Base: qualquer violacao de contrato do plano de controle."""


class RimError(RunnerError):
    pass


class RegistryError(RunnerError):
    pass


class LedgerError(RunnerError):
    pass


class LifecycleError(LedgerError):
    pass


class InterlockError(RunnerError):
    pass


class DiskGuardError(RunnerError):
    pass


class PackageError(RunnerError):
    pass


class LockError(RunnerError):
    pass


class ResumeRefused(RunnerError):
    """R0: um run nao-terminal NUNCA e retomado."""


class OperatorStop(RunnerError):
    pass


class PreflightFailed(RunnerError):
    pass
