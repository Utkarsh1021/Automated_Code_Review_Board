"""
agents/base_agent.py

Abstract base class shared by all specialist agents.
Handles Semantic Kernel kernel construction so each agent subclass
only needs to implement `run(state: ChangeState)`.
"""

from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from typing import List

import semantic_kernel as sk
from semantic_kernel.connectors.ai.open_ai import (
    OpenAIChatCompletion,
    AzureChatCompletion,
)
from semantic_kernel.connectors.ai.open_ai.prompt_execution_settings.open_ai_prompt_execution_settings import (
    OpenAIChatPromptExecutionSettings,
)
from semantic_kernel.contents.chat_history import ChatHistory

from config import config
from models.finding import Finding, Severity
from models.shared_state import ChangeState


class BaseAgent(ABC):
    """
    Every specialist agent inherits from BaseAgent.

    Subclasses implement:
        name        — agent identifier string
        system_prompt — the role / persona prompt
        run(state)  — async entry point; writes findings into state
    """

    name: str = "base"
    system_prompt: str = "You are a code review agent."

    def __init__(self):
        self._kernel = self._build_kernel()

    # ── Kernel construction ───────────────────────────────────────────────────

    def _build_kernel(self) -> sk.Kernel:
        kernel = sk.Kernel()
        if config.USE_AZURE:
            service = AzureChatCompletion(
                deployment_name=config.AZURE_OPENAI_DEPLOYMENT,
                endpoint=config.AZURE_OPENAI_ENDPOINT,
                api_key=config.AZURE_OPENAI_API_KEY,
                api_version=config.AZURE_OPENAI_API_VERSION,
                service_id="chat",
            )
        else:
            service = OpenAIChatCompletion(
                ai_model_id=config.OPENAI_MODEL,
                api_key=config.OPENAI_API_KEY,
                service_id="chat",
            )
        kernel.add_service(service)
        return kernel

    # ── LLM call helper ───────────────────────────────────────────────────────

    async def _chat(self, user_message: str) -> str:
        """
        Single-turn chat with the LLM.
        Returns the assistant's raw text response.
        """
        chat_service = self._kernel.get_service("chat")

        settings = OpenAIChatPromptExecutionSettings(
            max_tokens=config.MAX_TOKENS,
            temperature=config.TEMPERATURE,
        )

        history = ChatHistory()
        history.add_system_message(self.system_prompt)
        history.add_user_message(user_message)

        response = await chat_service.get_chat_message_contents(
            chat_history=history,
            settings=settings,
            kernel=self._kernel,
        )
        return response[0].content if response else ""

    # ── JSON extraction helper ────────────────────────────────────────────────

    @staticmethod
    def _extract_json(text: str) -> str:
        """
        Extract the first JSON block from an LLM response that may contain
        markdown code fences or leading/trailing prose.
        """
        # Try ```json ... ``` block
        match = re.search(r"```(?:json)?\s*([\s\S]+?)```", text, re.IGNORECASE)
        if match:
            return match.group(1).strip()

        # Fallback: find the first '[' or '{' and extract to matching close
        for start_char, end_char in [("[", "]"), ("{", "}")]:
            idx = text.find(start_char)
            if idx != -1:
                depth = 0
                for i, ch in enumerate(text[idx:], start=idx):
                    if ch == start_char:
                        depth += 1
                    elif ch == end_char:
                        depth -= 1
                        if depth == 0:
                            return text[idx : i + 1]
        return text

    @staticmethod
    def _parse_findings(raw_json: str, agent_name: str) -> List[Finding]:
        """
        Parse a JSON array of finding dicts into Finding objects.
        Silently skips malformed entries.
        """
        findings: List[Finding] = []
        try:
            data = json.loads(raw_json)
            if isinstance(data, dict):
                # Agent may have returned {"findings": [...]}
                data = data.get("findings", [data])
            for item in data:
                try:
                    item["agent"] = agent_name
                    sev_raw = item.get("severity", "LOW")
                    item["severity"] = Severity.from_str(
                        sev_raw if isinstance(sev_raw, str) else str(sev_raw)
                    )
                    findings.append(Finding(**item))
                except Exception:
                    pass  # skip malformed finding
        except (json.JSONDecodeError, TypeError):
            pass
        return findings

    # ── Abstract interface ────────────────────────────────────────────────────

    @abstractmethod
    async def run(self, state: ChangeState) -> None:
        """
        Execute this agent's review.
        Results must be written into `state.findings` via `state.add_findings()`.
        """
        ...
