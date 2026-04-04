"""Nubi exception hierarchy."""


class NubiError(Exception):
    """Base exception for all Nubi errors."""


class TaskSpecValidationError(NubiError):
    """Raised when a TaskSpec fails Pydantic validation."""


class PhaseTransitionError(NubiError):
    """Raised when an invalid phase transition is attempted."""


class HandlerError(NubiError):
    """Raised when a kopf handler encounters an error."""
