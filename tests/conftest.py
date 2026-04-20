from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from office_admin.api import app


@pytest.fixture
def client() -> TestClient:
    with TestClient(app) as test_client:
        yield test_client
