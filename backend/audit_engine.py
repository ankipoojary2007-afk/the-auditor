"""
Core audit engine for The Auditor.

Orchestrates the full mutation-testing pipeline:

1. **Baseline check** — run the original test suite to confirm it passes.
2. **AST mutation** — generate deterministic operator-level mutations
   via the Python ``ast`` module.
3. **AI mutation** — request Granite-generated semantic mutations.
4. **Execution loop** — for each mutant, inject the mutation, run the
   tests in the sandbox, and record whether the mutant was *killed*
   or *survived*.
5. **Analysis** — aggregate scores per file and overall, then (optionally)
   ask Granite for a narrative report.
"""

from __future__ import annotations

import ast
import copy
import logging
import textwrap
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from backend.config import Settings, get_settings
from backend.granite_service import GraniteService
from backend.models import (
    AuditConfig,
    AuditStatus,
    AuditSummary,
    FileAuditResult,
    Mutant,
    MutantStatus,
    MutationOrigin,
)
from backend.sandbox import SandboxResult, create_sandbox
from backend.utils import (
    cleanup_workspace,
    create_temp_workspace,
    truncate,
    write_files,
)

logger = logging.getLogger("the_auditor.engine")


# ═══════════════════════════════════════════════════════════════════════════
# AST-based mutation operators
# ═══════════════════════════════════════════════════════════════════════════

# Maps an AST operator type to a list of replacement operator types.
_BINARY_OP_SWAPS: dict[type, list[type]] = {
    ast.Add: [ast.Sub, ast.Mult],
    ast.Sub: [ast.Add],
    ast.Mult: [ast.Div, ast.Add],
    ast.Div: [ast.Mult],
    ast.FloorDiv: [ast.Div],
    ast.Mod: [ast.Mult],
    ast.Pow: [ast.Mult],
}

_COMPARE_OP_SWAPS: dict[type, list[type]] = {
    ast.Eq: [ast.NotEq],
    ast.NotEq: [ast.Eq],
    ast.Lt: [ast.LtE, ast.Gt],
    ast.LtE: [ast.Lt, ast.GtE],
    ast.Gt: [ast.GtE, ast.Lt],
    ast.GtE: [ast.Gt, ast.LtE],
    ast.Is: [ast.IsNot],
    ast.IsNot: [ast.Is],
    ast.In: [ast.NotIn],
    ast.NotIn: [ast.In],
}

_BOOL_OP_SWAPS: dict[type, list[type]] = {
    ast.And: [ast.Or],
    ast.Or: [ast.And],
}

_UNARY_OP_SWAPS: dict[type, list[type]] = {
    ast.UAdd: [ast.USub],
    ast.USub: [ast.UAdd],
    ast.Not: [],  # removing `not` is handled separately
}


# ═══════════════════════════════════════════════════════════════════════════
# AST mutation generator
# ═══════════════════════════════════════════════════════════════════════════


