class WorkflowError(Exception):
    """A classified condition that the runner can persist and resume."""

    def __init__(self, message, kind="permanent", retry_after=None, details=None):
        super().__init__(message)
        self.kind = kind
        self.retry_after = retry_after
        self.details = details or {}


class Waiting(WorkflowError):
    def __init__(self, message, kind="waiting_owner", details=None):
        super().__init__(message, kind=kind, details=details)


class UncertainWrite(WorkflowError):
    def __init__(self, message, details=None):
        super().__init__(message, kind="uncertain", details=details)
