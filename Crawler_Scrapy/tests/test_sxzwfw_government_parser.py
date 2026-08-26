from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import parse_qs
import json
import unittest

from scrapy import Request
from scrapy.http import TextResponse

from crawler_scrapy.sites.sxzwfw import config
from crawler_scrapy.sites.sxzwfw.government_parser import (
    SxzwfwGovernmentProcurementParser,
)
from crawler_scrapy.sites.sxzwfw.parser import SxzwfwParser
from crawler_scrapy.sites.sxzwfw.validate_government_output import validate_output
from crawler_scrapy.spiders.sxzwfw import SxzwfwSpider


DOCS = (
    Path(__file__).parents[1]
    / "crawler_scrapy"
    / "docs"
    / "sxzwfw"
    / "政府采购"
)


def correction_html() -> bytes:
    return """
    <html><body>
      <p class="cs_title_P1">某单位物业服务项目更正公告</p>
      <p class="cs_title_P3">发布日期：2026-07-16 18:00 信息来源：山西省政府采购网</p>
      <div class="cs_xq_content">
        <p>一、项目基本情况</p>
        <p>原公告的采购项目名称：某单位物业服务项目</p>
        <p>二、更正信息</p>
        <p>更正事项：采购公告</p>
        <p>响应文件提交截止时间：2026-07-20 09:30</p>
        <p>三、其他补充事宜</p><p>无</p>
        <p>四、凡对本次公告内容提出询问，请按以下方式联系</p>
        <p>1.采购人信息</p>
        <p>名 称：某采购单位</p><p>地 址：太原市甲路1号</p>
        <p>联系方式：0351-1111111</p>
        <p>2.采购代理机构信息</p>
        <p>名 称：某代理有限公司</p><p>地 址：太原市乙路2号</p>
        <p>联系方式：0351-2222222</p>
        <p>3.项目联系方式</p><p>项目联系人：张某</p><p>电 话：0351-2222222</p>
      </div>
    </body></html>
    """.encode("utf-8")


