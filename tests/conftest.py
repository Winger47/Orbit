"""Shared pytest fixtures for the Orbit test suite."""

from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient

from orbit.main import create_app


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    """An httpx client bound to a freshly built app via create_app().

    Each test gets its own app instance so no state leaks between tests.
    """
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
