"""Override the session-scoped wait_for_stack fixture for pure unit tests.

Unit tests are self-contained and must not require a running Docker stack.
"""
import pytest


@pytest.fixture(scope="session", autouse=True)
def wait_for_stack() -> None:  # noqa: PT004
    """No-op: unit tests do not need a live stack."""
