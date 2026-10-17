"""
Secure code-execution sandbox for The Auditor.

Provides two isolation backends:

1. **SubprocessSandbox** — runs tests in a child process with
   wall-clock timeout and (on Linux) memory limits via
   ``resource.setrlimit``.

2. **DockerSandbox** — runs tests inside a disposable Docker
   container with CPU, memory, and network restrictions.

Both backends implement the same :class:`SandboxResult` contract and
are selected at runtime via the ``SANDBOX_MODE`` setting.
"""

from __future__ import annotations

import logging
import os
import platform
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from backend.config import Settings, get_settings

logger = logging.getLogger("the_auditor.sandbox")


# ═══════════════════════════════════════════════════════════════════════════
# Data structures
# ═══════════════════════════════════════════════════════════════════════════


@dataclass
class SandboxResult:
    """Outcome of a sandboxed test execution."""

    return_code: int
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False
    error: str | None = None

    @property
    def tests_passed(self) -> bool:
        """Did the test suite exit cleanly (rc == 0)?"""
        return self.return_code == 0 and not self.timed_out

    @property
    def tests_failed(self) -> bool:
        """Did at least one test assertion fail (rc != 0, no timeout)?"""
        return self.return_code != 0 and not self.timed_out and self.error is None


# ═══════════════════════════════════════════════════════════════════════════
# Sandbox protocol
# ═══════════════════════════════════════════════════════════════════════════


class Sandbox(Protocol):
    """Interface that every sandbox backend must satisfy."""

    def run_tests(
        self,
        workspace: Path,
        test_command: str,
        timeout: int | None = None,
    ) -> SandboxResult: ...


# ═══════════════════════════════════════════════════════════════════════════
# Subprocess sandbox
# ═══════════════════════════════════════════════════════════════════════════


class SubprocessSandbox:
    """
    Run tests in a subprocess with timeout and optional memory cap.

    On Linux the child process has its RSS hard-limited via
    ``resource.setrlimit(RLIMIT_AS, …)``.  On other platforms the
    memory limit is advisory only (logged as a warning).
    """

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()

    # -- internal helpers ------------------------------------------------

    @staticmethod
    def _preexec_limit_memory(max_bytes: int):  # noqa: ANN205
        """Return a preexec_fn that sets RLIMIT_AS on Linux."""
        if platform.system() != "Linux":
            return None

        def _set_limits() -> None:
            import resource
            resource.setrlimit(resource.RLIMIT_AS, (max_bytes, max_bytes))

        return _set_limits

    # -- public API ------------------------------------------------------

    def run_tests(
        self,
        workspace: Path,
        test_command: str,
        timeout: int | None = None,
    ) -> SandboxResult:
        """Execute *test_command* inside *workspace* under sandbox limits."""
        timeout = timeout or self._settings.sandbox_timeout_seconds
        max_mem = self._settings.sandbox_max_memory_mb * 1024 * 1024

        env = os.environ.copy()
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        # Ensure the workspace is on sys.path for imports
        env["PYTHONPATH"] = str(workspace)

        preexec = self._preexec_limit_memory(max_mem)

        try:
            result = subprocess.run(
                test_command,
                shell=True,
                cwd=str(workspace),
                capture_output=True,
                text=True,
                timeout=timeout,
                env=env,
                preexec_fn=preexec,
            )
            return SandboxResult(
                return_code=result.returncode,
                stdout=result.stdout[-5000:],  # tail to avoid huge payloads
                stderr=result.stderr[-5000:],
            )

        except subprocess.TimeoutExpired:
            logger.warning("Test run timed out after %ds in %s", timeout, workspace)
            return SandboxResult(return_code=-1, timed_out=True)

        except Exception as exc:
            logger.exception("Sandbox execution error")
            return SandboxResult(return_code=-1, error=str(exc))


# ═══════════════════════════════════════════════════════════════════════════
# Docker sandbox
# ═══════════════════════════════════════════════════════════════════════════


class DockerSandbox:
    """
    Run tests inside a disposable Docker container.

    The workspace is bind-mounted read-only.  Network access is
    disabled.  CPU is limited to 1 core and memory to the configured
    cap.
    """

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self._image = self._settings.sandbox_docker_image

        if not shutil.which("docker"):
            raise RuntimeError(
                "Docker sandbox requested but 'docker' CLI is not on PATH."
            )

    def run_tests(
        self,
        workspace: Path,
        test_command: str,
        timeout: int | None = None,
    ) -> SandboxResult:
        """Run *test_command* in a Docker container backed by *workspace*."""
        timeout = timeout or self._settings.sandbox_timeout_seconds
        mem_limit = f"{self._settings.sandbox_max_memory_mb}m"

        docker_cmd = [
            "docker", "run",
            "--rm",
            "--network", "none",                  # no internet
            "--cpus", "1",                         # 1 CPU core
            "--memory", mem_limit,
            "--memory-swap", mem_limit,            # no swap
            "-v", f"{workspace}:/project:ro",      # read-only mount
            "-w", "/project",
            self._image,
            "bash", "-c",
            f"pip install -q pytest 2>/dev/null; {test_command}",
        ]

        try:
            result = subprocess.run(
                docker_cmd,
                capture_output=True,
                text=True,
                timeout=timeout + 30,  # grace period for container start
            )
            return SandboxResult(
                return_code=result.returncode,
                stdout=result.stdout[-5000:],
                stderr=result.stderr[-5000:],
            )

        except subprocess.TimeoutExpired:
            logger.warning("Docker test run timed out")
            return SandboxResult(return_code=-1, timed_out=True)

        except Exception as exc:
            logger.exception("Docker sandbox error")
            return SandboxResult(return_code=-1, error=str(exc))


# ═══════════════════════════════════════════════════════════════════════════
# Factory
# ═══════════════════════════════════════════════════════════════════════════


def create_sandbox(mode: str | None = None, settings: Settings | None = None) -> Sandbox:
    """
    Create and return the appropriate sandbox backend.

    Parameters
    ----------
    mode:
        ``"subprocess"`` or ``"docker"``.  Falls back to the
        ``SANDBOX_MODE`` environment setting.
    settings:
        Injected settings (useful for testing).
    """
    settings = settings or get_settings()
    mode = mode or settings.sandbox_mode

    if mode == "docker":
        return DockerSandbox(settings)
    return SubprocessSandbox(settings)