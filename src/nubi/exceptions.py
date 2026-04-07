"""Nubi exception hierarchy."""


class NubiError(Exception):
    """Base exception for all Nubi errors."""


class TaskSpecValidationError(NubiError):
    """Raised when a TaskSpec fails Pydantic validation."""


class PhaseTransitionError(NubiError):
    """Raised when an invalid phase transition is attempted."""


class HandlerError(NubiError):
    """Raised when a kopf handler encounters an error."""


class NamespaceError(NubiError):
    """Raised when namespace lifecycle operations fail."""


class CredentialError(NubiError):
    """Raised when credential scoping operations fail."""


class SandboxError(NubiError):
    """Raised when sandbox job operations fail."""


class ResultError(NubiError):
    """Raised when reading executor results fails."""


class ReviewError(NubiError):
    """Raised when reviewer operations fail."""
