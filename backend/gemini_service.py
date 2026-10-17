"""
Google Gemini integration for The Auditor.

Manages connections to Google Gemini models via google.generativeai to:
1. Generate semantic mutations (logic errors, off-by-one, etc.)
2. Generate a narrative analysis of the test suite quality.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from backend.config import Settings

logger = logging.getLogger("the_auditor.gemini")


class GeminiService:
    """Service class for interacting with Google Gemini models."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._model = None

        if not self._settings.gemini_api_key:
            logger.warning("Gemini API key missing. AI features will be bypassed.")
            return

        try:
            import google.generativeai as genai

            genai.configure(api_key=self._settings.gemini_api_key)
            self._model = genai.GenerativeModel(self._settings.gemini_model_id)
            logger.info("GeminiService initialized with model %s", self._settings.gemini_model_id)
        except ImportError:
            logger.error("google.generativeai package not installed. AI features disabled.")
        except Exception as exc:
            logger.error("Failed to initialize Google Gemini AI: %s", exc)

    def is_available(self) -> bool:
        """Check if the Gemini client was successfully initialized."""
        return self._model is not None

    def generate_mutations(
        self, file_path: str, source_code: str, max_mutations: int
    ) -> list[dict[str, Any]]:
        """Ask Gemini to propose realistic semantic mutations for a file."""
        if not self.is_available():
            return []

        prompt = (
            f"You are an expert Python mutation testing system.\n"
            f"Given the following Python file ({file_path}), generate exactly {max_mutations} semantic mutations.\n"
            f"Semantic mutations include off-by-one errors, swapping variables, changing edge cases, or deleting crucial function calls.\n"
            f"Output strictly valid JSON as a list of dictionaries with keys: 'description', 'line_number', and 'mutated_code' (containing the full file with the mutation applied).\n\n"
            f"```python\n{source_code}\n```\n\nJSON Output:\n"
        )

        try:
            response = self._model.generate_content(prompt)
            response_text = response.text if hasattr(response, "text") else str(response)
            return self._parse_json_list(response_text)
        except Exception as exc:
            logger.error("Gemini mutation generation failed: %s", exc)
            return []

    def analyse_results(
        self, overall_score: float, killed: int, total: int, file_breakdown: str, survived_sample: str
    ) -> tuple[str, list[str]]:
        """Ask Gemini to analyze the audit results and recommend improvements."""
        if not self.is_available():
            return "AI analysis unavailable.", []

        prompt = (
            f"You are a QA automation expert. Analyze this mutation testing report:\n"
            f"- Overall Score: {overall_score:.1f}% ({killed}/{total} mutants killed)\n"
            f"- File Breakdown:\n{file_breakdown}\n"
            f"- Survived Mutants Sample:\n{survived_sample}\n\n"
            f"Provide a brief narrative analysis of the test suite quality, followed by a JSON list of 3 actionable recommendations to improve coverage.\n"
            f"Format your response EXACTLY as:\n"
            f"NARRATIVE: <your analysis paragraph>\n"
            f"RECOMMENDATIONS: [\"rec 1\", \"rec 2\", \"rec 3\"]"
        )

        try:
            response = self._model.generate_content(prompt)
            response_text = response.text if hasattr(response, "text") else str(response)

            narrative = "Analysis completed."
            recs: list[str] = []

            if "NARRATIVE:" in response_text and "RECOMMENDATIONS:" in response_text:
                parts = response_text.split("RECOMMENDATIONS:")
                narrative = parts[0].replace("NARRATIVE:", "").strip()
                try:
                    recs = json.loads(parts[1].strip())
                except json.JSONDecodeError:
                    recs = ["Improve test coverage based on survived mutants."]
            else:
                narrative = response_text.strip()

            return narrative, recs
        except Exception as exc:
            logger.error("Gemini analysis failed: %s", exc)
            return "Failed to generate AI analysis.", []

    def _parse_json_list(self, text: str) -> list[dict[str, Any]]:
        """Safely extract and parse a JSON list from Gemini's text output."""
        try:
            cleaned = re.sub(r"^```(?:json)?\s*", "", text.strip(), flags=re.MULTILINE)
            cleaned = re.sub(r"```\s*$", "", cleaned, flags=re.MULTILINE)
            match = re.search(r'\[.*\]', cleaned, re.DOTALL)
            if match:
                parsed = json.loads(match.group(0))
                if isinstance(parsed, list):
                    return parsed
            return []
        except Exception:
            return []