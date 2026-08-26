"""多站复用的 GLM-5.2 确定性抽取配置。"""

from __future__ import annotations

from typing import Any

from crawler_scrapy.ai.provider_profiles import (
    PROVIDER_PROFILES,
    QWEN3_8B_MODEL,
    SILICONFLOW_PROVIDER,
)
from crawler_scrapy.sites.qianji.ai_provider import API_KEY_ENV, BASE_URL, MODEL


GLM52_HYBRID_SETTINGS: dict[str, Any] = {
    "NOTICE_AI_API_KEY_ENV": API_KEY_ENV,
    "NOTICE_AI_BASE_URL": BASE_URL,
    "NOTICE_AI_MODEL": MODEL,
    "NOTICE_AI_JSON_MODE": True,
    "NOTICE_AI_ENABLE_THINKING": False,
    "NOTICE_AI_INCLUDE_OPTIONAL_FIELDS": True,
    "CONCURRENT_ITEMS": 3,
    "REACTOR_THREADPOOL_MAXSIZE": 4,
    "NOTICE_AI_MIN_INTERVAL": 2.0,
    "NOTICE_AI_TIMEOUT": 90.0,
    "NOTICE_AI_RETRY_TIMES": 0,
    "NOTICE_AI_MAX_OUTPUT_TOKENS": 1200,
}

_qwen_profile = PROVIDER_PROFILES[SILICONFLOW_PROVIDER]
QWEN3_HYBRID_SETTINGS: dict[str, Any] = {
    **_qwen_profile.scrapy_settings(QWEN3_8B_MODEL),
    "NOTICE_AI_JSON_MODE": True,
    "NOTICE_AI_INCLUDE_OPTIONAL_FIELDS": True,
    # 0 表示不限总调用次数；准确率优先，由请求间隔和 429 退避控制速度。
    "NOTICE_AI_MAX_CALLS": 0,
    "CONCURRENT_ITEMS": 3,
    "REACTOR_THREADPOOL_MAXSIZE": 4,
}

HYBRID_PIPELINE = "crawler_scrapy.ai.hybrid_pipeline.HybridAiExtractionPipeline"
LEGACY_PIPELINE = "crawler_scrapy.pipelines.AiHtmlExtractionPipeline"


def install_hybrid_pipeline(settings: Any) -> None:
    """用证据裁决型 Pipeline 替换旧的“只补空值” Pipeline。"""

    pipelines = settings.getdict("ITEM_PIPELINES")
    pipelines[LEGACY_PIPELINE] = None
    pipelines[HYBRID_PIPELINE] = 200
    settings.set("ITEM_PIPELINES", pipelines, priority="spider")


__all__ = [
    "GLM52_HYBRID_SETTINGS",
    "QWEN3_HYBRID_SETTINGS",
    "install_hybrid_pipeline",
]
