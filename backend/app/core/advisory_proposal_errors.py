class AdvisoryProposalError(Exception):
    """Base error for durable authenticated advisory proposals."""


class AdvisoryProposalValidationError(AdvisoryProposalError):
    """Input violates the immutable advisory proposal contract."""


class AdvisoryProposalConflictError(AdvisoryProposalError):
    """Persisted advisory proposal state conflicts with the request."""


class AdvisoryProposalIdempotencyConflictError(
    AdvisoryProposalConflictError
):
    """One idempotency identity was reused for different advisory content."""


class AdvisoryProposalNotFoundError(AdvisoryProposalError):
    """Durable advisory proposal does not exist."""


class AdvisoryProposalConsumptionAuthorizationError(
    AdvisoryProposalError
):
    """Current authenticated authority cannot consume the proposal."""


class AdvisoryProposalConsumptionStaleError(
    AdvisoryProposalConflictError
):
    """Persisted advisory proposal no longer matches current safe state."""

class AdvisoryProposalDispatchNotAllowedError(AdvisoryProposalError):
    """Validated advisory candidate is not eligible for governed 25L dispatch."""

class AdvisoryProposalApprovalNotAllowedError(AdvisoryProposalError):
    """Validated advisory candidate is not eligible for the 25M Approval bridge."""


class AdvisoryProposalApprovalCorrelationError(
    AdvisoryProposalConflictError
):
    """Persisted Approval identity diverges from the current advisory candidate."""
