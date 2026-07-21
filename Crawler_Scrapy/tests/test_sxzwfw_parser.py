from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from pathlib import Path
import unittest

from crawler_scrapy.sites.sxzwfw.parser import SxzwfwParser, visible_content_text


DOCS = Path(__file__).parents[1] / "crawler_scrapy" / "docs" / "sxzwfw"


def detail_html(title: str, body: str, extra: str = "") -> bytes:
    return f"""
    <html><body>
      <p class="cs_title_P1">{title}</p>
      <p class="cs_title_P3">发布日期：2026-07-16 12:30 信息来源：测试交易平台</p>
      <div class="cs_xq_content">{body}</div>
      {extra}
    </body></html>
    """.encode("utf-8")


class SxzwfwParserTest(unittest.TestCase):
    def test_saved_list_page_is_parsed_without_browser(self):
        page = (DOCS / "山西省公共资源交易平台列表页.html").read_bytes()
        records = SxzwfwParser.parse_list_records(page)

        self.assertEqual(len(records), 10)
        self.assertEqual(records[0]["notice_id"], "1074678")
        self.assertEqual(
            records[0]["title"],
            "泽州县南村镇农村人居环境整治项目初步设计招标公告",
        )
        self.assertEqual(records[0]["publish_time"], "2026/07/16")
        self.assertEqual(records[0]["location"], "晋城市")
        self.assertEqual(
            records[0]["detail_url"],
            "https://prec.sxzwfw.gov.cn/jyxxgczb/1074678.jhtml",
        )
        self.assertEqual(SxzwfwParser.list_total(page), 413981)

    def test_pdf_html_visual_lines_and_tender_fields(self):
        list_page = (DOCS / "山西省公共资源交易平台列表页.html").read_bytes()
        record = SxzwfwParser.parse_list_records(list_page)[0]
        page = (DOCS / "山西省公共资源交易平台.html").read_bytes()

        parsed = SxzwfwParser.parse(
            "zbgg_zys", page, record, record["detail_url"]
        )

        self.assertEqual((parsed.subtype, parsed.notice_type), ("zbgg", "招标公告"))
        self.assertEqual(
            parsed.data["项目名称"],
            "泽州县南村镇农村人居环境整治项目初步设计",
        )
        self.assertEqual(parsed.data["项目性质"], "")
        self.assertEqual(parsed.data["组织形式"], "")
        self.assertEqual(parsed.data["发布日期"], datetime(2026, 7, 16, 14, 10))
        self.assertEqual(parsed.data["开标时间"], datetime(2026, 8, 7, 9, 30))
        self.assertEqual(parsed.data["项目编号/招标编号"], "E1405000297A02989")
        self.assertEqual(parsed.data["资金来源"], "申请上级资金及南村镇政府统筹")
        self.assertEqual(parsed.data["项目地点"], "晋城市.泽州县|晋城市")
        self.assertIn("本项目为农村人居环境整治项目", parsed.data["项目规模"])
        self.assertIn("配套健身器材 66 套等", parsed.data["项目规模"])
        self.assertEqual(
            parsed.data["质量要求"],
            "符合国家及行业有关质量、安全及技术规范、规程、标准等要求，达到国家 "
            "及行业有关设计文件编制深度的规定。",
        )
        self.assertIn("网上免费下载招标文件", parsed.data["获取方式"])
        self.assertNotIn("4.2 获取方式", parsed.data["获取方式"])
        self.assertEqual(parsed.data["招标人/采购人名称"], "泽州县南村镇人民政府")
        self.assertEqual(parsed.data["招标人联系人"], "闫玉清")
        self.assertEqual(parsed.data["招标人联系方式"], "15735608110")
        self.assertEqual(parsed.data["招标代理机构"], "山西玉辉全过程咨询有限公司")
        self.assertEqual(parsed.data["招标代理机构联系人"], "徐宇")
        self.assertEqual(parsed.data["招标代理机构联系方式"], "13935695230")
        self.assertEqual(parsed.attachments, [])
        self.assertNotIn("（签名）", parsed.raw_text)
        self.assertNotIn("（盖章）", parsed.raw_text)

    def test_candidate_names_and_prices_remain_paired_when_quote_is_missing(self):
        page = detail_html(
            "咨询服务中标候选人公示",
            """
            <p>公示开始时间：2026-07-16 09:00</p>
            <p>公示结束时间：2026-07-19 09:00</p>
            <p>一、评标情况</p><p>001不分标段：</p>
            <p>第1名：甲咨询有限公司，投标报价：782000元。</p>
            <p>第2名：乙咨询有限公司</p>
            <p>第3名：丙咨询有限公司，投标报价：810000元。</p>
            <p>二、提出异议</p>
            """,
        )
        parsed = SxzwfwParser.parse(
            "hxr", page, {"notice_id": "candidate-1"},
            "https://prec.sxzwfw.gov.cn/jyxxgchxr/1.jhtml",
        )

        self.assertEqual(
            parsed.data["公示时间"],
            "2026-07-16 09:00 至 2026-07-19 09:00",
        )
        self.assertEqual(
            parsed.data["中标候选人明细"],
            [
                {"标段": "001不分标段", "候选人名称": "甲咨询有限公司", "候选人报价": Decimal("782000.00")},
                {"标段": "001不分标段", "候选人名称": "乙咨询有限公司", "候选人报价": None},
                {"标段": "001不分标段", "候选人名称": "丙咨询有限公司", "候选人报价": Decimal("810000.00")},
            ],
        )
        self.assertEqual(
            parsed.data["中标候选人报价"],
            [Decimal("782000.00"), None, Decimal("810000.00")],
        )

    def test_award_names_and_prices_remain_paired_when_price_is_missing(self):
        page = detail_html(
            "施工项目中标结果公示",
            """
            <p>一、中标人信息</p><p>001第一标段：</p>
            <p>中标人：甲建设有限公司</p><p>中标价格：1616600元</p>
            <p>002第二标段：</p><p>中标人：乙建设有限公司</p>
            <p>003第三标段：</p><p>中标人：丙建设有限公司</p><p>中标价格：1900000元</p>
            <p>二、其他公示内容</p>
            """,
        )
        parsed = SxzwfwParser.parse(
            "gs", page, {"notice_id": "award-1"},
            "https://prec.sxzwfw.gov.cn/jyxxgczbgs/1.jhtml",
        )

        self.assertEqual(
            parsed.data["中标结果明细"],
            [
                {"标段": "001第一标段", "中标人名称": "甲建设有限公司", "中标价": Decimal("1616600.00")},
                {"标段": "002第二标段", "中标人名称": "乙建设有限公司", "中标价": None},
                {"标段": "003第三标段", "中标人名称": "丙建设有限公司", "中标价": Decimal("1900000.00")},
            ],
        )
        self.assertEqual(
            parsed.data["中标价"],
            [Decimal("1616600.00"), None, Decimal("1900000.00")],
        )

    def test_direct_and_cms_attachments_are_discovered(self):
        page = detail_html(
            "附件测试招标公告",
            '<p><a href="/files/a.pdf">公告附件.pdf</a></p><a id="attach0">清单.xlsx</a>',
            '<script>Cms.attachment("", "12345", 1, "attach");</script>',
        )
        parsed = SxzwfwParser.parse(
            "zbgg_zys", page, {"notice_id": "12345"},
            "https://prec.sxzwfw.gov.cn/jyxxgczb/12345.jhtml",
        )

        self.assertEqual(len(parsed.attachments), 2)
        self.assertEqual(parsed.attachments[0]["file_url"], "https://prec.sxzwfw.gov.cn/files/a.pdf")
        self.assertEqual(parsed.attachments[1]["source_file_id"], "12345_0")
        self.assertEqual(parsed.attachments[1]["file_name"], "清单.xlsx")
        self.assertEqual(parsed.attachments[1]["parse_status"], "PENDING")
        self.assertEqual(parsed.cms_attachment["count"], 1)

    def test_hidden_nodes_do_not_pollute_visible_text(self):
        page = detail_html(
            "隐藏内容测试招标公告",
            '<p>有效正文<span style="display:none">隐藏一</span>'
            '<span style="visibility: hidden">隐藏二</span></p>',
        )
        text = visible_content_text(page)
        self.assertEqual(text, "有效正文")


if __name__ == "__main__":
    unittest.main()
