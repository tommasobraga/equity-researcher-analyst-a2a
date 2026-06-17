"""Typed exceptions for the A2A pipeline.

Distinguere il tipo di errore permette a tenacity di applicare
politiche di retry selettive: RateLimitError viene ritentato con
backoff esponenziale; AgentUnavailableError apre il circuit breaker
e non viene ritentato.
"""


class A2AError(Exception):
    """Base class for A2A pipeline errors."""


class RateLimitError(A2AError):
    """Agent returned 429 or a rate-limit message in the response body."""


class AgentTimeoutError(A2AError):
    """Agent did not respond within the configured timeout."""


class AgentUnavailableError(A2AError):
    """Agent is unreachable, returned 5xx, or circuit breaker is open."""
