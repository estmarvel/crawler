"""跨站点复用的 C 方案混合 AI 抽取入口。

底层实现最初在千极链真实样本上完成验证。这里提供站点无关的公开名称，
使其他 Spider 复用候选窗口、字段契约、双阶段证据校验和冲突裁决逻辑，
而不复制一份容易分叉的实现。
"""

from crawler_scrapy.sites.qianji.hybrid_ai import (
    QianjiHybridAiExtractionPipeline,
    QianjiHybridAiService,
)


class HybridAiExtractionPipeline(QianjiHybridAiExtractionPipeline):
    """站点通用的混合 AI Pipeline。"""


HybridAiExtractionService = QianjiHybridAiService

__all__ = ["HybridAiExtractionPipeline", "HybridAiExtractionService"]
