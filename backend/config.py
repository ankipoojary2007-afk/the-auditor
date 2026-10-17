"""
Configuration module for The Auditor backend.

Loads environment variables from a .env file and exposes them
as a validated Pydantic settings model. All IBM watsonx.ai
credentials, sandbox limits, and server tuning live here.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv
from pydantic import Field
from pydantic_settings import BaseSettings

# ---------------------------------------------------------------------------
# Load .env from the project root (two levels up from this file)
# ---------------------------------------------------------------------------
_ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(dotenv_path=_ENV_PATH)


class Settings(BaseSettings):
    """
    Application settings sourced from environment variables.

    Every field maps 1-to-1 to an env var of the same (uppercased) name.
    Defaults are provided for development convenience; override in .env
    for production.
    """

    # ── IBM watsonx.ai credentials ──────────────────────────────────────
    watsonx_api_key: str = Field(
        default="",
        description="IBM Cloud API key for watsonx.ai authentication.",
    )
    watsonx_url: str = Field(
        default="https://us-south.ml.cloud.ibm.com",
        description="Regional endpoint URL for watsonx.ai.",
    )
    watsonx_project_id: str = Field(
        default="",
        description="watsonx.ai project / space ID.",
    )
    granite_model_id: str = Field(
        default="ibm/granite-3-3-8b-instruct",
        description="Foundation model identifier on watsonx.ai.",
    )

    # ── Granite generation parameters ───────────────────────────────────
    granite_max_new_tokens: int = Field(default=1024, ge=1, le=8192)
    granite_temperature: float = Field(default=0.2, ge=0.0, le=2.0)
    granite_top_p: float = Field(default=0.9, ge=0.0, le=1.0)
    granite_top_k: int = Field(default=50, ge=1, le=500)
    granite_repetition_penalty: float = Field(default=1.05, ge=1.0, le=2.0)

    # ── Sandbox / secure execution limits ───────────────────────────────
    sandbox_timeout_seconds: int = Field(
        default=60,
        description="Max wall-clock time (seconds) for a single test run.",
    )
    sandbox_max_memory_mb: int = Field(
        default=512,
        description="Max RSS memory (MB) for a sandboxed subprocess.",
    )
    sandbox_mode: Literal["subprocess", "docker"] = Field(
        default="subprocess",
        description="Isolation backend: 'subprocess' (default) or 'docker'.",
    )
    sandbox_docker_image: str = Field(
        default="python:3.11-slim",
        description="Docker image for container-isolated test runs.",
    )

    # ── Mutation engine tuning ──────────────────────────────────────────
    max_mutations_per_file: int = Field(
        default=50,
        description="Cap on AST-based mutations generated per source file.",
    )
    ai_mutations_per_file: int = Field(
        default=5,
        description="Number of Granite-suggested semantic mutations per file.",
    )

    # ── Server ──────────────────────────────────────────────────────────
    host: str = Field(default="0.0.0.0")
    port: int = Field(default=8000)
    log_level: str = Field(default="info")
    cors_origins: str = Field(
        default="*",
        description="Comma-separated allowed CORS origins.",
    )

    # ── Upload limits ───────────────────────────────────────────────────
    upload_max_size_mb: int = Field(
        default=50,
        description="Maximum upload size in megabytes for project archives.",
    )

    model_config = {
        "env_file": str(_ENV_PATH),
        "env_file_encoding": "utf-8",
        "case_sensitive": False,
    }


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return a cached singleton of the application settings."""
    return Settings()
