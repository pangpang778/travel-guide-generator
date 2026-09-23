#!/usr/bin/env python3
"""jev（TypeSafe System One）客户端：advisory 判断点的外部评分通道。

独立模块，只负责一件事：对宿主 AI 给定的素材/图片描述发一次评分请求并
把结果变成可降级的数据。鉴权走 env（绝不打印 key）、超时 5s、失败重试
1 次、每份攻略限次（默认 20 次，env JEV_CALL_LIMIT 可调）。任何不可用
（无 key / 失败 / 超时 / 超限）都不抛异常，返回 degraded 结果让调用方
退回启发式孪生（宿主 AI 自行评估）并显式记录降级。

决策依据：docs/adr/0001-jev-advisory-degradation-contract.md
（advisory-only、逐点 opt-in、每份限次）。
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any, Callable

API_URL = "https://api.typesafe.ai/v1/systemone"
MODEL = "jev-latest"  # 必填：缺 model 字段会被 422 拒掉
QUESTION_KEY = "credibility"
DEFAULT_TIMEOUT = 5.0
DEFAULT_RETRIES = 1  # 首次失败后重试 1 次（每次尝试都计入限次预算）
DEFAULT_CALL_LIMIT = 20
DEFAULT_THRESHOLD = 7.0

# 降级原因（写入 pipeline.jev.reason）
NO_KEY = "no_key"
LIMIT_REACHED = "limit_reached"
TIMEOUT = "timeout"
NETWORK = "network"
HTTP_5XX = "http_5xx"
HTTP_4XX = "http_4xx"
INVALID_RESPONSE = "invalid_response"

# (method, url, payload, headers, timeout) -> (status, body_text)
Transport = Callable[[str, str, bytes, dict, float], "tuple[int, str]"]


def material_point_enabled() -> bool:
    """JEV_POINT_MATERIAL=1 才开启素材可信度判断点（默认关闭 = 零调用）。"""
    return os.environ.get("JEV_POINT_MATERIAL") == "1"


def plausibility_point_enabled() -> bool:
    """JEV_POINT_PLAUSIBILITY=1 才开启行程合理性判断点（默认关闭 = 零调用）。"""
    return os.environ.get("JEV_POINT_PLAUSIBILITY") == "1"


_shared_client: "JevClient | None" = None


def shared_client() -> "JevClient":
    """进程内所有判断点共用一个客户端实例 = 共享限次预算。

    每份攻略一次进程运行；素材可信度与行程合理性两个判断点都从这里
    取同一实例，合计调用数不超过 limit。测试用 reset_shared_client() 复位。
    """
    global _shared_client
    if _shared_client is None:
        _shared_client = JevClient()
    return _shared_client


def reset_shared_client() -> None:
    """仅测试用：丢弃进程级共享客户端。"""
    global _shared_client
    _shared_client = None


def call_limit() -> int:
    try:
        return int(os.environ.get("JEV_CALL_LIMIT", DEFAULT_CALL_LIMIT))
    except ValueError:
        return DEFAULT_CALL_LIMIT


def score_threshold() -> float:
    try:
        return float(os.environ.get("JEV_SCORE_THRESHOLD", DEFAULT_THRESHOLD))
    except ValueError:
        return DEFAULT_THRESHOLD


def load_api_key() -> str:
    """从 env 或 JEV_ENV_FILE（默认 jev-demo 的 .env）读取 key，绝不输出。"""
    for name in ("TYPESAFE_API_KEY", "JEV_API_KEY"):
        value = os.environ.get(name, "").strip()
        if value:
            return value
    path = os.environ.get("JEV_ENV_FILE", r"D:\aiLocal\jev-demo\.env")
    try:
        with open(path, encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if line.startswith("TYPESAFE_API_KEY") and "=" in line:
                    value = line.split("=", 1)[1].strip().strip('"').strip("'")
                    if value:
                        return value
    except OSError:
        return ""
    return ""


def _urllib_transport(method, url, payload, headers, timeout):
    request = urllib.request.Request(url, data=payload, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read().decode("utf-8")
    except urllib.error.HTTPError as error:  # 4xx/5xx 以 HTTPError 形态出现
        return error.code, error.read().decode("utf-8", "replace")


class JevClient:
    """每份攻略一个实例；calls_used 是全部 HTTP 尝试数（含重试）。"""

    def __init__(
        self,
        api_key: str | None = None,
        limit: int | None = None,
        timeout: float | None = None,
        retries: int | None = None,
        threshold: float | None = None,
        url: str = API_URL,
        transport: Transport | None = None,
    ) -> None:
        self.api_key = load_api_key() if api_key is None else api_key
        self.limit = call_limit() if limit is None else limit
        self.timeout = DEFAULT_TIMEOUT if timeout is None else timeout
        self.retries = DEFAULT_RETRIES if retries is None else retries
        self.threshold = score_threshold() if threshold is None else threshold
        self.url = url
        self._transport = transport or _urllib_transport
        self.calls_used = 0
        self.degraded = False  # 任一次调用降级即置位（显式降级记录的依据）

    def score(
        self,
        state: str,
        instructions: str | None = None,
        criteria: list[str] | None = None,
    ) -> dict[str, Any]:
        """对一段素材描述打 0-10 分；失败路径一律返回 degraded 结果。"""
        if not self.api_key:
            return self._degrade(NO_KEY)
        if self.calls_used >= self.limit:
            return self._degrade(LIMIT_REACHED)

        question: dict[str, Any] = {"type": "score"}
        if instructions:
            question["instructions"] = instructions
        if criteria:
            question["criteria"] = criteria
        payload = json.dumps(
            {"state": state, "model": MODEL, "questions": {QUESTION_KEY: question}},
            ensure_ascii=False,
        ).encode("utf-8")
        headers = {
            "Authorization": "Bearer " + self.api_key,
            "Content-Type": "application/json",
        }

        reason = NETWORK
        for _ in range(self.retries + 1):
            if self.calls_used >= self.limit:  # 重试也吃预算；预算尽则带着真实失败原因降级
                break
            self.calls_used += 1
            try:
                status, body = self._transport(
                    "POST", self.url, payload, headers, self.timeout
                )
            except TimeoutError:
                reason = TIMEOUT
                continue
            except OSError:
                reason = NETWORK
                continue
            if 500 <= status < 600:
                reason = HTTP_5XX
                continue
            if status >= 400:  # 4xx（如 422 缺字段）不重试
                return self._degrade(HTTP_4XX)
            parsed = self._parse_score(body)
            if parsed is None:
                reason = INVALID_RESPONSE
                continue
            return {"ok": True, "score": parsed, "degraded": False, "reason": None}
        return self._degrade(reason)

    def _degrade(self, reason: str) -> dict[str, Any]:
        self.degraded = True
        return {"ok": False, "score": None, "degraded": True, "reason": reason}

    @staticmethod
    def _parse_score(body: str) -> float | None:
        """兼容 answers.{key} 为数字或 {score: n} 两种回包形态，钳到 0-10。"""
        try:
            data = json.loads(body)
        except ValueError:
            return None
        answers = data.get("answers")
        if not isinstance(answers, dict):
            result = data.get("result")
            answers = result.get("answers") if isinstance(result, dict) else None
        if not isinstance(answers, dict):
            return None
        value = answers.get(QUESTION_KEY)
        if isinstance(value, dict):
            value = value.get("score")
        try:
            return max(0.0, min(10.0, float(value)))
        except (TypeError, ValueError):
            return None
