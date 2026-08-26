from __future__ import annotations

import pytest
from scrapy.settings import Settings

from crawler_scrapy.ai.glm52_profile import HYBRID_PIPELINE, LEGACY_PIPELINE
from crawler_scrapy.ai.field_contracts import AI_POLICY_DIRECT, get_field_contract
from crawler_scrapy.schemas.notice_fields import ANNOUNCEMENT_SCHEMAS
from crawler_scrapy.spiders.bitbid import BitbidSpider
from crawler_scrapy.spiders.qianji import QianjiSpider
from crawler_scrapy.spiders.sxjm import SxjmSpider
from crawler_scrapy.spiders.sxzwfw import SxzwfwSpider
from crawler_scrapy.spiders.trade365 import Trade365Spider
from crawler_scrapy.spiders.sxbid import SxbidSpider
from crawler_scrapy.spiders.sxxindian import SxxindianSpider
from crawler_scrapy.spiders.sxty_ebidding import SxtyEbiddingSpider
from crawler_scrapy.spiders.huaxin import HuaxinSpider
from crawler_scrapy.spiders.jiubang import JiubangSpider


HYBRID_SPIDERS = (
    QianjiSpider,
    SxjmSpider,
    BitbidSpider,
    Trade365Spider,
    SxzwfwSpider,
    HuaxinSpider,
    JiubangSpider,
    SxbidSpider,
    SxxindianSpider,
    SxtyEbiddingSpider,
)


@pytest.mark.parametrize(
    ("spider_class", "metadata_key"),
    (
        (SxjmSpider, "sxjmHybridAi"),
        (BitbidSpider, "bitbidHybridAi"),
        (Trade365Spider, "trade365HybridAi"),
        (SxzwfwSpider, "sxzwfwHybridAi"),
        (HuaxinSpider, "huaxinHybridAi"),
        (JiubangSpider, "jiubangHybridAi"),
    ),
)
def test_target_sites_use_glm52_c_scheme_pipeline(spider_class, metadata_key):
    settings = Settings(
        {
            "ITEM_PIPELINES": {
                LEGACY_PIPELINE: 200,
                "crawler_scrapy.pipelines.NoticeMultiFormatPipeline": 300,
            }
        }
    )

    spider_class.update_settings(settings)

    pipelines = settings.getdict("ITEM_PIPELINES")
    assert pipelines[LEGACY_PIPELINE] is None
    assert pipelines[HYBRID_PIPELINE] == 200
    assert settings.get("NOTICE_AI_API_KEY_ENV") == "ZHIPUAI_API_KEY"
    assert settings.get("NOTICE_AI_BASE_URL") == "https://open.bigmodel.cn/api/paas/v4"
    assert settings.get("NOTICE_AI_MODEL") == "glm-5.2"
    assert settings.getbool("NOTICE_AI_JSON_MODE") is True
    assert settings.getbool("NOTICE_AI_ENABLE_THINKING") is False
    assert spider_class.ai_metadata_key == metadata_key
    assert settings.getbool("NOTICE_SNAPSHOT_ENABLED") is True


def test_large_history_sites_only_escalate_anomalies_by_default():
    assert not any(SxjmSpider.ai_extract_fields.values())
    assert not any(BitbidSpider.ai_extract_fields.values())
    assert not any(SxzwfwSpider.ai_extract_fields.values())
    assert not any(Trade365Spider.ai_extract_fields.values())


@pytest.mark.parametrize(
    ("spider_class", "metadata_key"),
    (
        (SxbidSpider, "sxbidHybridAi"),
        (SxxindianSpider, "sxxindianHybridAi"),
        (SxtyEbiddingSpider, "sxtyEbiddingHybridAi"),
    ),
)
def test_qwen_target_sites_use_c_scheme_pipeline(spider_class, metadata_key):
    settings = Settings(
        {
            "ITEM_PIPELINES": {
                LEGACY_PIPELINE: 200,
                "crawler_scrapy.pipelines.NoticeMultiFormatPipeline": 300,
            }
        }
    )
    spider_class.update_settings(settings)
    pipelines = settings.getdict("ITEM_PIPELINES")
    assert pipelines[LEGACY_PIPELINE] is None
    assert pipelines[HYBRID_PIPELINE] == 200
    assert settings.get("NOTICE_AI_API_KEY_ENV") == "SILICONFLOW_API_KEY"
    assert settings.get("NOTICE_AI_MODEL") == "Qwen/Qwen3-8B"
    assert settings.getint("NOTICE_AI_MAX_CALLS") == 0
    assert settings.getfloat("NOTICE_AI_MIN_INTERVAL") == 6.0
    assert spider_class.ai_metadata_key == metadata_key


@pytest.mark.parametrize("spider_class", HYBRID_SPIDERS)
def test_hybrid_ai_fields_are_unique_and_belong_to_current_schema(spider_class):
    """Fail fast when a renamed/removed DB-mapped field silently disables AI."""

    extract_by_type = spider_class.ai_extract_fields
    candidates_by_type = spider_class.ai_candidate_fields
    assert set(extract_by_type) == set(candidates_by_type)
    for notice_type, candidates in candidates_by_type.items():
        schema = set(ANNOUNCEMENT_SCHEMAS[notice_type])
        routine = tuple(extract_by_type[notice_type])
        assert len(candidates) == len(set(candidates))
        assert len(routine) == len(set(routine))
        assert set(candidates) <= schema
        assert set(routine) <= set(candidates)
        assert not {
            field
            for field in candidates
            if get_field_contract(field).ai_policy == AI_POLICY_DIRECT
        }


@pytest.mark.parametrize(
    ("spider_class", "trusted_key"),
    (
        (HuaxinSpider, "huaxinApiTrustedFields"),
        (JiubangSpider, "jiubangApiTrustedFields"),
    ),
)
def test_tws_sites_write_dynamic_structured_api_trust_metadata(
    spider_class, trusted_key
):
    spider = spider_class(sections="zbjh")
    item = spider._build_item(
        "zbjh",
        {
            "id": "plan-1",
            "projectName": "结构化项目",
            "tenderMode": "1",
            "projectType": "03",
            "contributionScale": "120",
            "projectAddress": "太原市",
            "projectScale": "建设一栋厂房",
            "noticePlanSendTime": "2026-09-01 09:00:00",
            "releaseTime": "2026-08-20 09:00:00",
        },
        "primary",
    )

    trusted = item["field_meta"][trusted_key]
    assert "发布日期" in trusted
    assert "发布网站" in trusted
    assert "项目名称" in trusted
    assert "招标方式" in trusted
    assert "项目总投资" in trusted
    assert "建设地点" in trusted
    assert "建设内容及规模" in trusted
    assert (
        "jiubangApiTrustedFields"
        if trusted_key == "huaxinApiTrustedFields"
        else "huaxinApiTrustedFields"
    ) not in item["field_meta"]
