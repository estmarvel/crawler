from __future__ import annotations

from datetime import datetime
from decimal import Decimal
import unittest

from crawler_scrapy.sites.huaxin.config import (
    build_bid_plan_list_payload,
    build_list_payload,
)
from crawler_scrapy.sites.huaxin.parser import HuaxinParser, _combine_text
from crawler_scrapy.schemas.notice_fields import (
    canonicalize_notice_data,
    create_empty_notice_data,
    get_notice_type_code,
)


class HuaxinParserTest(unittest.TestCase):
    def test_list_payload_matches_frontend_classification(self):
        payload = build_list_payload("zbgg_zys", 2, 50)
        self.assertEqual(payload["pageNum"], 2)
        self.assertEqual(payload["pageSize"], 50)
        self.assertEqual(payload["annClassifications"], ["1"])
        self.assertEqual(
            build_bid_plan_list_payload(3, 20),
            {"current": 3, "size": 20, "status": 6},
        )

    def test_tender_announcement_structured_fields_and_contacts(self):
        detail = {
            "annId": "10001",
            "annClassification": "1",
            "announcementType": "1",
            "annTitle": "某工程招标公告",
            "industryName": "建筑业",
            "diyProjectNo": "E1401005129000002378",
            "purDiyCode": "HXZB-GC20260714",
            "bidName": "测试招标人",
            "companyName": "测试代理机构",
            "submitDeadline": "2026-08-01 09:00:00",
            "acquisitionStart": "2026-07-15 09:00:00",
            "acquisitionEnd": "2026-07-20 17:00:00",
            "projectOverview": "项目概况正文",
            "bidQualification": "具备相应资质",
            "bidContactInformation": """
                <p>招标人：测试招标人</p><p>地址：招标人地址</p>
                <p>联系人：张三</p><p>电话：0351-1111111</p>
                <p>招标代理机构：测试代理机构</p><p>地址：代理地址</p>
                <p>联系人：李四</p><p>电话：0351-2222222</p>
            """,
            "releaseTime": "2026-07-14 10:00:00",
            "fileId": "file-1",
            "fileName": "招标文件.pdf",
        }

        subtype, notice_type, data, attachments = HuaxinParser.parse("zbgg_zys", detail)

        self.assertEqual(subtype, "zbgg")
        self.assertEqual(notice_type, "招标公告")
        self.assertEqual(data["项目名称"], "某工程")
        self.assertIn("E1401005129000002378", data["项目编号/招标编号"])
        self.assertEqual(data["招标内容与范围"], "项目概况正文")
        self.assertEqual(data["申请人资格要求/投标人资格要求"], "具备相应资质")
        self.assertEqual(data["招标人联系人"], "张三")
        self.assertEqual(data["招标代理机构联系人"], "李四")
        self.assertEqual(
            attachments,
            [
                {
                    "source_file_id": "file-1",
                    "file_name": "招标文件.pdf",
                    "file_url": None,
                    "storage_path": None,
                    "file_hash": None,
                    "file_size_bytes": None,
                    "file_type": "application/pdf",
                    "parse_status": "PENDING",
                }
            ],
        )
        self.assertEqual(data["开标时间"], datetime(2026, 8, 1, 9, 0))
        self.assertEqual(data["发布日期"], datetime(2026, 7, 14, 10, 0))

    def test_prequalification_detection(self):
        detail = {"annId": "2", "announcementType": "3", "annTitle": "资格预审公告"}
        subtype, notice_type, _, _ = HuaxinParser.parse("zbgg_zys", detail)
        self.assertEqual((subtype, notice_type), ("zbys", "资格预审公告"))

    def test_inline_funding_and_multiline_guarantee_are_extracted(self):
        detail = {
            "annId": "funding-1",
            "annClassification": "1",
            "annTitle": "资金与保证金规则测试招标公告",
            "annContent": """
                <p>1.招标条件</p>
                <p>项目资金来源为财政资金，招标人为测试单位。</p>
                <p>7.提交投标保证金的形式</p>
                <p>可采用电汇、银行保函或电子保函。</p>
                <p>8.提出异议的渠道和方式</p>
            """,
        }
        _, _, data, _ = HuaxinParser.parse("zbgg_zys", detail)
        self.assertEqual(data["资金来源"], "财政资金")
        self.assertEqual(
            data["投标保证金方式"], "可采用电汇、银行保函或电子保函。"
        )

    def test_project_name_and_contact_section_do_not_mix_objection_phone(self):
        detail = {
            "annId": "contact-1",
            "annClassification": "1",
            "annTitle": "四家单位城燃物资集中采购(002标段)三次重新招标公告",
            "bidName": "上级产业集团有限公司",
            "companyName": "接口代理机构",
            "administrativeName": "太原市",
            "annContent": """
                <p>招标项目所在地区：中国-山西省</p>
                <p>1.招标条件</p>
                <p>项目资金来源为自筹资金，招标人为甲公司、乙公司、丙公司、丁公司。</p>
                <p>8.提出异议的渠道和方式</p>
                <p>接收异议的联系人：张朝</p>
                <p>电话：0351-1111111</p>
                <p>11.联系方式</p>
                <p>招标人：甲公司、乙公司、丙公司、丁公司</p>
                <p>联系人：石建伟</p>
                <p>电话：0351-2981056</p>
                <p>招标代理机构：山西华新阳光科技咨询有限公司</p>
                <p>详细地址：太原市长治路345号东楼23层</p>
                <p>联系人：李晓辉、张朝、闫潇腾</p>
                <p>电话：0351-2453225、2221733、2221755</p>
                <p>0351-2221744（财务）</p>
            """,
        }
        _, _, data, _ = HuaxinParser.parse("zbgg_zys", detail)
        self.assertEqual(data["项目名称"], "四家单位城燃物资集中采购(002标段)")
        self.assertEqual(data["项目性质"], "")
        self.assertEqual(data["项目地点"], "中国-山西省|太原市")
        self.assertEqual(
            data["招标人/采购人名称"],
            "甲公司、乙公司、丙公司、丁公司|上级产业集团有限公司",
        )
        self.assertEqual(data["招标人联系人"], "石建伟")
        self.assertEqual(data["招标人联系方式"], "0351-2981056")
        self.assertEqual(
            data["招标代理机构"],
            "山西华新阳光科技咨询有限公司|接口代理机构",
        )
        self.assertEqual(data["招标代理机构地址"], "太原市长治路345号东楼23层")
        self.assertEqual(data["招标代理机构联系人"], "李晓辉、张朝、闫潇腾")
        self.assertEqual(
            data["招标代理机构联系方式"],
            "0351-2453225、2221733、2221755 0351-2221744（财务）",
        )

    def test_project_nature_is_only_set_when_source_explicitly_provides_it(self):
        _, _, empty_data, _ = HuaxinParser.parse(
            "zbgg_zys",
            {
                "annClassification": "1",
                "annTitle": "未说明性质的项目招标公告",
            },
        )
        self.assertEqual(empty_data["项目性质"], "")

        _, _, explicit_data, _ = HuaxinParser.parse(
            "zbgg_zys",
            {
                "annClassification": "1",
                "annTitle": "明确性质的项目招标公告",
                "annContent": "<p>项目性质：非依法必须招标项目</p>",
            },
        )
        self.assertEqual(explicit_data["项目性质"], "非依法必须招标项目")

    def test_candidate_name_and_price_do_not_include_service_period(self):
        detail = {
            "annId": "candidate-1",
            "annClassification": "2",
            "annTitle": "测试项目中标候选人公示",
            "reviewSituation": """
                <p>一、评标情况</p>
                <p>001第一标段：</p>
                <p>推荐中标候选人名称：测试公司</p>
                <p>投标报价：625526元</p>
                <p>服务期限：响应招标文件要求</p>
                <p>二、提出异议</p>
            """,
        }
        _, _, data, _ = HuaxinParser.parse("hxr", detail)
        self.assertEqual(data["中标候选人名称"], ["001第一标段：测试公司"])
        self.assertEqual(data["中标候选人报价"], [Decimal("625526.00")])
        self.assertEqual(
            data["中标候选人明细"],
            [
                {
                    "标段": "001第一标段",
                    "候选人名称": "测试公司",
                    "候选人报价": Decimal("625526.00"),
                }
            ],
        )

    def test_multiple_candidates_in_one_section_are_all_extracted(self):
        detail = {
            "annId": "candidate-2",
            "annClassification": "2",
            "annTitle": "测试项目中标候选人公示",
            "reviewSituation": """
                <p>一、评标情况</p><p>001第一标段：</p>
                <p>第一中标候选人名称：甲公司</p><p>投标报价：100万元</p>
                <p>第二中标候选人名称：乙公司</p><p>投标报价：120万元</p>
                <p>二、提出异议</p>
            """,
        }
        _, _, data, _ = HuaxinParser.parse("hxr", detail)
        self.assertEqual(
            data["中标候选人名称"],
            ["001第一标段：甲公司", "001第一标段：乙公司"],
        )
        self.assertEqual(
            data["中标候选人报价"],
            [Decimal("1000000.00"), Decimal("1200000.00")],
        )
        self.assertEqual(
            data["中标候选人明细"],
            [
                {
                    "标段": "001第一标段",
                    "候选人名称": "甲公司",
                    "候选人报价": Decimal("1000000.00"),
                },
                {
                    "标段": "001第一标段",
                    "候选人名称": "乙公司",
                    "候选人报价": Decimal("1200000.00"),
                },
            ],
        )

    def test_candidate_missing_price_keeps_pair_alignment(self):
        detail = {
            "annId": "candidate-missing-price",
            "annClassification": "2",
            "annTitle": "候选人报价缺失测试中标候选人公示",
            "reviewSituation": """
                <p>一、评标情况</p><p>001第一标段：</p>
                <p>第一中标候选人名称：甲公司</p>
                <p>第二中标候选人名称：乙公司</p><p>投标报价：120万元</p>
                <p>二、提出异议</p>
            """,
        }
        _, _, data, _ = HuaxinParser.parse("hxr", detail)
        self.assertEqual(
            data["中标候选人名称"],
            ["001第一标段：甲公司", "001第一标段：乙公司"],
        )
        self.assertEqual(
            data["中标候选人报价"],
            [None, Decimal("1200000.00")],
        )
        self.assertEqual(
            data["中标候选人明细"],
            [
                {
                    "标段": "001第一标段",
                    "候选人名称": "甲公司",
                    "候选人报价": None,
                },
                {
                    "标段": "001第一标段",
                    "候选人名称": "乙公司",
                    "候选人报价": Decimal("1200000.00"),
                },
            ],
        )

    def test_duplicate_ann_content_is_not_sent_twice_to_rules_or_ai(self):
        detail = {
            "annContent": "<p>项目名称：测试项目</p>",
            "annContent2": "<div>项目名称：测试项目</div>",
        }
        self.assertEqual(_combine_text(detail), "项目名称：测试项目")

    def test_result_detection_prefers_title_semantics(self):
        correction = {
            "annId": "3",
            "annClassification": "3",
            "annNature": "4",
            "annTitle": "某项目更正中标结果公示",
        }
        normal = {
            "annId": "4",
            "annClassification": "3",
            "annNature": "4",
            "annTitle": "(004标段)中标结果公示",
        }
        self.assertEqual(HuaxinParser.detect_subtype("gs", correction), "gzjg")
        self.assertEqual(HuaxinParser.detect_subtype("gs", normal), "zbjg")

    def test_result_only_keeps_configured_schema_fields(self):
        detail = {
            "annId": "5",
            "annClassification": "3",
            "annTitle": "测试项目中标结果公示",
            "diyProjectNo": "HXZB-FW20260714",
            "bidAnnDealDOS": [
                {"dealName": "测试中标人", "dealPrice": "68", "dealPriceUnit": "4"}
            ],
            "otherContent": "其他公示内容",
            "supervisionUnitName": "测试监督部门",
        }
        _, notice_type, data, _ = HuaxinParser.parse("gs", detail)
        normalized = canonicalize_notice_data(notice_type, data)
        self.assertEqual(normalized["中标人名称"], ["测试中标人"])
        # 数据库 bid_amount 无法表示百分比，类型对齐阶段保留源站原文。
        self.assertEqual(normalized["中标价"], ["68%"])
        self.assertEqual(
            normalized["中标结果明细"],
            [
                {
                    "标段": "",
                    "中标人名称": "测试中标人",
                    "中标价": "68%",
                }
            ],
        )
        self.assertNotIn("招标编号/项目编号", normalized)
        self.assertNotIn("其他公示内容", normalized)
        self.assertNotIn("监督部门", normalized)

    def test_award_missing_price_keeps_winner_alignment(self):
        detail = {
            "annId": "award-pairing",
            "annClassification": "3",
            "annTitle": "多个标段中标结果公示",
            "annContent": """
                <p>一、中标人信息</p>
                <p>001第一标段：</p>
                <p>中标人：甲公司</p>
                <p>002第二标段：</p>
                <p>中标人：乙公司</p>
                <p>中标价格：120万元</p>
                <p>二、其他公示内容</p>
            """,
            "bidAnnouncementSectionDOS": [
                {"sectionOnlyId": "s1", "sectionCode": "001"},
                {"sectionOnlyId": "s2", "sectionCode": "002"},
            ],
            "bidAnnDealDOS": [
                {
                    "sectionOnlyId": "s1",
                    "dealName": "甲公司",
                    "dealPrice": None,
                    "dealPriceUnit": 1,
                },
                {
                    "sectionOnlyId": "s2",
                    "dealName": "乙公司",
                    "dealPrice": "120",
                    "dealPriceUnit": 2,
                },
            ],
        }
        _, notice_type, data, _ = HuaxinParser.parse("gs", detail)
        normalized = canonicalize_notice_data(notice_type, data)
        self.assertEqual(normalized["中标人名称"], ["甲公司", "乙公司"])
        self.assertEqual(normalized["中标价"], [None, Decimal("1200000.00")])
        self.assertEqual(
            normalized["中标结果明细"],
            [
                {
                    "标段": "001第一标段",
                    "中标人名称": "甲公司",
                    "中标价": None,
                },
                {
                    "标段": "002第二标段",
                    "中标人名称": "乙公司",
                    "中标价": Decimal("1200000.00"),
                },
            ],
        )

    def test_bid_plan_route_uses_list_announcement_id(self):
        detail = {
            "annId": "2047000000000000000",
            "_route_planid": "14",
            "planId": "p2047",
            "projectName": "测试计划",
            "tenderMode": "1",
            "projectType": "08",
            "tenderContent": "2;3",
            "contributionScale": "2700",
        }
        subtype, notice_type, data, _ = HuaxinParser.parse("zbjh", detail)
        self.assertEqual((subtype, notice_type), ("zbjh", "招标计划"))
        self.assertEqual(data["项目类型"], "能源")
        self.assertEqual(data["项目总投资"], Decimal("27000000.00"))
        self.assertEqual(data["招标内容"], "设计、施工")
        self.assertTrue(HuaxinParser.detail_url(subtype, detail).endswith("planid=14"))

    def test_file_id_without_name_is_kept_for_metadata_resolution(self):
        detail = {
            "annId": "no-visible-attachment",
            "annClassification": "1",
            "annTitle": "无附件入口的招标公告",
            "fileId": "internal-file-id",
            "fileName": "",
        }
        _, _, data, attachments = HuaxinParser.parse("zbgg_zys", detail)
        self.assertEqual(attachments[0]["source_file_id"], "internal-file-id")
        self.assertIsNone(attachments[0]["file_name"])
        self.assertEqual(data["附件"], attachments)

    def test_pdf_file_is_only_archived_for_pdf_only_detail(self):
        html_notice = {
            "annTitle": "普通HTML公告",
            "pdfFile": "generated-pdf",
            "annContent": "<p>正文</p>",
            "bidAnnouncementSectionDOS": [
                {"fileId": "section-file", "fileName": "标段附件.pdf"}
            ],
        }
        _, _, _, html_attachments = HuaxinParser.parse("zbgg_zys", html_notice)
        self.assertEqual(html_attachments, [])

        pdf_notice = {
            "annTitle": "纯PDF公告",
            "pdfFile": "source-pdf",
            "annContent": "",
            "annContent2": "",
        }
        _, _, _, pdf_attachments = HuaxinParser.parse("zbgg_zys", pdf_notice)
        self.assertEqual(len(pdf_attachments), 1)
        self.assertEqual(pdf_attachments[0]["source_file_id"], "source-pdf")
        self.assertEqual(pdf_attachments[0]["file_name"], "纯PDF公告.pdf")
        self.assertEqual(pdf_attachments[0]["file_type"], "application/pdf")

    def test_database_compatible_empty_values_and_notice_type_code(self):
        data = create_empty_notice_data("招标公告")
        self.assertIsNone(data["开标时间"])
        self.assertIsNone(data["项目总投资/估算金额"])
        self.assertEqual(data["附件"], [])
        self.assertEqual(get_notice_type_code("招标公告"), "TENDER")
        self.assertEqual(get_notice_type_code("TENDER"), "TENDER")


if __name__ == "__main__":
    unittest.main()
