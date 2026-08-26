"""AI 提供方运行配置。

GLM-5.2 继续使用智谱开放平台；Qwen3-8B 使用硅基流动的 OpenAI
兼容接口。这里只保存环境变量名称，绝不把真实密钥写入命令或日志。
"""

from __future__ import annotations

from dataclasses import dataclass


ZHIPU_PROVIDER = "zhipu"
SILICONFLOW_PROVIDER = "siliconflow"
AUTO_PROVIDER = "auto"

GLM52_MODEL = "glm-5.2"
QWEN3_8B_MODEL = "Qwen/Qwen3-8B"


@dataclass(frozen=True)
class AiProviderProfile:
    name: str
    api_key_env: str
    base_url: str
    default_model: str
    response_format: str
    enable_thinking: bool
    retry_times: int
    retry_base_delay: float
    retry_max_delay: float
    min_interval_seconds: float | None = None
    timeout_seconds: float | None = None
    max_output_tokens: int | None = None

    def scrapy_settings(self, model: str) -> dict[str, str]:
        settings = {
            "NOTICE_AI_PROVIDER": self.name,
            "NOTICE_AI_API_KEY_ENV": self.api_key_env,
            "NOTICE_AI_BASE_URL": self.base_url,
            "NOTICE_AI_MODEL": model,
            "NOTICE_AI_JSON_MODE": "True",
            "NOTICE_AI_RESPONSE_FORMAT": self.response_format,
            "NOTICE_AI_ENABLE_THINKING": (
                "True" if self.enable_thinking else "False"
            ),
            "NOTICE_AI_RETRY_TIMES": str(self.retry_times),
            "NOTICE_AI_RETRY_BASE_DELAY": str(self.retry_base_delay),
            "NOTICE_AI_RETRY_MAX_DELAY": str(self.retry_max_delay),
        }
        if self.timeout_seconds is not None:
            settings["NOTICE_AI_TIMEOUT"] = str(self.timeout_seconds)
        if self.min_interval_seconds is not None:
            settings["NOTICE_AI_MIN_INTERVAL"] = str(
                self.min_interval_seconds
            )
        if self.max_output_tokens is not None:
            settings["NOTICE_AI_MAX_OUTPUT_TOKENS"] = str(
                self.max_output_tokens
            )
        return settings


PROVIDER_PROFILES = {
    ZHIPU_PROVIDER: AiProviderProfile(
        name=ZHIPU_PROVIDER,
        api_key_env="ZHIPUAI_API_KEY",
        base_url="https://open.bigmodel.cn/api/paas/v4",
        default_model=GLM52_MODEL,
        # 保持现有 GLM-5.2 请求格式，不改变已经验证过的调用行为。
        response_format="json_object",
        enable_thinking=False,
        retry_times=0,
        retry_base_delay=3.0,
        retry_max_delay=30.0,
    ),
    SILICONFLOW_PROVIDER: AiProviderProfile(
        name=SILICONFLOW_PROVIDER,
        api_key_env="SILICONFLOW_API_KEY",
        base_url="https://api.siliconflow.cn/v1",
        default_model=QWEN3_8B_MODEL,
        # Qwen3-8B 用字段级 JSON Schema，比仅提示其输出 JSON 更稳定。
        response_format="json_schema",
        enable_thinking=False,
        # 免费端点可能短暂限流或过载，只进行一次可统计的有限重试。
        retry_times=1,
        retry_base_delay=3.0,
        retry_max_delay=12.0,
        # 免费 Qwen 端点按分钟限流。单进程调用起始时间至少间隔 6 秒，
        # 宁可排队等待，也不通过固定总次数提前放弃后续公告。
        min_interval_seconds=6.0,
        # 50 条/类型双 Key 实采仍出现 120 秒排队超时。放宽到 180 秒，
        # 但超时仍不立即重放，避免拥塞时重复占用队列和 token。
        timeout_seconds=180.0,
        # 实测 11 字段严格 Schema 的最大输出约 1200 token；保留余量，
        # 同时避免异常响应长时间占用免费模型队列。
        max_output_tokens=2200,
    ),
}


def infer_provider(model: str) -> str:
    compact = str(model or "").strip().lower()
    if compact.startswith("qwen/") or "qwen3" in compact:
        return SILICONFLOW_PROVIDER
    return ZHIPU_PROVIDER


def resolve_provider(provider: str, model: str) -> tuple[AiProviderProfile, str]:
    selected = str(provider or AUTO_PROVIDER).strip().lower()
    if selected == AUTO_PROVIDER:
        selected = infer_provider(model)
    if selected not in PROVIDER_PROFILES:
        raise ValueError(f"不支持的 AI 提供方：{provider!r}")
    profile = PROVIDER_PROFILES[selected]
    resolved_model = str(model or "").strip() or profile.default_model
    # 显式选择硅基流动但没有覆盖旧默认值时，自动落到 Qwen3-8B；
    # 显式传入其他模型名则原样保留，便于后续 A/B 测试。
    if selected == SILICONFLOW_PROVIDER and resolved_model == GLM52_MODEL:
        resolved_model = profile.default_model
    return profile, resolved_model


__all__ = [
    "AUTO_PROVIDER",
    "GLM52_MODEL",
    "PROVIDER_PROFILES",
    "QWEN3_8B_MODEL",
    "SILICONFLOW_PROVIDER",
    "ZHIPU_PROVIDER",
    "AiProviderProfile",
    "infer_provider",
    "resolve_provider",
]
