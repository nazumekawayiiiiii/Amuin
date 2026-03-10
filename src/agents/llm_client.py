"""LLM client — unified OpenAI-compatible API wrapper.

Supports any provider that exposes an OpenAI-compatible endpoint
(DeepSeek, Qwen, GLM, Moonshot, Claude, etc.) via base_url.
"""

import json
import logging
from typing import Any

from openai import OpenAI


log = logging.getLogger("claw")


class LLMClient:
    """Stateless LLM client using OpenAI-compatible chat completions.

    Each call is independent — no conversation history maintained.
    All "memory" lives in SQLite, not in the LLM context.

    Usage:
        client = LLMClient(
            base_url="https://api.deepseek.com/v1",
            api_key="sk-xxx",
            model="deepseek-chat",
        )
        result = client.chat(system_prompt, user_message)
    """

    def __init__(self, base_url: str, api_key: str, model: str):
        self._model = model
        self._client = OpenAI(
            base_url=base_url,
            api_key=api_key,
        )

    def chat(
        self,
        system_prompt: str,
        user_message: str,
        temperature: float = 0.3,
        max_tokens: int = 800,
    ) -> str:
        """Send a single-turn chat completion request.

        Args:
            system_prompt: System-level instructions.
            user_message: The task content.
            temperature: Sampling temperature (low = more deterministic).
            max_tokens: Maximum response tokens.

        Returns:
            The assistant's response text.

        Raises:
            Exception on API errors (caller should handle).
        """
        response = self._client.chat.completions.create(
            model=self._model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
            temperature=temperature,
            max_tokens=max_tokens,
        )
        return response.choices[0].message.content or ""

    def chat_json(
        self,
        system_prompt: str,
        user_message: str,
        temperature: float = 0.1,
        max_tokens: int = 800,
    ) -> dict[str, Any]:
        """Send a chat request and parse the response as JSON.

        Extracts JSON from the response even if wrapped in markdown
        code fences (```json ... ```).

        Returns:
            Parsed dict.

        Raises:
            ValueError if response cannot be parsed as JSON.
        """
        raw = self.chat(system_prompt, user_message, temperature, max_tokens)

        # Try direct parse
        text = raw.strip()
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        # Try extracting from markdown code fence
        if "```" in text:
            start = text.find("```")
            end = text.rfind("```")
            if start != end:
                inner = text[start:end]
                # Remove the opening ``` and optional language tag
                first_newline = inner.find("\n")
                if first_newline != -1:
                    inner = inner[first_newline + 1:]
                try:
                    return json.loads(inner.strip())
                except json.JSONDecodeError:
                    pass

        raise ValueError(f"LLM 返回内容无法解析为 JSON: {text[:200]}")