class SxzwfwGovernmentProcurementParserTest(unittest.TestCase):
    def test_government_output_validator_checks_pairing_snapshots_and_files(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            site_dir = root / "sxzwfw"
            json_dir = site_dir / "json"
            json_dir.mkdir(parents=True)
            snapshot = site_dir / "snapshots" / "one.html"
            attachment = site_dir / "attachments" / "one.pdf"
            snapshot.parent.mkdir(parents=True)
            attachment.parent.mkdir(parents=True)
            snapshot.write_text("<html>ok</html>", encoding="utf-8")
            attachment.write_bytes(b"%PDF-test")

            common = {
                "公告ID": "1",
                "项目名称": "测试项目",
                "发布日期": "2026-07-16 12:00:00",
                "详情页链接": "https://example.test/1.jhtml",
                "HTML快照路径": "sxzwfw/snapshots/one.html",
                "解析状态": "PARSED",
                "公告正文": "测试正文",
                "附件": [],
            }
            correction = {
                **common,
                "公共类型": "更正（政府采购更正公告,channelId=19）",
                "公告内容": "更正内容",
            }
            award = {
                **common,
                "公告ID": "2",
                "源站公告性质": "成交（政府采购中标结果公告,channelId=20）",
                "中标人名称": ["甲公司"],
                "中标价": ["100.00"],
                "中标结果明细": [
                    {"标段": "1", "中标人名称": "甲公司", "中标价": "100.00"}
                ],
                "附件": [
                    {
                        "file_url": "https://example.test/one.pdf",
                        "storage_path": "sxzwfw/attachments/one.pdf",
                        "parse_status": "DOWNLOADED_NO_OCR",
                    }
                ],
            }
            (json_dir / "07_更正结果公示.json").write_text(
                json.dumps([correction], ensure_ascii=False), encoding="utf-8"
            )
            award_path = json_dir / "06_中标结果公示.json"
            award_path.write_text(
                json.dumps([award], ensure_ascii=False), encoding="utf-8"
            )

            report = validate_output(site_dir, root, expected_per_type=1)
            self.assertEqual(report["status"], "PASS")
            self.assertEqual(report["total_records"], 2)
            self.assertEqual(report["attachment_records"], 1)

            award["中标价"] = ["200.00"]
            award_path.write_text(
                json.dumps([award], ensure_ascii=False), encoding="utf-8"
            )
            report = validate_output(site_dir, root, expected_per_type=1)
            self.assertEqual(report["status"], "FAIL")
            self.assertTrue(any("可能发生错位" in value for value in report["errors"]))

    def test_only_correction_and_result_channels_are_enabled(self):
        self.assertEqual(config.SECTION_CHANNELS["zc_gz"], ("19", "政府采购更正公告"))
        self.assertEqual(config.SECTION_CHANNELS["zc_jg"], ("20", "政府采购中标结果公告"))
        self.assertNotIn("zc_cg", config.SECTION_CHANNELS)
        self.assertNotIn("zc_gz", config.DEFAULT_SECTIONS)
        self.assertNotIn("zc_jg", config.DEFAULT_SECTIONS)

        spider = SxzwfwSpider(sections="zc_gz,zc_jg", days=1)
        request = spider._list_request(
            "zc_jg",
            1,
            spider.window_start,
            spider.window_end,
        )
        form = parse_qs(request.body.decode("utf-8"), keep_blank_values=True)
        self.assertEqual(form["channelId"], ["20"])

    def test_saved_government_list_reuses_common_list_parser(self):
        page = (DOCS / "山西省公共资源交易平台.html").read_bytes()
        records = SxzwfwParser.parse_list_records(page)

        self.assertEqual(len(records), 10)
        self.assertEqual(records[0]["notice_id"], "1074777")
        self.assertEqual(
            records[0]["detail_url"],
            "https://prec.sxzwfw.gov.cn/jyxxzczb/1074777.jhtml",
        )
        self.assertEqual(SxzwfwParser.list_total(page), 502909)

    def test_real_result_page_extracts_paired_award_contacts_and_attachments(self):
        page = (DOCS / "山西省公共资源交易平台详情页.html").read_bytes()
        parsed = SxzwfwGovernmentProcurementParser.parse(
            "zc_jg",
            page,
            {
                "notice_id": "1074777",
                "title": "平遥县示范幼儿园委托业务服务项目结果公告",
                "publish_time": "2026/07/16",
                "location": "省本级",
            },
            "https://prec.sxzwfw.gov.cn/jyxxzczb/1074777.jhtml",
        )

        self.assertEqual((parsed.subtype, parsed.notice_type), ("zbjg", "中标结果公示"))
        self.assertEqual(parsed.data["项目名称"], "平遥县示范幼儿园委托业务服务项目")
        self.assertEqual(parsed.data["招标方式"], "")
        self.assertEqual(
            parsed.data["中标结果明细"],
            [
                {
                    "标段": "1",
                    "中标人名称": "山西金源物业管理有限公司",
                    "中标价": Decimal("1282790.00"),
                }
            ],
        )
        self.assertEqual(parsed.data["中标人名称"], ["山西金源物业管理有限公司"])
        self.assertEqual(parsed.data["中标价"], [Decimal("1282790.00")])
        self.assertEqual(parsed.data["工期"], "1年")
        self.assertEqual(parsed.data["招标人/采购人"], "平遥县示范幼儿园")
        self.assertEqual(parsed.data["招标人地址"], "平遥县永安北路13号")
        self.assertEqual(parsed.data["招标人联系方式"], "0354-5626699")
        self.assertEqual(parsed.data["招标代理机构"], "山西敏诚招标代理有限公司")
        self.assertEqual(parsed.data["招标代理机构联系人"], "王女士")
        self.assertEqual(parsed.data["招标代理机构联系方式"], "0354-5605550")
        self.assertEqual(parsed.data["发布日期"], datetime(2026, 7, 16, 16, 35))
        self.assertEqual(len(parsed.attachments), 2)
        self.assertTrue(all(value["file_type"] == "application/pdf" for value in parsed.attachments))

    def test_correction_forces_correction_schema_and_keeps_role_boundaries(self):
        parsed = SxzwfwGovernmentProcurementParser.parse(
            "zc_gz",
            correction_html(),
            {"notice_id": "correction-1"},
            "https://prec.sxzwfw.gov.cn/jyxxzcgz/1.jhtml",
        )

        self.assertEqual((parsed.subtype, parsed.notice_type), ("gzjg", "更正结果公示"))
        self.assertEqual(parsed.data["项目名称"], "某单位物业服务项目")
        self.assertEqual(parsed.data["公共类型"], "更正公告")
        self.assertEqual(parsed.data["开标时间"], datetime(2026, 7, 20, 9, 30))
        self.assertEqual(parsed.data["招标人地址"], "太原市甲路1号")
        self.assertEqual(parsed.data["招标人联系方式"], "0351-1111111")
        self.assertEqual(parsed.data["招标代理机构"], "某代理有限公司")
        self.assertEqual(parsed.data["招标代理机构地址"], "太原市乙路2号")
        self.assertEqual(parsed.data["招标代理机构联系人"], "张某")
        self.assertEqual(parsed.data["招标代理机构联系方式"], "0351-2222222")
        self.assertIn("响应文件提交截止时间", parsed.data["公告内容"])
        self.assertNotIn("其他补充事宜", parsed.data["公告内容"])
        self.assertNotIn("某采购单位", parsed.data["公告内容"])
        self.assertNotIn("项目联系方式", parsed.data["公告内容"])

    def test_termination_reason_does_not_swallow_contact_sections(self):
        page = """
        <html><body>
          <p class="cs_title_P1">某设备采购项目废标公告</p>
          <div class="cs_xq_content">
            <p>一、项目基本情况</p><p>采购项目名称：某设备采购项目</p>
            <p>二、项目终止的原因</p><p>通过符合性审查的供应商不足三家。</p>
            <p>三、评审小组成员名单</p><p>张某、李某、王某</p>
            <p>四、其他补充事项</p><p>无</p>
            <p>五、凡对本次公告内容提出询问，请按以下方式联系</p>
            <p>采购人：某采购单位</p><p>电话：0351-1234567</p>
          </div>
        </body></html>
        """.encode("utf-8")

        parsed = SxzwfwGovernmentProcurementParser.parse(
            "zc_gz", page, {"notice_id": "termination-1"},
            "https://prec.sxzwfw.gov.cn/jyxxzcgz/termination-1.jhtml",
        )

        self.assertEqual(parsed.data["公告内容"], "通过符合性审查的供应商不足三家。")
        self.assertNotIn("评审小组", parsed.data["公告内容"])
        self.assertNotIn("其他补充事项", parsed.data["公告内容"])
        self.assertNotIn("某采购单位", parsed.data["公告内容"])

    def test_correction_table_uses_the_last_changed_opening_time(self):
        page = correction_html().replace(
            b"<p>\xe5\x93\x8d\xe5\xba\x94\xe6\x96\x87\xe4\xbb\xb6\xe6\x8f\x90\xe4\xba\xa4\xe6\x88\xaa\xe6\xad\xa2\xe6\x97\xb6\xe9\x97\xb4\xef\xbc\x9a2026-07-20 09:30</p>",
            (
                "<p>2开标时间开标时间：2026年07月31日09：00"
                "开标时间：2026年08月18日09：00</p>"
            ).encode("utf-8"),
        )

        parsed = SxzwfwGovernmentProcurementParser.parse(
            "zc_gz", page, {"notice_id": "correction-time"},
            "https://prec.sxzwfw.gov.cn/jyxxzcgz/correction-time.jhtml",
        )

        self.assertEqual(parsed.data["开标时间"], datetime(2026, 8, 18, 9, 0))

    def test_spider_dispatches_government_result_parser(self):
        spider = SxzwfwSpider(sections="zc_jg", days=1)
        page = (DOCS / "山西省公共资源交易平台详情页.html").read_bytes()
        request = Request("https://prec.sxzwfw.gov.cn/jyxxzczb/1074777.jhtml")
        response = TextResponse(
            url=request.url,
            request=request,
            encoding="utf-8",
            body=page,
        )
        values = list(
            spider.parse_detail(
                response,
                "zc_jg",
                "1074777",
                {
                    "notice_id": "1074777",
                    "title": "平遥县示范幼儿园委托业务服务项目结果公告",
                    "publish_time": "2026/07/16",
                },
            )
        )

        self.assertEqual(len(values), 1)
        self.assertEqual(values[0]["notice_type"], "AWARD")
        self.assertEqual(values[0]["extraction_model"], "sxzwfw-zfcg-rule-parser")
        self.assertEqual(values[0]["data"]["中标人名称"], ["山西金源物业管理有限公司"])


if __name__ == "__main__":
    unittest.main()
