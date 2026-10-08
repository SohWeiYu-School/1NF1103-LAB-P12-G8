"""Shared pytest fixtures."""

import logging

import pytest


@pytest.fixture(autouse=True)
def _suppress_console_logging():
    """Prevent any log records from reaching the console during tests."""
    root = logging.getLogger()
    null = logging.NullHandler()
    root.addHandler(null)
    # Remove any StreamHandlers that may have been added before the test
    stream_handlers = [h for h in root.handlers if isinstance(h, logging.StreamHandler)
                       and not isinstance(h, logging.FileHandler)]
    for h in stream_handlers:
        root.removeHandler(h)
    yield
    root.removeHandler(null)