class ASTMutator:
    """
    Walk a Python AST and yield deterministic operator mutations.

    Each mutation is a ``dict`` with ``description``, ``line_number``,
    and ``mutated_code`` (the full file with the single mutation applied).
    """

    def __init__(self, source: str, file_path: str, max_mutations: int = 50):
        self._source = source
        self._file_path = file_path
        self._max = max_mutations

    def generate(self) -> list[dict[str, Any]]:
        """Return a list of mutation dicts."""
        try:
            tree = ast.parse(self._source)
        except SyntaxError:
            logger.warning("Cannot parse %s — skipping AST mutations", self._file_path)
            return []

        mutations: list[dict[str, Any]] = []

        for node in ast.walk(tree):
            if len(mutations) >= self._max:
                break

            # ── Binary operators (a + b → a - b) ──────────────────────
            if isinstance(node, ast.BinOp):
                swaps = _BINARY_OP_SWAPS.get(type(node.op), [])
                for new_op_cls in swaps:
                    if len(mutations) >= self._max:
                        break
                    mutated = self._apply_binop_mutation(tree, node, new_op_cls)
                    if mutated:
                        mutations.append(mutated)

            # ── Comparison operators (a < b → a <= b) ─────────────────
            elif isinstance(node, ast.Compare) and node.ops:
                for idx, op in enumerate(node.ops):
                    swaps = _COMPARE_OP_SWAPS.get(type(op), [])
                    for new_op_cls in swaps:
                        if len(mutations) >= self._max:
                            break
                        mutated = self._apply_compare_mutation(
                            tree, node, idx, new_op_cls
                        )
                        if mutated:
                            mutations.append(mutated)

            # ── Boolean operators (and → or) ──────────────────────────
            elif isinstance(node, ast.BoolOp):
                swaps = _BOOL_OP_SWAPS.get(type(node.op), [])
                for new_op_cls in swaps:
                    if len(mutations) >= self._max:
                        break
                    mutated = self._apply_boolop_mutation(tree, node, new_op_cls)
                    if mutated:
                        mutations.append(mutated)

            # ── Return value mutations (return x → return None) ───────
            elif isinstance(node, ast.Return) and node.value is not None:
                if len(mutations) < self._max:
                    mutated = self._apply_return_none_mutation(tree, node)
                    if mutated:
                        mutations.append(mutated)

            # ── Constant boundary mutations (0 → 1, True → False) ────
            elif isinstance(node, ast.Constant):
                if len(mutations) < self._max:
                    mutated = self._apply_constant_mutation(tree, node)
                    if mutated:
                        mutations.append(mutated)

        logger.info(
            "AST mutator produced %d mutations for %s",
            len(mutations),
            self._file_path,
        )
        return mutations

    # ── mutation applicators ────────────────────────────────────────────

    def _apply_binop_mutation(
        self, tree: ast.AST, node: ast.BinOp, new_op_cls: type,
    ) -> dict[str, Any] | None:
        original_op = type(node.op).__name__
        new_op_name = new_op_cls.__name__
        tree_copy = copy.deepcopy(tree)

        for n in ast.walk(tree_copy):
            if (
                isinstance(n, ast.BinOp)
                and getattr(n, "lineno", None) == node.lineno
                and getattr(n, "col_offset", None) == node.col_offset
            ):
                n.op = new_op_cls()
                break
        else:
            return None

        return self._build_mutation_dict(
            tree_copy,
            f"Replace {original_op} with {new_op_name}",
            getattr(node, "lineno", None),
        )

    def _apply_compare_mutation(
        self,
        tree: ast.AST,
        node: ast.Compare,
        op_idx: int,
        new_op_cls: type,
    ) -> dict[str, Any] | None:
        original_op = type(node.ops[op_idx]).__name__
        new_op_name = new_op_cls.__name__
        tree_copy = copy.deepcopy(tree)

        for n in ast.walk(tree_copy):
            if (
                isinstance(n, ast.Compare)
                and getattr(n, "lineno", None) == node.lineno
                and getattr(n, "col_offset", None) == node.col_offset
            ):
                n.ops[op_idx] = new_op_cls()
                break
        else:
            return None

        return self._build_mutation_dict(
            tree_copy,
            f"Replace {original_op} with {new_op_name}",
            getattr(node, "lineno", None),
        )

    def _apply_boolop_mutation(
        self, tree: ast.AST, node: ast.BoolOp, new_op_cls: type,
    ) -> dict[str, Any] | None:
        original_op = type(node.op).__name__
        new_op_name = new_op_cls.__name__
        tree_copy = copy.deepcopy(tree)

        for n in ast.walk(tree_copy):
            if (
                isinstance(n, ast.BoolOp)
                and getattr(n, "lineno", None) == node.lineno
                and getattr(n, "col_offset", None) == node.col_offset
            ):
                n.op = new_op_cls()
                break
        else:
            return None

        return self._build_mutation_dict(
            tree_copy,
            f"Replace {original_op} with {new_op_name}",
            getattr(node, "lineno", None),
        )

    def _apply_return_none_mutation(
        self, tree: ast.AST, node: ast.Return,
    ) -> dict[str, Any] | None:
        tree_copy = copy.deepcopy(tree)

        for n in ast.walk(tree_copy):
            if (
                isinstance(n, ast.Return)
                and getattr(n, "lineno", None) == node.lineno
                and n.value is not None
            ):
                n.value = ast.Constant(value=None)
                break
        else:
            return None

        return self._build_mutation_dict(
            tree_copy,
            "Replace return value with None",
            getattr(node, "lineno", None),
        )

    def _apply_constant_mutation(
        self, tree: ast.AST, node: ast.Constant,
    ) -> dict[str, Any] | None:
        val = node.value
        # Choose a replacement value
        if isinstance(val, bool):
            new_val = not val
            desc = f"Negate boolean: {val} → {new_val}"
        elif isinstance(val, int) and val == 0:
            new_val = 1
            desc = "Boundary: 0 → 1"
        elif isinstance(val, int) and val == 1:
            new_val = 0
            desc = "Boundary: 1 → 0"
        elif isinstance(val, float) and val == 0.0:
            new_val = 1.0
            desc = "Boundary: 0.0 → 1.0"
        else:
            return None

        tree_copy = copy.deepcopy(tree)
        for n in ast.walk(tree_copy):
            if (
                isinstance(n, ast.Constant)
                and getattr(n, "lineno", None) == node.lineno
                and getattr(n, "col_offset", None) == node.col_offset
                and n.value == val
            ):
                n.value = new_val
                break
        else:
            return None

        return self._build_mutation_dict(
            tree_copy, desc, getattr(node, "lineno", None)
        )

    # ── helpers ─────────────────────────────────────────────────────────

    def _build_mutation_dict(
        self, mutated_tree: ast.AST, description: str, line_number: int | None,
    ) -> dict[str, Any] | None:
        """Unparse the mutated AST back to source and wrap it in a dict."""
        try:
            mutated_code = ast.unparse(mutated_tree)
            return {
                "description": description,
                "line_number": line_number,
                "mutated_code": mutated_code,
            }
        except Exception:
            return None


