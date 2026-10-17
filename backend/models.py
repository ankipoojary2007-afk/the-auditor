"""
Pydantic models for The Auditor API.

Defines request bodies, response schemas, and internal data
structures used across the audit pipeline.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


# ═══════════════════════════════════════════════════════════════════════════
# Enums
# ═══════════════════════════════════════════════════════════════════════════


class AuditStatus(str, Enum):
    """Lifecycle states of an audit job."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class MutantStatus(str, Enum):
    """Outcome of a single mutant against the test suite."""

    KILLED = "killed"        # A test detected the mutation (good)
    SURVIVED = "survived"    # No test detected the mutation (gap)
    TIMEOUT = "timeout"      # Test run exceeded time limit
    ERROR = "error"          # Execution error unrelated to mutation


class MutationOrigin(str, Enum):
    """How the mutation was generated."""

    AST = "ast"              # Standard AST-based operator mutation
    AI = "ai"                # Granite-suggested semantic mutation


# ═══════════════════════════════════════════════════════════════════════════
# Request models
# ═══════════════════════════════════════════════════════════════════════════


class AuditRequest(BaseModel):
    """
    Payload to start a new audit.

    The client sends either inline code + tests *or* an upload reference
    (handled via the /upload endpoint separately).
    """

    source_files: dict[str, str] = Field(
        ...,
        description=(
            "Mapping of relative file paths to their source code contents. "
            "Example: {'src/calculator.py': 'def add(a, b): ...'}"
        ),
    )
    test_files: dict[str, str] = Field(
        ...,
        description=(
            "Mapping of relative file paths to their test code contents. "
            "Example: {'tests/test_calculator.py': 'def test_add(): ...'}"
        ),
    )
    test_command: str = Field(
        default="pytest --tb=short -q",
        description="Shell command used to run the test suite.",
    )
    config: AuditConfig | None = Field(
        default=None,
        description="Optional tuning for this specific audit run.",
    )


class AuditConfig(BaseModel):
    """Per-audit overrides for engine behaviour."""

    max_mutations_per_file: int | None = Field(
        default=None, ge=1, le=200,
        description="Override: max AST mutations per source file.",
    )
    ai_mutations_per_file: int | None = Field(
        default=None, ge=0, le=20,
        description="Override: Granite semantic mutations per source file.",
    )
    timeout_seconds: int | None = Field(
        default=None, ge=5, le=300,
        description="Override: per-mutant test timeout.",
    )
    sandbox_mode: str | None = Field(
        default=None,
        description="Override: 'subprocess' or 'docker'.",
    )


# ═══════════════════════════════════════════════════════════════════════════
# Internal / pipeline models
# ═══════════════════════════════════════════════════════════════════════════


class Mutant(BaseModel):
    """A single code mutation and its test outcome."""

    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    file_path: str
    original_code: str
    mutated_code: str
    mutation_description: str
    line_number: int | None = None
    origin: MutationOrigin = MutationOrigin.AST
    status: MutantStatus = MutantStatus.SURVIVED
    error_output: str | None = None


class FileAuditResult(BaseModel):
    """Aggregated mutation-testing results for a single source file."""

    file_path: str
    total_mutants: int = 0
    killed: int = 0
    survived: int = 0
    timeout: int = 0
    errors: int = 0
    mutation_score: float = Field(
        default=0.0,
        description="Percentage of mutants killed (0.0–100.0).",
    )
    mutants: list[Mutant] = Field(default_factory=list)


# ═══════════════════════════════════════════════════════════════════════════
# Response models
# ═══════════════════════════════════════════════════════════════════════════


class AuditSummary(BaseModel):
    """Top-level summary returned after an audit completes."""

    audit_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    status: AuditStatus = AuditStatus.PENDING
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
    )
    completed_at: datetime | None = None
    total_files: int = 0
    total_mutants: int = 0
    total_killed: int = 0
    total_survived: int = 0
    overall_mutation_score: float = 0.0
    file_results: list[FileAuditResult] = Field(default_factory=list)
    ai_analysis: str | None = Field(
        default=None,
        description="Granite-generated narrative analysis of test quality.",
    )
    recommendations: list[str] = Field(
        default_factory=list,
        description="Actionable suggestions to improve test coverage.",
    )
    error_message: str | None = None


class AuditStatusResponse(BaseModel):
    """Lightweight status check response."""

    audit_id: str
    status: AuditStatus
    progress: float = Field(
        default=0.0,
        description="Completion percentage (0.0–100.0).",
    )
    message: str = ""


class HealthResponse(BaseModel):
    """Health-check response."""

    status: str = "healthy"
    version: str = "1.0.0"
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
    )
    watsonx_configured: bool = False