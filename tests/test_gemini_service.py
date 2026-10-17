"""
Tests for GeminiService using mocked Google Generative AI SDK.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from backend.config import Settings
from backend.gemini_service import GeminiService


@pytest.fixture
def configured_settings() -> Settings:
    return Settings(
        gemini_api_key="test-gemini-key",
        gemini_model_id="gemini-1.5-pro",
    )


@pytest.fixture
def unconfigured_settings() -> Settings:
    return Settings(
        gemini_api_key="",
        gemini_model_id="gemini-1.5-pro",
    )


class TestGeminiServiceInitialization:
    def test_unconfigured_when_api_key_empty(self, unconfigured_settings: Settings):
        service = GeminiService(unconfigured_settings)
        assert not service.is_available()

    @patch("google.generativeai.configure")
    @patch("google.generativeai.GenerativeModel")
    def test_configured_when_api_key_provided(
        self, mock_model_cls: MagicMock, mock_configure: MagicMock, configured_settings: Settings
    ):
        mock_model_instance = MagicMock()
        mock_model_cls.return_value = mock_model_instance

        service = GeminiService(configured_settings)

        assert service.is_available()
        mock_configure.assert_called_once_with(api_key="test-gemini-key")
        mock_model_cls.assert_called_once_with("gemini-1.5-pro")

    @patch("google.generativeai.configure", side_effect=Exception("Config error"))
    def test_initialization_failure_handles_gracefully(
        self, mock_configure: MagicMock, configured_settings: Settings
    ):
        service = GeminiService(configured_settings)
        assert not service.is_available()


class TestGeminiServiceMutations:
    def test_generate_mutations_returns_empty_when_unavailable(
        self, unconfigured_settings: Settings
    ):
        service = GeminiService(unconfigured_settings)
        mutations = service.generate_mutations("calc.py", "def add(a, b): return a + b", 5)
        assert mutations == []

    @patch("google.generativeai.configure")
    @patch("google.generativeai.GenerativeModel")
    def test_generate_mutations_success(
        self, mock_model_cls: MagicMock, mock_configure: MagicMock, configured_settings: Settings
    ):
        mock_model = MagicMock()
        mock_model_cls.return_value = mock_model

        expected_mutations = [
            {
                "description": "Change addition to subtraction",
                "line_number": 1,
                "mutated_code": "def add(a, b): return a - b",
            }
        ]
        mock_response = MagicMock()
        mock_response.text = f"```json\n{json.dumps(expected_mutations)}\n```"
        mock_model.generate_content.return_value = mock_response

        service = GeminiService(configured_settings)
        mutations = service.generate_mutations("calc.py", "def add(a, b): return a + b", 1)

        assert len(mutations) == 1
        assert mutations[0]["description"] == "Change addition to subtraction"
        assert mutations[0]["line_number"] == 1
        assert "return a - b" in mutations[0]["mutated_code"]

    @patch("google.generativeai.configure")
    @patch("google.generativeai.GenerativeModel")
    def test_generate_mutations_handles_api_exception(
        self, mock_model_cls: MagicMock, mock_configure: MagicMock, configured_settings: Settings
    ):
        mock_model = MagicMock()
        mock_model_cls.return_value = mock_model
        mock_model.generate_content.side_effect = RuntimeError("Quota exceeded")

        service = GeminiService(configured_settings)
        mutations = service.generate_mutations("calc.py", "def add(a, b): return a + b", 1)

        assert mutations == []


class TestGeminiServiceAnalysis:
    def test_analyse_results_returns_fallback_when_unavailable(
        self, unconfigured_settings: Settings
    ):
        service = GeminiService(unconfigured_settings)
        narrative, recs = service.analyse_results(80.0, 8, 10, "calc.py: 80%", "None")
        assert narrative == "AI analysis unavailable."
        assert recs == []

    @patch("google.generativeai.configure")
    @patch("google.generativeai.GenerativeModel")
    def test_analyse_results_parses_formatted_output(
        self, mock_model_cls: MagicMock, mock_configure: MagicMock, configured_settings: Settings
    ):
        mock_model = MagicMock()
        mock_model_cls.return_value = mock_model

        mock_response = MagicMock()
        mock_response.text = (
            "NARRATIVE: The test suite caught most bugs, but missed edge cases.\n"
            "RECOMMENDATIONS: [\"Test negative numbers\", \"Test boundary conditions\"]"
        )
        mock_model.generate_content.return_value = mock_response

        service = GeminiService(configured_settings)
        narrative, recs = service.analyse_results(80.0, 8, 10, "calc.py: 80%", "line 5 survived")

        assert "missed edge cases" in narrative
        assert len(recs) == 2
        assert recs[0] == "Test negative numbers"
        assert recs[1] == "Test boundary conditions"

    @patch("google.generativeai.configure")
    @patch("google.generativeai.GenerativeModel")
    def test_analyse_results_handles_exception(
        self, mock_model_cls: MagicMock, mock_configure: MagicMock, configured_settings: Settings
    ):
        mock_model = MagicMock()
        mock_model_cls.return_value = mock_model
        mock_model.generate_content.side_effect = Exception("API timeout")

        service = GeminiService(configured_settings)
        narrative, recs = service.analyse_results(80.0, 8, 10, "calc.py: 80%", "line 5 survived")

        assert narrative == "Failed to generate AI analysis."
        assert recs == []
