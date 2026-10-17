"""
IBM watsonx.ai integration for The Auditor.

Manages connections to the IBM Granite foundation models to:
1. Generate semantic mutations (logic errors, off-by-one, etc.)
2. Generate a narrative analysis of the test suite quality.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from backend.config import Settings

logger = logging.getLogger("the_auditor.granite")


class GraniteService:
    """Service class for interacting with IBM Granite models."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._model = None
        
        if not self._settings.watsonx_api_key or not self._settings.watsonx_project_id:
            logger.warning("Watsonx credentials missing. AI features will be bypassed.")
            return
            
        try:
            from ibm_watsonx_ai.foundation_models import ModelInference
            from ibm_watsonx_ai.metanames import GenTextParamsMetaNames as GenParams
            
            params = {
                GenParams.MAX_NEW_TOKENS: self._settings.granite_max_new_tokens,
                GenParams.TEMPERATURE: self._settings.granite_temperature,
                GenParams.TOP_P: self._settings.granite_top_p,
                GenParams.TOP_K: self._settings.granite_top_k,
                GenParams.REPETITION_PENALTY: self._settings.granite_repetition_penalty,
            }
            
            credentials = {
                "url": self._settings.watsonx_url,
                "apikey": self._settings.watsonx_api_key
            }
            
            self._model = ModelInference(
                model_id=self._settings.granite_model_id,
                params=params,
                credentials=credentials,
                project_id=self._settings.watsonx_project_id
            )
            logger.info("GraniteService initialized with model %s", self._settings.granite_model_id)
        except ImportError:
            logger.error("ibm-watsonx-ai package not installed. AI features disabled.")
        except Exception as exc:
            logger.error("Failed to initialize Watsonx AI: %s", exc)

    def is_available(self) -> bool:
        """Check if the Watsonx client was successfully initialized."""
        return self._model is not None

    def generate_mutations(
        self, file_path: str, source_code: str, max_mutations: int
    ) -> list[dict[str, Any]]:
        """Ask Granite to propose realistic semantic mutations for a file."""
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
            response = self._model.generate_text(prompt=prompt)
            return self._parse_json_list(response)
        except Exception as exc:
            logger.error("Granite mutation generation failed: %s", exc)
            return []

    def analyse_results(
        self, overall_score: float, killed: int, total: int, file_breakdown: str, survived_sample: str
    ) -> tuple[str, list[str]]:
        """Ask Granite to analyze the audit results and recommend improvements."""
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
            response = self._model.generate_text(prompt=prompt)
            
            narrative = "Analysis completed."
            recs = []
            
            if "NARRATIVE:" in response and "RECOMMENDATIONS:" in response:
                parts = response.split("RECOMMENDATIONS:")
                narrative = parts[0].replace("NARRATIVE:", "").strip()
                try:
                    recs = json.loads(parts[1].strip())
                except json.JSONDecodeError:
                    recs = ["Improve test coverage based on survived mutants."]
            else:
                narrative = response.strip()
                
            return narrative, recs
        except Exception as exc:
            logger.error("Granite analysis failed: %s", exc)
            return "Failed to generate AI analysis.", []

    def _parse_json_list(self, text: str) -> list[dict[str, Any]]:
        """Safely extract and parse a JSON list from Granite's text output."""
        try:
            match = re.search(r'\[.*\]', text, re.DOTALL)
            if match:
                parsed = json.loads(match.group(0))
                if isinstance(parsed, list):
                    return parsed
            return []
        except Exception:
            return []