class PilotMutationError(Exception):
    pass


class PilotMutationValidationError(PilotMutationError):
    pass


class PilotMutationAuthorizationError(PilotMutationError):
    pass


class PilotMutationConflictError(PilotMutationError):
    pass


class PilotMutationStateError(PilotMutationError):
    pass
