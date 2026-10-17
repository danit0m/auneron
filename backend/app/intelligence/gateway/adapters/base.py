from abc import ABC
from abc import abstractmethod

from app.intelligence.gateway.contracts import ProviderRequest
from app.intelligence.gateway.contracts import ProviderResponse


class ProviderAdapter(ABC):
    """
    Contract every provider adapter must satisfy.

    * invoke() must enforce request.timeout_seconds itself (the
      gateway cannot interrupt a synchronous call).
    * Failures are raised as ProviderError subclasses; the CLASS
      selects the ledger transition, message text is never used.
    * Raise ProviderNotSentError only with proof nothing was
      transmitted. Anything ambiguous must be a timeout/transport
      error (=> unknown, never free).
    * Adapters never execute provider tools and never receive
      credentials from this phase (none exist).
    """

    provider_kind: str = ""

    @property
    def is_fake(self) -> bool:
        return self.provider_kind == "fake"

    @abstractmethod
    def invoke(self, request: ProviderRequest) -> ProviderResponse:
        raise NotImplementedError
