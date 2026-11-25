"""
config.py — Central configuration for the Automated Code Review Board.
Loads environment variables and exposes a typed Config object.
"""

import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    # ── LLM provider ──────────────────────────────────────────────────────────
    OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
    OPENAI_MODEL: str = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

    # Azure OpenAI (optional override)
    AZURE_OPENAI_ENDPOINT: str = os.getenv("AZURE_OPENAI_ENDPOINT", "")
    AZURE_OPENAI_API_KEY: str = os.getenv("AZURE_OPENAI_API_KEY", "")
    AZURE_OPENAI_DEPLOYMENT: str = os.getenv("AZURE_OPENAI_DEPLOYMENT", "")
    AZURE_OPENAI_API_VERSION: str = os.getenv(
        "AZURE_OPENAI_API_VERSION", "2024-02-15-preview"
    )

    USE_AZURE: bool = bool(AZURE_OPENAI_ENDPOINT and AZURE_OPENAI_API_KEY)

    # ── Agent settings ────────────────────────────────────────────────────────
    MAX_TOKENS: int = 2048
    TEMPERATURE: float = 0.1          # low temperature for deterministic reviews

    # ── Review thresholds ─────────────────────────────────────────────────────
    # Minimum severity that blocks a merge (anything >= this → REQUEST_CHANGES)
    BLOCKING_SEVERITY: str = "HIGH"


config = Config()
