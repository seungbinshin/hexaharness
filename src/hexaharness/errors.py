class HexaHarnessError(Exception):
    """Base class for expected, user-facing HexaHarness failures."""


class PolicyBlockedError(HexaHarnessError):
    """An action was denied or needs approval."""


class BudgetExceededError(HexaHarnessError):
    """A configured execution budget was exhausted."""


class HarnessStoppedError(HexaHarnessError):
    """The emergency stop is active."""


class TaskNotFoundError(HexaHarnessError):
    """A requested task checkpoint does not exist."""


class VerificationFailedError(HexaHarnessError):
    """One or more required computational sensors failed."""
