"""
Tests for the FastAPI REST API endpoints.

Uses ``httpx.AsyncClient`` with ``app`` directly (no server needed).
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from backend.main import app
from backend.models import AuditStatus, AuditSummary


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest_asyncio.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


# ═══════════════════════════════════════════════════════════════════════════
# Health endpoint
# ═══════════════════════════════════════════════════════════════════════════


class TestHealthEndpoint:
    @pytest.mark.asyncio
    async def test_health_returns_200(self, client: AsyncClient):
        resp = await client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "healthy"
        assert "version" in data
        assert "timestamp" in data

    @pytest.mark.asyncio
    async def test_health_reports_gemini_status(self, client: AsyncClient):
        resp = await client.get("/health")
        data = resp.json()
        assert "gemini_configured" in data
        assert "watsonx_configured" in data


# ═══════════════════════════════════════════════════════════════════════════
# Audit endpoint
# ═══════════════════════════════════════════════════════════════════════════


class TestAuditEndpoint:
    @pytest.mark.asyncio
    async def test_audit_rejects_empty_sources(self, client: AsyncClient):
        resp = await client.post(
            "/audit",
            json={
                "source_files": {},
                "test_files": {"test.py": "def test_x(): pass"},
            },
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_audit_rejects_empty_tests(self, client: AsyncClient):
        resp = await client.post(
            "/audit",
            json={
                "source_files": {"src.py": "x = 1"},
                "test_files": {},
            },
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_audit_returns_summary(self, client: AsyncClient):
        """Submit a valid audit and verify the response structure."""
        with patch("backend.main.AuditEngine") as MockEngine:
            mock_instance = MagicMock()
            mock_summary = AuditSummary(
                status=AuditStatus.COMPLETED,
                total_files=1,
                total_mutants=5,
                total_killed=4,
                total_survived=1,
                overall_mutation_score=80.0,
            )
            mock_instance.run_audit = AsyncMock(return_value=mock_summary)
            MockEngine.return_value = mock_instance

            resp = await client.post(
                "/audit",
                json={
                    "source_files": {"calc.py": "def add(a,b): return a+b"},
                    "test_files": {
                        "test_calc.py": "from calc import add\ndef test_add(): assert add(1,2)==3"
                    },
                },
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "completed"
        assert data["overall_mutation_score"] == 80.0
        assert data["total_killed"] == 4


# ═══════════════════════════════════════════════════════════════════════════
# Audit retrieval
# ═══════════════════════════════════════════════════════════════════════════


class TestAuditRetrieval:
    @pytest.mark.asyncio
    async def test_get_nonexistent_audit_returns_404(self, client: AsyncClient):
        resp = await client.get("/audit/nonexistent-id")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_status_nonexistent_returns_404(self, client: AsyncClient):
        resp = await client.get("/audit/nonexistent-id/status")
        assert resp.status_code == 404


# ═══════════════════════════════════════════════════════════════════════════
# List audits
# ═══════════════════════════════════════════════════════════════════════════


class TestListAudits:
    @pytest.mark.asyncio
    async def test_list_audits_returns_list(self, client: AsyncClient):
        resp = await client.get("/audits")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)
