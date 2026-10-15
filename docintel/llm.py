"""Optional LLM layer (Claude).

The pipeline is fully deterministic by default: rules, regex and validators.
If ANTHROPIC_API_KEY is set, agents use Claude as a *fallback* for the hard
cases rules can't handle — classifying an unfamiliar document, or pulling
fields from a messy layout. Rules always run first, so the LLM is never the
only source of truth for a regulated field, and every LLM call is audited.
"""
from __future__ import annotations

import json
import os
import re
from typing import Any

DEFAULT_MODEL = os.getenv("DOCINTEL_MODEL", "claude-sonnet-5-5")


class LLM:
    def __init__(self, model: str = DEFAULT_MODEL):
        self.model = model
        self._client = None
        if os.getenv("ANTHROPIC_API_KEY") and os.getenv("DOCINTEL_DISABLE_LLM") != "1":
            try:
                import anthropic

                self._client = anthropic.Anthropic()
            except Exception:  # SDK missing or misconfigured -> rules only
                self._client = None

    @property
    def available(self) -> bool:
        return self._client is not None

    def extract_json(self, instruction: str, text: str, max_chars: int = 12000) -> dict[str, Any] | None:
        if not self.available:
            return None
        prompt = (
            f"{instruction}\n\nRespond with a single JSON object and nothing else.\n\n"
            f"<document>\n{text[:max_chars]}\n</document>"
        )
        try:
            msg = self._client.messages.create(
                model=self.model,
                max_tokens=1500,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
            match = re.search(r"\{.*\}", raw, re.S)
            return json.loads(match.group(0)) if match else None
        except Exception:
            return None
