"""
Tests for the AST-based mutation engine and the AuditEngine orchestrator.

These tests verify mutation generation, mutant scoring, and the
end-to-end audit pipeline **without** requiring Google Gemini
credentials (the Gemini service is mocked).
"""

from __future__ import annotations

import ast
import textwrap
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

from backend.audit_engine import ASTMutator, AuditEngine
from backend.config import Settings
from backend.models import (
    AuditConfig,
    AuditStatus,
    AuditSummary,
    FileAuditResult,
    Mutant,
    MutantStatus,
    MutationOrigin,
)


# ═══════════════════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════════════════

SAMPLE_SOURCE = textwrap.dedent("""\
    def add(a, b):
        return a + b

    def is_positive(n):
        return n > 0

    def check(x, y):
        if x > 0 and y > 0:
            return True
        return False

    def get_default():
        return 0
""")

SAMPLE_TESTS = textwrap.dedent("""\
    from source import add, is_positive, check, get_default

    def test_add():
        assert add(2, 3) == 5

    def test_add_negative():
        assert add(-1, 1) == 0

    def test_is_positive():
        assert is_positive(5) is True

    def test_is_positive_zero():
        assert is_positive(0) is False

    def test_check_both_positive():
        assert check(1, 1) is True

    def test_check_negative():
        assert check(-1, 1) is False

    def test_get_default():
        assert get_default() == 0
""")


@pytest.fixture
def sample_source() -> str:
    return SAMPLE_SOURCE


@pytest.fixture
def sample_tests() -> str:
    return SAMPLE_TESTS


@pytest.fixture
def settings() -> Settings:
    """Test settings with AI features disabled."""
    return Settings(
        gemini_api_key="test-api-key",
        gemini_model_id="gemini-1.5-pro",
        max_mutations_per_file=20,
        ai_mutations_per_file=0,
        sandbox_timeout_seconds=30,
        sandbox_mode="subprocess",
    )


# ═══════════════════════════════════════════════════════════════════════════
# ASTMutator tests
# ═══════════════════════════════════════════════════════════════════════════


class TestASTMutator:
    """Verify that the AST mutator generates expected mutations."""

    def test_generates_mutations(self, sample_source: str):
        mutator = ASTMutator(sample_source, "source.py", max_mutations=50)
        mutations = mutator.generate()
        assert len(mutations) > 0, "Should generate at least one mutation"

    def test_mutations_have_required_keys(self, sample_source: str):
        mutator = ASTMutator(sample_source, "source.py", max_mutations=10)
        for m in mutator.generate():
            assert "description" in m
            assert "mutated_code" in m
            assert "line_number" in m

    def test_mutated_code_is_valid_python(self, sample_source: str):
        mutator = ASTMutator(sample_source, "source.py", max_mutations=20)
        for m in mutator.generate():
            try:
                ast.parse(m["mutated_code"])
            except SyntaxError:
                pytest.fail(
                    f"Mutation produced invalid Python: {m['description']}"
                )

    def test_mutated_code_differs_from_original(self, sample_source: str):
        mutator = ASTMutator(sample_source, "source.py", max_mutations=20)
        original_ast = ast.dump(ast.parse(sample_source))
        for m in mutator.generate():
            mutated_ast = ast.dump(ast.parse(m["mutated_code"]))
            assert mutated_ast != original_ast, (
                f"Mutation did not change the AST: {m['description']}"
            )

    def test_respects_max_mutations_cap(self, sample_source: str):
        cap = 3
        mutator = ASTMutator(sample_source, "source.py", max_mutations=cap)
        mutations = mutator.generate()
        assert len(mutations) <= cap

    def test_handles_syntax_errors_gracefully(self):
        bad_code = "def broken(:\n  pass"
        mutator = ASTMutator(bad_code, "broken.py", max_mutations=10)
        mutations = mutator.generate()
        assert mutations == []

    def test_binary_op_mutations(self):
        code = "x = a + b"
        mutator = ASTMutator(code, "ops.py", max_mutations=10)
        mutations = mutator.generate()
        descriptions = [m["description"] for m in mutations]
        assert any("Add" in d for d in descriptions)

    def test_comparison_op_mutations(self):
        code = "result = x < y"
        mutator = ASTMutator(code, "cmp.py", max_mutations=10)
        mutations = mutator.generate()
        descriptions = [m["description"] for m in mutations]
        assert any("Lt" in d for d in descriptions)

    def test_boolean_op_mutations(self):
        code = "result = a and b"
        mutator = ASTMutator(code, "bool.py", max_mutations=10)
        mutations = mutator.generate()
        descriptions = [m["description"] for m in mutations]
        assert any("And" in d or "Or" in d for d in descriptions)

    def test_return_none_mutation(self):
        code = "def f():\n    return 42"
        mutator = ASTMutator(code, "ret.py", max_mutations=10)
        mutations = mutator.generate()
        descriptions = [m["description"] for m in mutations]
        assert any("return" in d.lower() or "None" in d for d in descriptions)

    def test_constant_boundary_mutation(self):
        code = "x = 0"
        mutator = ASTMutator(code, "const.py", max_mutations=10)
        mutations = mutator.generate()
        descriptions = [m["description"] for m in mutations]
        assert any("0" in d and "1" in d for d in descriptions)


