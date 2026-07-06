from collections.abc import Sequence

import httpx

from app.core.config import Settings


class AIConfigurationError(RuntimeError):
    pass


class AIResponseError(RuntimeError):
    pass


class AIReplyClient:
    def __init__(self, config: Settings) -> None:
        self._config = config
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(config.ai_timeout_seconds),
            follow_redirects=False,
        )

    @property
    def is_configured(self) -> bool:
        return self._config.ai_api_key is not None and bool(self._config.ai_model.strip())

    async def generate_reply(
        self,
        messages: Sequence[dict[str, str]],
        system_prompt: str,
    ) -> str:
        if not self.is_configured:
            raise AIConfigurationError("AI_API_KEY and AI_MODEL are required")

        api_key = self._config.ai_api_key
        assert api_key is not None
        endpoint = f"{self._config.ai_base_url.rstrip('/')}/chat/completions"
        response = await self._client.post(
            endpoint,
            headers={
                "Authorization": f"Bearer {api_key.get_secret_value()}",
                "Content-Type": "application/json",
            },
            json={
                "model": self._config.ai_model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    *messages,
                ],
            },
        )
        response.raise_for_status()

        try:
            content = response.json()["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise AIResponseError("AI provider returned an invalid response") from exc

        if not isinstance(content, str) or not content.strip():
            raise AIResponseError("AI provider returned an empty response")
        return content.strip()[:4096]

    async def close(self) -> None:
        await self._client.aclose()
