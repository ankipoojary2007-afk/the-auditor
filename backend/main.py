"""
FastAPI application for The Auditor.

Exposes REST endpoints for:
- Health checking
- Submitting audit jobs (inline code or ZIP upload)
- Polling audit status
- Retrieving audit results

The app is configured with CORS, structured logging, and
lifespan-managed resources.
"""

from __future__ import annotations

import io
import logging
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from backend.audit_engine import AuditEngine
from backend.config import get_settings
from backend.granite_service import GraniteService
from backend.models import (
    AuditConfig,
    AuditRequest,
    AuditStatus,
    AuditStatusResponse,
    AuditSummary,
    HealthResponse,
)
from backend.utils import (
    cleanup_workspace,
    collect_python_files,
    create_temp_workspace,
    extract_zip,
    safe_read,
)

# ═══════════════════════════════════════════════════════════════════════════
# Logging
# ═══════════════════════════════════════════════════════════════════════════

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("the_auditor.api")

# ═══════════════════════════════════════════════════════════════════════════
# In-memory audit store (swap for a DB in production)
# ═══════════════════════════════════════════════════════════════════════════

_audits: dict[str, AuditSummary] = {}

# ═══════════════════════════════════════════════════════════════════════════
# Application lifespan
# ═══════════════════════════════════════════════════════════════════════════


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup / shutdown hooks."""
    settings = get_settings()
    logger.info("The Auditor starting — model=%s", settings.granite_model_id)
    yield
    logger.info("The Auditor shutting down")


# ═══════════════════════════════════════════════════════════════════════════
# FastAPI app
# ═══════════════════════════════════════════════════════════════════════════

app = FastAPI(
    title="The Auditor",
    description=(
        "AI-assisted software test-quality auditing platform.  "
        "Evaluates whether a project's existing tests can detect "
        "defects via mutation testing, powered by IBM Granite."
    ),
    version="1.0.0",
    lifespan=lifespan,
)

# ── CORS ────────────────────────────────────────────────────────────────
_settings = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=_settings.cors_origins.split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ═══════════════════════════════════════════════════════════════════════════
# Endpoints
# ═══════════════════════════════════════════════════════════════════════════


@app.get("/health", response_model=HealthResponse, tags=["System"])
async def health_check():
    """Return service health and configuration status."""
    settings = get_settings()
    granite = GraniteService(settings)
    return HealthResponse(
        status="healthy",
        version="1.0.0",
        timestamp=datetime.now(timezone.utc),
        watsonx_configured=granite.is_available(),
    )


@app.post("/audit", response_model=AuditSummary, tags=["Audit"])
async def start_audit(request: AuditRequest):
    """
    Submit an audit job with inline source and test files.

    The audit runs synchronously (for simplicity in v1) and returns
    the full :class:`AuditSummary` when complete.  For large projects,
    consider the ``/audit/upload`` endpoint with async polling.
    """
    if not request.source_files:
        raise HTTPException(status_code=422, detail="No source files provided.")
    if not request.test_files:
        raise HTTPException(status_code=422, detail="No test files provided.")

    engine = AuditEngine()
    summary = await engine.run_audit(
        source_files=request.source_files,
        test_files=request.test_files,
        test_command=request.test_command,
        config=request.config,
    )

    # Store for later retrieval
    _audits[summary.audit_id] = summary

    return summary


@app.post("/audit/upload", response_model=AuditStatusResponse, tags=["Audit"])
async def upload_and_audit(
    archive: UploadFile = File(..., description="ZIP archive of the project."),
    test_command: str = "pytest --tb=short -q",
):
    """
    Upload a ZIP archive containing a Python project, then run
    an audit against it.

    The archive should contain source files and a ``tests/`` directory.
    Returns an :class:`AuditStatusResponse` with the ``audit_id`` that
    can be polled via ``GET /audit/{audit_id}``.
    """
    settings = get_settings()
    max_bytes = settings.upload_max_size_mb * 1024 * 1024

    data = await archive.read()
    if len(data) > max_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"Archive exceeds {settings.upload_max_size_mb} MB limit.",
        )

    # Extract to temp workspace
    workspace = create_temp_workspace()
    try:
        extract_zip(data, workspace)
    except Exception as exc:
        cleanup_workspace(workspace)
        raise HTTPException(status_code=400, detail=f"Invalid archive: {exc}")

    # Auto-detect source and test files
    all_py = collect_python_files(workspace)
    source_files: dict[str, str] = {}
    test_files: dict[str, str] = {}

    for p in all_py:
        rel = p.relative_to(workspace).as_posix()
        content = safe_read(p)
        if content is None:
            continue
        if "test" in rel.lower():
            test_files[rel] = content
        else:
            source_files[rel] = content

    cleanup_workspace(workspace)

    if not source_files:
        raise HTTPException(status_code=422, detail="No Python source files found.")
    if not test_files:
        raise HTTPException(status_code=422, detail="No test files found.")

    # Run audit
    engine = AuditEngine()
    summary = await engine.run_audit(
        source_files=source_files,
        test_files=test_files,
        test_command=test_command,
    )
    _audits[summary.audit_id] = summary

    return AuditStatusResponse(
        audit_id=summary.audit_id,
        status=summary.status,
        progress=100.0 if summary.status == AuditStatus.COMPLETED else 0.0,
        message=(
            "Audit completed."
            if summary.status == AuditStatus.COMPLETED
            else summary.error_message or "Audit finished with errors."
        ),
    )


@app.get("/audit/{audit_id}", response_model=AuditSummary, tags=["Audit"])
async def get_audit(audit_id: str):
    """Retrieve the full results of a previously run audit."""
    summary = _audits.get(audit_id)
    if summary is None:
        raise HTTPException(status_code=404, detail="Audit not found.")
    return summary


@app.get("/audit/{audit_id}/status", response_model=AuditStatusResponse, tags=["Audit"])
async def get_audit_status(audit_id: str):
    """Lightweight status poll for an audit job."""
    summary = _audits.get(audit_id)
    if summary is None:
        raise HTTPException(status_code=404, detail="Audit not found.")
    return AuditStatusResponse(
        audit_id=audit_id,
        status=summary.status,
        progress=100.0 if summary.status == AuditStatus.COMPLETED else 50.0,
        message=summary.error_message or "",
    )


@app.get("/audits", response_model=list[AuditStatusResponse], tags=["Audit"])
async def list_audits():
    """List all audit jobs with their status."""
    return [
        AuditStatusResponse(
            audit_id=aid,
            status=s.status,
            progress=100.0 if s.status == AuditStatus.COMPLETED else 0.0,
            message=s.error_message or "",
        )
        for aid, s in _audits.items()
    ]


# ═══════════════════════════════════════════════════════════════════════════
# Error handlers
# ═══════════════════════════════════════════════════════════════════════════


@app.exception_handler(Exception)
async def global_exception_handler(request, exc):
    """Catch-all for unhandled exceptions — never leak stack traces."""
    logger.exception("Unhandled error: %s", exc)
    return JSONResponse(
        status_code=500,
        content={"detail": "An internal error occurred. Please try again."},
    )