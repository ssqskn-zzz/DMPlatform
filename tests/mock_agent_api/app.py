"""Test fixture: re-export the in-process mock lineage agent API."""

from dgov.devtools.mock_agent_api import app

__all__ = ["app"]