# ═══════════════════════════════════════════════════════════════════════════
# The main audit engine
# ═══════════════════════════════════════════════════════════════════════════


class AuditEngine:
    """
    Orchestrate a complete mutation-testing audit.

    Usage::

        engine = AuditEngine()
        summary = await engine.run_audit(request)
    """

    def __init__(
        self,
        settings: Settings | None = None,
        granite: GraniteService | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._granite = granite or GraniteService(self._settings)

    # ── public entry point ──────────────────────────────────────────────

    async def run_audit(
        self,
        source_files: dict[str, str],
        test_files: dict[str, str],
        test_command: str = "pytest --tb=short -q",
        config: AuditConfig | None = None,
    ) -> AuditSummary:
        """
        Execute the full mutation-testing pipeline and return an
        :class:`AuditSummary`.
        """
        summary = AuditSummary()
        summary.status = AuditStatus.RUNNING

        # Resolve per-run config overrides
        max_ast = (
            config.max_mutations_per_file
            if config and config.max_mutations_per_file
            else self._settings.max_mutations_per_file
        )
        max_ai = (
            config.ai_mutations_per_file
            if config and config.ai_mutations_per_file is not None
            else self._settings.ai_mutations_per_file
        )
        timeout = (
            config.timeout_seconds
            if config and config.timeout_seconds
            else self._settings.sandbox_timeout_seconds
        )
        sandbox_mode = (
            config.sandbox_mode
            if config and config.sandbox_mode
            else self._settings.sandbox_mode
        )

        workspace = create_temp_workspace()
        try:
            # Step 0: Write files to the temp workspace
            all_files = {**source_files, **test_files}
            write_files(workspace, all_files)

            # Step 1: Baseline — the original tests must pass
            sandbox = create_sandbox(mode=sandbox_mode, settings=self._settings)
            baseline = sandbox.run_tests(workspace, test_command, timeout)

            if not baseline.tests_passed:
                summary.status = AuditStatus.FAILED
                summary.error_message = (
                    "Baseline test suite does not pass.  "
                    "Fix your tests before running an audit.\n\n"
                    f"stdout:\n{truncate(baseline.stdout, 1000)}\n"
                    f"stderr:\n{truncate(baseline.stderr, 1000)}"
                )
                return summary

            logger.info("Baseline tests passed ✓")

            # Step 2: Generate mutations & run them per source file
            file_results: list[FileAuditResult] = []

            for file_path, source_code in source_files.items():
                fr = await self._audit_file(
                    workspace=workspace,
                    file_path=file_path,
                    source_code=source_code,
                    all_files=all_files,
                    test_command=test_command,
                    sandbox=sandbox,
                    timeout=timeout,
                    max_ast=max_ast,
                    max_ai=max_ai,
                )
                file_results.append(fr)

            # Step 3: Aggregate
            summary.file_results = file_results
            summary.total_files = len(file_results)
            summary.total_mutants = sum(fr.total_mutants for fr in file_results)
            summary.total_killed = sum(fr.killed for fr in file_results)
            summary.total_survived = sum(fr.survived for fr in file_results)
            summary.overall_mutation_score = (
                (summary.total_killed / summary.total_mutants * 100)
                if summary.total_mutants > 0
                else 100.0
            )

            # Step 4: AI analysis (optional — only if Granite is configured)
            if self._granite.is_available():
                try:
                    narrative, recs = self._granite.analyse_results(
                        overall_score=summary.overall_mutation_score,
                        killed=summary.total_killed,
                        total=summary.total_mutants,
                        file_breakdown=self._format_file_breakdown(file_results),
                        survived_sample=self._format_survived_sample(file_results),
                    )
                    summary.ai_analysis = narrative
                    summary.recommendations = recs
                except Exception:
                    logger.exception("AI analysis step failed")

            summary.status = AuditStatus.COMPLETED
            summary.completed_at = datetime.now(timezone.utc)

        except Exception as exc:
            logger.exception("Audit engine error")
            summary.status = AuditStatus.FAILED
            summary.error_message = f"Internal error: {exc}"
        finally:
            cleanup_workspace(workspace)

        return summary

    # ── per-file audit ──────────────────────────────────────────────────

    async def _audit_file(
        self,
        workspace: Path,
        file_path: str,
        source_code: str,
        all_files: dict[str, str],
        test_command: str,
        sandbox,
        timeout: int,
        max_ast: int,
        max_ai: int,
    ) -> FileAuditResult:
        """Generate mutations for one file, run tests, return results."""
        result = FileAuditResult(file_path=file_path)
        mutants: list[Mutant] = []

        # ── AST mutations ─────────────────────────────────────────────
        ast_mutator = ASTMutator(source_code, file_path, max_mutations=max_ast)
        for m_dict in ast_mutator.generate():
            mutants.append(
                Mutant(
                    file_path=file_path,
                    original_code=source_code,
                    mutated_code=m_dict["mutated_code"],
                    mutation_description=m_dict["description"],
                    line_number=m_dict.get("line_number"),
                    origin=MutationOrigin.AST,
                )
            )

        # ── AI mutations (Granite) ────────────────────────────────────
        if max_ai > 0 and self._granite.is_available():
            try:
                ai_mutations = self._granite.generate_mutations(
                    file_path=file_path,
                    source_code=source_code,
                    max_mutations=max_ai,
                )
                for m_dict in ai_mutations:
                    mutants.append(
                        Mutant(
                            file_path=file_path,
                            original_code=source_code,
                            mutated_code=m_dict.get("mutated_code", ""),
                            mutation_description=m_dict.get("description", "AI mutation"),
                            line_number=m_dict.get("line_number"),
                            origin=MutationOrigin.AI,
                        )
                    )
            except Exception:
                logger.exception("AI mutation generation failed for %s", file_path)

        # ── Run each mutant against the test suite ────────────────────
        target_path = workspace / file_path
        for mutant in mutants:
            if not mutant.mutated_code.strip():
                mutant.status = MutantStatus.ERROR
                mutant.error_output = "Empty mutated code"
                continue

            # Inject the mutation
            try:
                target_path.write_text(mutant.mutated_code, encoding="utf-8")
            except OSError as exc:
                mutant.status = MutantStatus.ERROR
                mutant.error_output = str(exc)
                continue

            # Run tests
            sr: SandboxResult = sandbox.run_tests(workspace, test_command, timeout)

            if sr.timed_out:
                mutant.status = MutantStatus.TIMEOUT
            elif sr.error:
                mutant.status = MutantStatus.ERROR
                mutant.error_output = sr.error
            elif sr.tests_failed:
                mutant.status = MutantStatus.KILLED
            else:
                mutant.status = MutantStatus.SURVIVED

            # Restore the original source
            target_path.write_text(source_code, encoding="utf-8")

        # ── Aggregate per-file stats ──────────────────────────────────
        result.mutants = mutants
        result.total_mutants = len(mutants)
        result.killed = sum(1 for m in mutants if m.status == MutantStatus.KILLED)
        result.survived = sum(1 for m in mutants if m.status == MutantStatus.SURVIVED)
        result.timeout = sum(1 for m in mutants if m.status == MutantStatus.TIMEOUT)
        result.errors = sum(1 for m in mutants if m.status == MutantStatus.ERROR)
        result.mutation_score = (
            (result.killed / result.total_mutants * 100)
            if result.total_mutants > 0
            else 100.0
        )

        logger.info(
            "File %s: score=%.1f%% (%d/%d killed)",
            file_path, result.mutation_score, result.killed, result.total_mutants,
        )
        return result

    # ── formatting helpers for AI analysis ──────────────────────────────

    @staticmethod
    def _format_file_breakdown(results: list[FileAuditResult]) -> str:
        lines: list[str] = []
        for fr in results:
            lines.append(
                f"- **{fr.file_path}**: {fr.mutation_score:.1f}% "
                f"({fr.killed}/{fr.total_mutants} killed, "
                f"{fr.survived} survived)"
            )
        return "\n".join(lines) or "No files analysed."

    @staticmethod
    def _format_survived_sample(
        results: list[FileAuditResult], max_samples: int = 10,
    ) -> str:
        samples: list[str] = []
        for fr in results:
            for m in fr.mutants:
                if m.status == MutantStatus.SURVIVED and len(samples) < max_samples:
                    samples.append(
                        f"- [{m.origin.value}] {fr.file_path} L{m.line_number}: "
                        f"{m.mutation_description}"
                    )
        return "\n".join(samples) or "All mutants were killed — excellent coverage!"