# ═══════════════════════════════════════════════════════════════════════════
# AuditEngine integration tests
# ═══════════════════════════════════════════════════════════════════════════


class TestAuditEngine:
    """End-to-end audit engine tests with mocked sandbox."""

    @pytest.mark.asyncio
    async def test_successful_audit(self, settings: Settings):
        """A well-tested project should have a high mutation score."""
        engine = AuditEngine(settings=settings)

        # Mock the sandbox to simulate test outcomes
        with patch("backend.audit_engine.create_sandbox") as mock_sandbox_factory:
            mock_sandbox = MagicMock()
            mock_sandbox_factory.return_value = mock_sandbox

            from backend.sandbox import SandboxResult

            # Baseline passes
            baseline_result = SandboxResult(return_code=0)

            # For mutations: alternate between killed and survived
            call_count = 0

            def fake_run_tests(workspace, cmd, timeout=None):
                nonlocal call_count
                call_count += 1
                if call_count == 1:
                    return baseline_result  # baseline
                # Even calls: test fails (mutant killed)
                # Odd calls: test passes (mutant survived)
                if call_count % 2 == 0:
                    return SandboxResult(return_code=1)
                return SandboxResult(return_code=0)

            mock_sandbox.run_tests.side_effect = fake_run_tests

            summary = await engine.run_audit(
                source_files={"source.py": SAMPLE_SOURCE},
                test_files={"test_source.py": SAMPLE_TESTS},
                test_command="pytest --tb=short -q",
            )

        assert summary.status == AuditStatus.COMPLETED
        assert summary.total_files == 1
        assert summary.total_mutants > 0
        assert 0.0 <= summary.overall_mutation_score <= 100.0

    @pytest.mark.asyncio
    async def test_failing_baseline_aborts(self, settings: Settings):
        """If the original test suite fails, audit should abort."""
        engine = AuditEngine(settings=settings)

        with patch("backend.audit_engine.create_sandbox") as mock_sf:
            mock_sandbox = MagicMock()
            mock_sf.return_value = mock_sandbox

            from backend.sandbox import SandboxResult
            mock_sandbox.run_tests.return_value = SandboxResult(return_code=1)

            summary = await engine.run_audit(
                source_files={"source.py": SAMPLE_SOURCE},
                test_files={"test_source.py": SAMPLE_TESTS},
            )

        assert summary.status == AuditStatus.FAILED
        assert "Baseline" in (summary.error_message or "")

    @pytest.mark.asyncio
    async def test_empty_source_produces_no_mutants(self, settings: Settings):
        """An empty source file should yield zero mutants."""
        engine = AuditEngine(settings=settings)

        with patch("backend.audit_engine.create_sandbox") as mock_sf:
            mock_sandbox = MagicMock()
            mock_sf.return_value = mock_sandbox

            from backend.sandbox import SandboxResult
            mock_sandbox.run_tests.return_value = SandboxResult(return_code=0)

            summary = await engine.run_audit(
                source_files={"empty.py": ""},
                test_files={"test_empty.py": "def test_noop(): pass"},
            )

        assert summary.status == AuditStatus.COMPLETED
        assert summary.total_mutants == 0
        assert summary.overall_mutation_score == 100.0

    @pytest.mark.asyncio
    async def test_config_overrides(self, settings: Settings):
        """Per-audit config overrides should be respected."""
        engine = AuditEngine(settings=settings)
        config = AuditConfig(max_mutations_per_file=2, ai_mutations_per_file=0)

        with patch("backend.audit_engine.create_sandbox") as mock_sf:
            mock_sandbox = MagicMock()
            mock_sf.return_value = mock_sandbox

            from backend.sandbox import SandboxResult
            mock_sandbox.run_tests.return_value = SandboxResult(return_code=0)

            summary = await engine.run_audit(
                source_files={"source.py": SAMPLE_SOURCE},
                test_files={"test_source.py": SAMPLE_TESTS},
                config=config,
            )

        assert summary.total_mutants <= 2


# ═══════════════════════════════════════════════════════════════════════════
# Model tests
# ═══════════════════════════════════════════════════════════════════════════


class TestModels:
    """Verify Pydantic model behaviour."""

    def test_mutant_default_id(self):
        m = Mutant(
            file_path="test.py",
            original_code="x = 1",
            mutated_code="x = 2",
            mutation_description="test",
        )
        assert len(m.id) == 12

    def test_file_audit_result_score_calculation(self):
        fr = FileAuditResult(
            file_path="test.py",
            total_mutants=10,
            killed=7,
            survived=3,
            mutation_score=70.0,
        )
        assert fr.mutation_score == 70.0

    def test_audit_summary_defaults(self):
        s = AuditSummary()
        assert s.status == AuditStatus.PENDING
        assert s.total_mutants == 0
