"""LLM 판정(judge) 호출을 위한 단일 진입점.

qa_agent 안에서 OpenAI/Anthropic 등 LLM API를 직접 호출하는 곳은 여기 한 군데뿐이어야
합니다. 모든 평가자(evaluators.py)는 이 모듈의 `OpenAIJudgeClient.judge()`를 통해서만
LLM을 호출하며, 그래야 재시도/타임아웃/에러 처리 방식이 한곳에서 일관되게 유지됩니다.
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, Optional

import requests

DEFAULT_MODEL = "gpt-4o-mini"
DEFAULT_BASE_URL = "https://api.openai.com/v1"
ANTHROPIC_DEFAULT_MODEL = "claude-sonnet-5"
ANTHROPIC_DEFAULT_BASE_URL = "https://api.anthropic.com/v1"
ANTHROPIC_VERSION = "2023-06-01"


def _strip_json_fences(text: str) -> str:
    """Claude는 "마크다운 코드펜스 없이 답하라"고 해도 ```json ... ``` 로 감싸서 보낼 때가
    많아서, 파싱 전에 그 감싼 부분을 벗겨냅니다."""
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.strip("`")
        if stripped[:4].lower() == "json":
            stripped = stripped[4:]
    return stripped.strip()


class OpenAIJudgeClient:
    """qa_agent 안의 모든 LLM 채점 호출이 거쳐가는 단일 진입점.

    평가자들은 LLM API를 직접 두드리지 말고 반드시 이 클래스의 `judge()`를 호출해야
    합니다 - 그래야 재시도/타임아웃/에러 처리가 한곳에서 일관되게 관리됩니다.

    4가지 provider를 지원합니다. LLM 사용을 항상 선택 사항으로 두고, 특정 벤더에
    종속되지 않도록 하기 위함입니다:
    - "openai" (기본값): OPENAI_API_KEY/OPENAI_MODEL 사용, Authorization: Bearer 헤더.
    - "anthropic": ANTHROPIC_API_KEY/ANTHROPIC_MODEL 사용, Claude Messages API
      (`x-api-key` 헤더, `{base_url}/messages`, 응답 텍스트를 JSON으로 파싱).
    - "custom": 직접 개발/운영 중인 LLM 게이트웨이. OpenAI 호환
      `POST {base_url}/chat/completions` 라우트를 노출해야 하며, 인증 헤더 이름/값은
      전부 사용자가 직접 지정합니다(`key_name`/`api_key`) - 모든 커스텀 게이트웨이가
      "Authorization: Bearer" 방식을 쓰는 건 아니기 때문입니다.
    - "none": LLM 채점을 명시적으로 비활성화 - 키가 설정되어 있어도 `enabled`는 항상
      False가 되어, 실행 시 모든 LLM 기반 검사를 의도적으로 건너뛸 수 있습니다
      (비용 절감/명시적 opt-out 용도).
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        base_url: Optional[str] = None,
        timeout: int = 30,
        provider: Optional[str] = None,
        key_name: Optional[str] = None,
    ):
        self.provider = (provider or "openai").lower()
        self.timeout = timeout

        if self.provider == "anthropic":
            self.api_key = api_key or os.getenv("ANTHROPIC_API_KEY")
            self.model = model or os.getenv("ANTHROPIC_MODEL", ANTHROPIC_DEFAULT_MODEL)
            self.base_url = base_url or os.getenv("ANTHROPIC_BASE_URL", ANTHROPIC_DEFAULT_BASE_URL)
            self.key_name = "x-api-key"
        elif self.provider == "custom":
            self.api_key = api_key
            self.model = model or os.getenv("OPENAI_MODEL", DEFAULT_MODEL)
            self.base_url = base_url
            self.key_name = key_name or "Authorization"
        else:
            self.api_key = api_key or os.getenv("OPENAI_API_KEY")
            self.model = model or os.getenv("OPENAI_MODEL", DEFAULT_MODEL)
            self.base_url = base_url or os.getenv("OPENAI_BASE_URL", DEFAULT_BASE_URL)
            self.key_name = "Authorization"

    @property
    def enabled(self) -> bool:
        """provider="none"이면 무조건 False. 그 외엔 키와 엔드포인트가 둘 다 있어야 True."""
        if self.provider == "none":
            return False
        return bool(self.api_key) and bool(self.base_url)

    def judge(self, system_prompt: str, user_prompt: str) -> Dict[str, Any]:
        """채점 프롬프트를 보내고, 모델이 응답한 JSON 객체를 파싱해서 돌려줌.

        실패하면(키 없음/네트워크 오류/JSON 파싱 실패) 예외를 그대로 던집니다 - 호출하는
        쪽(평가자)에서 이를 잡아 errored 결과로 바꿔야, LLM 장애가 파이프라인 전체를
        멈추지 않습니다.
        """
        if not self.enabled:
            raise RuntimeError("LLM judge client is not configured (no provider/key/endpoint)")

        if self.provider == "anthropic":
            return self._judge_anthropic(system_prompt, user_prompt)
        return self._judge_openai_compatible(system_prompt, user_prompt)

    def _judge_openai_compatible(self, system_prompt: str, user_prompt: str) -> Dict[str, Any]:
        """openai/custom provider 공용: Chat Completions 스타일 요청."""
        if self.provider == "custom":
            headers = {self.key_name: self.api_key, "Content-Type": "application/json"}
        else:
            headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}

        response = requests.post(
            f"{self.base_url}/chat/completions",
            headers=headers,
            data=json.dumps({
                "model": self.model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "temperature": 0,
                "response_format": {"type": "json_object"},  # OpenAI가 JSON만 답하도록 강제
            }),
            timeout=self.timeout,
        )
        response.raise_for_status()
        content = response.json()["choices"][0]["message"]["content"]
        return json.loads(content)

    def _judge_anthropic(self, system_prompt: str, user_prompt: str) -> Dict[str, Any]:
        """Anthropic Messages API 전용 - 요청/응답 형식이 OpenAI와 달라서 별도 구현."""
        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": ANTHROPIC_VERSION,
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.model,
            "max_tokens": 1024,
            "temperature": 0,
            "system": system_prompt + "\n\nRespond with ONLY a single JSON object -- no markdown code fences, no extra text.",
            "messages": [{"role": "user", "content": user_prompt}],
        }
        response = requests.post(f"{self.base_url}/messages", headers=headers, data=json.dumps(payload), timeout=self.timeout)
        response.raise_for_status()
        blocks = response.json().get("content", [])
        text = "".join(block.get("text", "") for block in blocks if block.get("type") == "text")
        return json.loads(_strip_json_fences(text))
