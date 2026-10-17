"""
Utility helpers for The Auditor backend.

File-system helpers, archive extraction, code hashing, and
general-purpose functions shared across modules.
"""

from __future__ import annotations

import hashlib
import logging
import os
import shutil
import tempfile
import zipfile
from io import BytesIO
from pathlib import Path

logger = logging.getLogger("the_auditor.utils")


# ═══════════════════════════════════════════════════════════════════════════
# Temporary workspace management
# ═══════════════════════════════════════════════════════════════════════════


def create_temp_workspace(prefix: str = "auditor_") -> Path:
    """
    Create a secure temporary directory for an audit run.

    Returns the :class:`Path` to the newly created directory.
    The caller is responsible for cleanup (see :func:`cleanup_workspace`).
    """
    workspace = Path(tempfile.mkdtemp(prefix=prefix))
    logger.debug("Created temp workspace: %s", workspace)
    return workspace


def cleanup_workspace(workspace: Path) -> None:
    """Recursively remove a temporary workspace directory."""
    if workspace.exists():
        shutil.rmtree(workspace, ignore_errors=True)
        logger.debug("Cleaned up workspace: %s", workspace)


# ═══════════════════════════════════════════════════════════════════════════
# File I/O
# ═══════════════════════════════════════════════════════════════════════════


def write_files(workspace: Path, file_map: dict[str, str]) -> list[Path]:
    """
    Write a mapping of ``{relative_path: content}`` into *workspace*.

    Creates intermediate directories as needed.  Returns a list of the
    absolute :class:`Path` objects that were written.
    """
    written: list[Path] = []
    for rel_path, content in file_map.items():
        # Sanitise: prevent directory traversal
        safe_rel = Path(rel_path).as_posix().lstrip("/").lstrip("\\")
        if ".." in safe_rel:
            logger.warning("Skipping suspicious path: %s", rel_path)
            continue

        full_path = workspace / safe_rel
        full_path.parent.mkdir(parents=True, exist_ok=True)
        full_path.write_text(content, encoding="utf-8")
        written.append(full_path)
        logger.debug("Wrote %s (%d bytes)", full_path, len(content))
    return written


def extract_zip(data: bytes, dest: Path) -> list[Path]:
    """
    Extract a ZIP archive from *data* into *dest*.

    Returns a list of extracted file paths.  Skips entries whose names
    would escape the destination via path traversal.
    """
    extracted: list[Path] = []
    with zipfile.ZipFile(BytesIO(data)) as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            # Guard against zip-slip
            target = (dest / info.filename).resolve()
            if not str(target).startswith(str(dest.resolve())):
                logger.warning("Zip-slip attempt blocked: %s", info.filename)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, open(target, "wb") as dst:
                dst.write(src.read())
            extracted.append(target)
    logger.info("Extracted %d files from archive", len(extracted))
    return extracted


# ═══════════════════════════════════════════════════════════════════════════
# Code utilities
# ═══════════════════════════════════════════════════════════════════════════


def hash_code(source: str) -> str:
    """Return a short SHA-256 hex digest of *source*."""
    return hashlib.sha256(source.encode("utf-8")).hexdigest()[:16]


def collect_python_files(directory: Path) -> list[Path]:
    """
    Recursively collect all ``*.py`` files under *directory*,
    excluding common non-project directories.
    """
    exclude_dirs = {
        "__pycache__", ".git", ".venv", "venv", "node_modules",
        ".tox", ".mypy_cache", ".pytest_cache", "egg-info",
    }
    results: list[Path] = []
    for root, dirs, files in os.walk(directory):
        # Prune excluded directories in-place
        dirs[:] = [d for d in dirs if d not in exclude_dirs]
        for fname in files:
            if fname.endswith(".py"):
                results.append(Path(root) / fname)
    return sorted(results)


def safe_read(path: Path, max_size_kb: int = 512) -> str | None:
    """
    Read a file's text content, returning ``None`` if the file is
    too large or unreadable.
    """
    try:
        size = path.stat().st_size
        if size > max_size_kb * 1024:
            logger.warning("File too large, skipping: %s (%d KB)", path, size // 1024)
            return None
        return path.read_text(encoding="utf-8", errors="replace")
    except (OSError, UnicodeDecodeError) as exc:
        logger.warning("Cannot read %s: %s", path, exc)
        return None


def truncate(text: str, max_length: int = 2000) -> str:
    """Truncate *text* to *max_length* characters with an ellipsis marker."""
    if len(text) <= max_length:
        return text
    return text[: max_length - 3] + "..."