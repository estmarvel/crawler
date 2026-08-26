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
            "openTime": "2026-08-01 09:00:00",
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
        self.assertEqual(data["项目地点"], "太原市")
        self.assertEqual(
            data["招标人/采购人名称"],
            "甲公司、乙公司、丙公司、丁公司",
        )
        self.assertEqual(data["招标人联系人"], "石建伟")
        self.assertEqual(data["招标人联系方式"], "0351-2981056")
        self.assertEqual(
            data["招标代理机构"],
            "山西华新阳光科技咨询有限公司",
        )
        self.assertEqual(data["招标代理机构地址"], "太原市长治路345号东楼23层")
        self.assertEqual(data["招标代理机构联系人"], "李晓辉、张朝、闫潇腾")
        self.assertEqual(
            data["招标代理机构联系方式"],
            "0351-2453225、2221733、2221755 0351-2221744（财务）",
        )

    def test_spaced_contact_labels_use_full_body_and_stop_cleanly(self):
        detail = {
            "annId": "contact-spaced-labels",
            "annClassification": "2",
            "annTitle": "咨询服务中标候选人公示",
            "bidName": "接口招标人",
            "companyName": "接口代理机构",
            # 模拟API某个结构化HTML只提供招标人；完整页面正文仍应优先。
            "bidContactInformation": """
                <p>招标人：中建三局（吕梁）市政基础设施建设运营有限公司</p>
                <p>联系人：王妥</p><p>联系电话：18829039998</p>
            """,
            "annContent": """
                <h4>五、联系方式</h4>
                <p>招 标 人：中建三局（吕梁）市政基础设施建设运营有限公司</p>
                <p>地 址：吕梁市离石区</p>
                <p>联 系 人：王妥</p>
                <p>联系电话：18829039998</p>
                <p>招标代理机构：山西中吕项目管理有限公司</p>
                <p>地 址：吕梁市离石区滨河北路</p>
                <p>联 系 人：李荣荣</p>
                <p>联系电话：0358-2811115</p>
            """,
        }

        _, _, data, _ = HuaxinParser.parse("hxr", detail)

        self.assertEqual(
            data["招标人/采购人"],
            "中建三局（吕梁）市政基础设施建设运营有限公司",
        )
        self.assertEqual(data["招标人地址"], "吕梁市离石区")
        self.assertEqual(data["招标人联系人"], "王妥")
        self.assertEqual(data["招标人联系方式"], "18829039998")
        self.assertEqual(
            data["招标代理机构"],
            "山西中吕项目管理有限公司",
        )
        self.assertEqual(data["招标代理机构地址"], "吕梁市离石区滨河北路")
        self.assertEqual(data["招标代理机构联系人"], "李荣荣")
        self.assertEqual(data["招标代理机构联系方式"], "0358-2811115")

    def test_short_agent_label_keeps_agent_contact_block(self):
        detail = {
            "annId": "contact-short-agent-label",
            "annClassification": "1",
            "annTitle": "护地坝工程招标公告",
            "annContent": """
                <p>十一、联系方式</p>
                <p>招 标 人：中阳县枝柯镇人民政府</p>
                <p>地 址：中阳县枝柯镇</p>
                <p>联 系 人：张先生</p><p>电 话：0358-5073388</p>
                <p>招标代理：山西立昇建设项目管理有限责任公司</p>
                <p>地 址：山西省太原市万柏林区千峰南路</p>
                <p>联 系 人：罗晓松、白小鹏</p><p>电 话：0351-5661255</p>
            """,
        }

        _, _, data, _ = HuaxinParser.parse("zbgg_zys", detail)

        self.assertEqual(data["招标代理机构"], "山西立昇建设项目管理有限责任公司")
        self.assertEqual(data["招标代理机构地址"], "山西省太原市万柏林区千峰南路")
        self.assertEqual(data["招标代理机构联系人"], "罗晓松、白小鹏")
        self.assertEqual(data["招标代理机构联系方式"], "0351-5661255")

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

    def test_ranked_inline_candidates_and_prices_are_paired(self):
        detail = {
            "annId": "candidate-ranked-inline",
            "annClassification": "2",
            "annTitle": "咨询服务重新招标中标候选人公示",
            "reviewSituation": """
                <p>一、评标情况</p>
                <p>001不分标段</p>
                <p>第1名：北京君成工程管理咨询有限公司,投标报价：782000元。</p>
                <p>第2名：北京大岳咨询有限责任公司,投标报价：795000.00元。</p>
                <p>二、提出异议的渠道和方式</p>
            """,
        }

        _, _, data, _ = HuaxinParser.parse("hxr", detail)

        self.assertEqual(
            data["中标候选人名称"],
            [
                "001不分标段：北京君成工程管理咨询有限公司",
                "001不分标段：北京大岳咨询有限责任公司",
            ],
        )
        self.assertEqual(
            data["中标候选人报价"],
            [Decimal("782000.00"), Decimal("795000.00")],
        )
        self.assertEqual(
            data["中标候选人明细"],
            [
                {
                    "标段": "001不分标段",
                    "候选人名称": "北京君成工程管理咨询有限公司",
                    "候选人报价": Decimal("782000.00"),
                },
                {
                    "标段": "001不分标段",
                    "候选人名称": "北京大岳咨询有限责任公司",
                    "候选人报价": Decimal("795000.00"),
                },
            ],
        )

    def test_ranked_inline_price_with_parenthetical_qualifier_is_not_part_of_name(self):
        detail = {
            "annId": "candidate-qualified-price-label",
            "annClassification": "2",
            "annTitle": "燃气报警装置供应商入围框架协议中标候选人公示",
            "reviewSituation": """
                <p>一、评标情况</p>
                <p>001第一标段</p>
                <p>第1名：成都鑫豪斯电子探测技术有限公司,投标报价（各分项单价合计值）：211元,项目负责人：杨帆,该投标人的资格能力条件满足招标文件相关要求。</p>
                <p>第2名：金卡智能集团股份有限公司,投标报价(各分项单价合计值)：175元,项目负责人：张思赐。</p>
                <p>二、提出异议的渠道和方式</p>
            """,
        }

        _, _, data, _ = HuaxinParser.parse("hxr", detail)

        self.assertEqual(
            data["中标候选人名称"],
            [
                "001第一标段：成都鑫豪斯电子探测技术有限公司",
                "001第一标段：金卡智能集团股份有限公司",
            ],
        )
        self.assertEqual(
            data["中标候选人报价"],
            [Decimal("211.00"), Decimal("175.00")],
        )
        self.assertEqual(
            [item["候选人报价"] for item in data["中标候选人明细"]],
            data["中标候选人报价"],
        )

    def test_vertical_candidate_table_is_paired_with_header_unit(self):
        detail = {
            "annId": "candidate-vertical-table",
            "annClassification": "2",
            "annTitle": "药剂采购中标候选人公示",
            "reviewSituation": """
                <p>一、评标情况</p>
                <p>001第二标段</p>
                <p>排序</p>
                <p>中标候选人名称</p>
                <p>投标报价（万元）</p>
                <p>交货期</p>
                <p>1</p><p>山西刻选环境科技有限公司</p><p>3595.02</p><p>30天</p>
                <p>2</p><p>大同市华瑞化玻仪器有限责任公司</p><p>2490.16</p><p>25天</p>
                <p>3</p><p>山西太工环保设备有限公司</p><p>3634.32</p><p>30天</p>
                <p>2、中标候选人响应招标文件要求的资格能力条件</p>
                <p>序号</p><p>中标候选人名称</p><p>响应情况</p>
                <p>1</p><p>山西刻选环境科技有限公司</p><p>响应</p>
                <p>2</p><p>大同市华瑞化玻仪器有限责任公司</p><p>响应</p>
                <p>3</p><p>山西太工环保设备有限公司</p><p>响应</p>
                <p>二、提出异议的渠道和方式</p>
            """,
        }

        _, _, data, _ = HuaxinParser.parse("hxr", detail)

        self.assertEqual(
            data["中标候选人名称"],
            [
                "001第二标段：山西刻选环境科技有限公司",
                "001第二标段：大同市华瑞化玻仪器有限责任公司",
                "001第二标段：山西太工环保设备有限公司",
            ],
        )
        self.assertEqual(
            data["中标候选人报价"],
            [
                Decimal("35950200.00"),
                Decimal("24901600.00"),
                Decimal("36343200.00"),
            ],
        )
        self.assertEqual(
            [item["候选人报价"] for item in data["中标候选人明细"]],
            data["中标候选人报价"],
        )

    def test_vertical_candidate_table_infers_unique_section_from_intro(self):
        detail = {
            "annId": "candidate-inferred-section",
            "annClassification": "2",
            "annTitle": "生产药剂采购第二标段中标候选人公示",
            "annContent": """
                <p>代理机构受招标人委托，对生产药剂采购第二标段进行公开招标。</p>
                <p>一、评标情况</p>
                <p>排序</p><p>中标候选人名称</p><p>投标报价（万元）</p>
                <p>1</p><p>甲公司</p><p>100</p>
                <p>2</p><p>乙公司</p><p>120</p>
                <p>二、提出异议的渠道和方式</p>
            """,
        }

        _, _, data, _ = HuaxinParser.parse("hxr", detail)

        self.assertEqual(
            data["中标候选人名称"],
            ["第二标段：甲公司", "第二标段：乙公司"],
        )
        self.assertEqual(
            [item["标段"] for item in data["中标候选人明细"]],
            ["第二标段", "第二标段"],
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

    def test_structured_subsections_already_in_body_are_not_appended_again(self):
        detail = {
            "annContent": """
                <p>一、评标情况</p><p>第1名：甲公司,投标报价：100元</p>
                <p>五、联系方式</p><p>招标人：测试单位</p>
            """,
            "reviewSituation": """
                <p>一、评标情况</p><p>第1名：甲公司,投标报价：100元</p>
            """,
            "contactInformation": """
                <p>五、联系方式</p><p>招标人：测试单位</p>
            """,
        }
        combined = _combine_text(detail)
        self.assertEqual(combined.count("第1名：甲公司"), 1)
        self.assertEqual(combined.count("招标人：测试单位"), 1)

    def test_structured_intro_with_spacing_difference_is_not_appended_again(self):
        detail = {
            "annContent2": """
                <p>本项目（招标项目编号：E1）经评审，确定001不分标段的中标结果，现公示如下：</p>
                <p>一、中标人信息</p><p>中标人：测试公司</p>
            """,
            "bidCondition": "本项目(招标项目编号:E1)经评审，确定001 不分标段的中标结果，现公示如下：",
        }

        combined = _combine_text(detail)

        self.assertEqual(combined.count("本项目"), 1)

    def test_spaced_and_numbered_tender_labels_are_extracted(self):
        detail = {
            "annClassification": "1",
            "annTitle": "标签格式测试招标公告",
            "annContent": """
                <p>一、招标条件</p>
                <p>项目资金为企业自筹，招标人为测试单位。</p>
                <p>二、项目概况和招标范围</p>
                <p>交 货 期：签订合同后2个月内。</p>
                <p>质量要求：符合国家标准。</p>
                <p>交货地点：招标人指定地点。</p>
                <p>四、招标文件的获取</p>
                <p>4.2获取方法：登录平台下载。</p>
                <p>五、投标文件的递交</p>
                <p>5.2递交方法：使用CA在线递交。</p>
            """,
        }

        _, _, data, _ = HuaxinParser.parse("zbgg_zys", detail)

        self.assertEqual(data["资金来源"], "企业自筹")
        self.assertEqual(data["工期/服务期/供货日期"], "签订合同后2个月内。")
        self.assertEqual(data["质量要求"], "符合国家标准。")
        self.assertEqual(data["获取方式"], "登录平台下载。")
        self.assertEqual(data["递交方法"], "使用CA在线递交。")

    def test_tender_new_formats_keep_scope_funding_service_and_download_way(self):
        detail = {
            "annClassification": "1",
            "annTitle": "安保服务项目招标公告",
            "annContent": """
                <p>一、招标条件</p>
                <p>本项目已具备招标条件，资金来源为自有资金，招标人为甲公司。</p>
                <p>二、项目概况和招标范围</p>
                <p>2.1项目规模：安保服务。</p>
                <p>2.2招标内容与范围：本项目划分为2个标段，本次招标为其中的：</p>
                <p>001第一标段：东区安保服务；</p>
                <p>002第二标段：西区安保服务。</p>
                <p>服务期限：1年（自合同签订之日起计算）。</p>
                <p>三、投标人资格要求</p>
                <p>3.1具有有效营业执照。</p>
                <p>四、招标文件的获取</p>
                <p>4.1获取时间：2026年7月13日至18日</p>
                <p>4.2登录“玖邦招标采购电子交易平台”下载招标文件；</p>
                <p>五、投标文件的递交</p>
            """,
        }

        _, _, data, _ = HuaxinParser.parse("zbgg_zys", detail)

        self.assertEqual(data["资金来源"], "自有资金")
        self.assertEqual(
            data["工期/服务期/供货日期"],
            "1年（自合同签订之日起计算）。",
        )
        self.assertIn("001第一标段：东区安保服务", data["招标内容与范围"])
        self.assertIn("002第二标段：西区安保服务", data["招标内容与范围"])
        self.assertNotIn("投标人资格要求", data["招标内容与范围"])
        self.assertNotIn("服务期限", data["招标内容与范围"])
        self.assertEqual(
            data["获取方式"],
            "登录“玖邦招标采购电子交易平台”下载招标文件",
        )

    def test_transaction_and_agent_project_numbers_are_both_kept(self):
        detail = {
            "annClassification": "1",
            "annTitle": "编号测试招标公告",
            "annContent": """
                <p>（招标编号：ZDF03-HZ260502）</p>
                <p>招标项目编号：E1401005107101357001</p>
            """,
        }
        _, _, data, _ = HuaxinParser.parse("zbgg_zys", detail)
        self.assertEqual(
            data["项目编号/招标编号"],
            "E1401005107101357001；ZDF03-HZ260502",
        )
        self.assertEqual(data["项目编号"], "E1401005107101357001")
        self.assertEqual(data["招标编号"], "ZDF03-HZ260502")

    def test_project_number_does_not_consume_next_subsection_number(self):
        detail = {
            "annClassification": "1",
            "annTitle": "编号边界测试招标公告",
            "annContent": """
                <p>2.2 招标编号：HXZB-GC20260613</p>
                <p>2.3 招标内容与范围：本项目划分为1个标段。</p>
            """,
        }

        _, _, data, _ = HuaxinParser.parse("zbgg_zys", detail)

        self.assertEqual(data["项目编号/招标编号"], "HXZB-GC20260613")
        self.assertEqual(data["项目编号"], "")
        self.assertEqual(data["招标编号"], "HXZB-GC20260613")

    def test_labelled_identifiers_stop_before_same_line_prose(self):
        detail = {
            "annClassification": "1",
            "annTitle": "同段正文编号边界测试招标公告",
            "annContent": """
                <p>某工程（招标项目编号：E1401005107101182001)资金来源为财政资金，招标人为甲公司。</p>
                <p>2.2 招标编号：SXZS招（2024）07-15；</p>
            """,
        }

        _, _, data, _ = HuaxinParser.parse("zbgg_zys", detail)

        self.assertEqual(data["项目编号"], "E1401005107101182001")
        self.assertEqual(data["招标编号"], "SXZS招（2024）07-15")

    def test_repeated_project_number_label_is_removed(self):
        detail = {
            "annClassification": "1",
            "annTitle": "重复标签测试招标公告",
            "annContent": (
                "某工程（招标项目编号：招标项目编号："
                "E1401005129000015008）,项目资金来源为自筹资金。"
            ),
        }

        _, _, data, _ = HuaxinParser.parse("zbgg_zys", detail)

        self.assertEqual(data["项目编号"], "E1401005129000015008")

    def test_identifier_keeps_its_own_balanced_parentheses(self):
        detail = {
            "annClassification": "1",
            "annTitle": "括号编号测试招标公告",
            "annContent": "招标编号：晋招（2024）；",
        }

        _, _, data, _ = HuaxinParser.parse("zbgg_zys", detail)

        self.assertEqual(data["招标编号"], "晋招（2024）")

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

    def test_ann_nature_is_preserved_with_frontend_meaning(self):
        tender = {
            "annId": "nature-tender",
            "annClassification": "1",
            "annNature": "10",
            "announcementType": "1",
            "annTitle": "测试项目暂停公告",
        }
        _, _, tender_data, _ = HuaxinParser.parse("zbgg_zys", tender)
        self.assertEqual(
            tender_data["源站公告性质"],
            "暂停（annNature=10）",
        )

        candidate = {
            "annId": "nature-candidate",
            "annClassification": "2",
            "annNature": "4",
            "annTitle": "测试项目更正中标候选人公示",
        }
        _, _, candidate_data, _ = HuaxinParser.parse("hxr", candidate)
        self.assertEqual(
            candidate_data["源站公告性质"],
            "更正中标候选人公示（annNature=4）",
        )

        correction = {
            "annId": "nature-correction",
            "annClassification": "3",
            "annNature": "5",
            "annTitle": "测试项目撤销中标结果公示",
        }
        subtype, notice_type, correction_data, _ = HuaxinParser.parse(
            "gs",
            correction,
        )
        self.assertEqual((subtype, notice_type), ("gzjg", "更正结果公示"))
        self.assertEqual(
            correction_data["公共类型"],
            "撤销中标结果（annNature=5）",
        )

        unknown = {
            "annId": "nature-unknown",
            "annClassification": "1",
            "annNature": "99",
            "annTitle": "未知性质公告",
        }
        _, _, unknown_data, _ = HuaxinParser.parse("zbgg_zys", unknown)
        self.assertEqual(
            unknown_data["源站公告性质"],
            "未知（annNature=99）",
        )

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
        normalized = canonicalize_notice_data(
            notice_type, data, include_parser_diagnostics=True
        )
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
        normalized = canonicalize_notice_data(
            notice_type, data, include_parser_diagnostics=True
        )
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
        self.assertNotIn("附件", data)

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
        self.assertNotIn("附件", data)
        self.assertEqual(get_notice_type_code("招标公告"), "TENDER")
        self.assertEqual(get_notice_type_code("TENDER"), "TENDER")

    def test_full_text_variants_fill_duration_quality_scope_and_control_price(self):
        detail = {
            "annId": "full-text-variants",
            "annClassification": "1",
            "annTitle": "完整正文规则测试招标公告",
            "annContent": """
                <p>二、项目概况与招标范围</p>
                <p>2.1项目概况：测试项目；合同履行期限：合同签订后3年；交货地点：指定地点；服务质量要求：符合国家标准。</p>
                <p>2.2招标范围：工程量清单范围内的全部内容。</p>
                <p>2.3招标控制价：9692569.81元；投标报价不得超过控制价。</p>
                <p>3、投标人资格要求</p>
                <p>具有独立法人资格。</p>
            """,
        }

        _, _, data, _ = HuaxinParser.parse("zbgg_zys", detail)

        self.assertEqual(data["工期/服务期/供货日期"], "合同签订后3年")
        self.assertEqual(data["质量要求"], "符合国家标准。")
        self.assertEqual(data["招标内容与范围"], "工程量清单范围内的全部内容。")
        self.assertEqual(data["招标金额"], Decimal("9692569.81"))

    def test_multisection_delivery_period_keeps_all_sections(self):
        detail = {
            "annId": "multi-section-duration",
            "annClassification": "1",
            "annTitle": "多标段设备采购招标公告",
            "annContent": """
                <p>2.4交货期：</p>
                <p>第一标段：合同签订后30日历天内。</p>
                <p>第二标段：合同签订后60日历天内。</p>
                <p>2.5交货地点：招标人指定地点。</p>
                <p>3、投标人资格要求</p>
            """,
        }

        _, _, data, _ = HuaxinParser.parse("zbgg_zys", detail)

        self.assertIn("第一标段：合同签订后30日历天内。", data["工期/服务期/供货日期"])
        self.assertIn("第二标段：合同签订后60日历天内。", data["工期/服务期/供货日期"])

    def test_award_full_text_fills_duration_manager_and_certificate(self):
        detail = {
            "annId": "award-full-text",
            "annClassification": "3",
            "annTitle": "监理项目中标结果公示",
            "annContent": """
                <p>中标人：测试监理有限公司；中标价：123456元。</p>
                <p>工期：96天；质量：合格；项目负责人：宋少龙；</p>
                <p>证书名称及编号：注册监理工程师（房屋建筑工程专业）、14008291。</p>
            """,
        }

        _, _, data, _ = HuaxinParser.parse("gs", detail)

        self.assertEqual(data["工期"], "96天")
        self.assertEqual(data["项目经理"], "宋少龙")
        self.assertEqual(
            data["项目经理证书名称"],
            "注册监理工程师（房屋建筑工程专业）",
        )
        self.assertEqual(data["项目经理证书编号"], "14008291")

    def test_tws_batch_description_is_not_organization_form(self):
        _, _, data, _ = HuaxinParser.parse(
            "zbgg_zys",
            {
                "annClassification": "1",
                "annTitle": "组织形式测试招标公告",
                "annNum": "重新招标",
                "annContent": "<p>组织形式：委托招标</p>",
            },
        )
        self.assertEqual(data["组织形式"], "委托招标")

    def test_main_qualification_is_not_lost_when_consortium_api_field_exists(self):
        _, _, data, _ = HuaxinParser.parse(
            "zbgg_zys",
            {
                "annClassification": "1",
                "annTitle": "资格边界测试招标公告",
                "consortiumQualification": "联合体成员不得超过三家。",
                "annContent": """
                    <p>三、投标人资格要求</p>
                    <p>3.1投标人须具备建筑工程施工总承包资质。</p>
                    <p>四、招标文件获取</p><p>登录平台下载文件。</p>
                """,
            },
        )
        qualification = data["申请人资格要求/投标人资格要求"]
        self.assertIn("建筑工程施工总承包资质", qualification)
        self.assertIn("联合体成员不得超过三家", qualification)
        self.assertNotIn("登录平台下载文件", qualification)

    def test_contact_address_roles_and_literal_u3000_are_cleaned(self):
        _, _, data, _ = HuaxinParser.parse(
            "hxr",
            {
                "annClassification": "2",
                "annTitle": "联系方式测试中标候选人公示",
                "annContent": """
                    <p>五、联系方式</p>
                    <p>招标人：甲公司</p><p>地址：甲方地址</p>
                    <p>联系人：张三</p><p>电话：111</p>
                    <p>招标代理机构：乙公司u3000u3000</p>
                    <p>联系地址：乙方地址</p><p>联系人：李四</p><p>电话：222</p>
                """,
            },
        )
        self.assertEqual(data["招标代理机构"], "乙公司")
        self.assertEqual(data["招标代理机构地址"], "乙方地址")
        self.assertNotEqual(data["招标人地址"], data["招标代理机构地址"])

    def test_ranked_inline_price_without_colon_is_not_part_of_candidate_name(self):
        _, _, data, _ = HuaxinParser.parse(
            "hxr",
            {
                "annClassification": "2",
                "annTitle": "候选人边界测试中标候选人公示",
                "reviewSituation": """
                    <p>一、评标情况</p><p>001第一标段</p>
                    <p>第1名：山西元久建筑工程有限公司，投标报价9677129.73元：，工期：45天，项目负责人：付松</p>
                    <p>二、提出异议</p>
                """,
            },
        )
        self.assertEqual(
            data["中标候选人名称"],
            ["001第一标段：山西元久建筑工程有限公司"],
        )
        self.assertEqual(data["中标候选人报价"], [Decimal("9677129.73")])

    def test_named_candidate_inline_price_is_not_part_of_candidate_name(self):
        _, _, data, _ = HuaxinParser.parse(
            "hxr",
            {
                "annClassification": "2",
                "annTitle": "监理候选人测试中标候选人公示",
                "reviewSituation": """
                    <p>一、评标情况</p><p>001第一标段</p>
                    <p>第一中标候选人：河南省光大建设管理有限公司,投标报价：1924477元,监理服务期限：96天</p>
                    <p>二、提出异议</p>
                """,
            },
        )
        self.assertEqual(
            data["中标候选人名称"],
            ["001第一标段：河南省光大建设管理有限公司"],
        )
        self.assertEqual(data["中标候选人报价"], [Decimal("1924477.00")])

    def test_candidate_without_published_price_does_not_absorb_service_fields(self):
        _, _, data, _ = HuaxinParser.parse(
            "hxr",
            {
                "annClassification": "2",
                "annTitle": "检测服务中标候选人公示",
                "reviewSituation": """
                    <p>一、评标情况</p><p>001第一标段</p>
                    <p>第一中标候选人：吕梁天基建设工程质量检测有限公司,服务期限：合同签订后120天内,项目负责人：王晓红</p>
                    <p>二、提出异议</p>
                """,
            },
        )
        self.assertEqual(
            data["中标候选人名称"],
            ["001第一标段：吕梁天基建设工程质量检测有限公司"],
        )
        self.assertEqual(data["中标候选人报价"], [None])

    def test_candidate_textual_unit_prices_are_kept_but_not_part_of_name(self):
        _, _, data, _ = HuaxinParser.parse(
            "hxr",
            {
                "annClassification": "2",
                "annTitle": "代理购电服务中标候选人公示",
                "reviewSituation": """
                    <p>一、评标情况</p>
                    <p>第一名：山西智华能源投资有限公司，投标报价：电压等级10KV每度优惠0.0315元，电压等级35KV每度优惠0.0315元，服务期限：响应招标文件</p>
                    <p>二、提出异议</p>
                """,
            },
        )
        self.assertEqual(
            data["中标候选人名称"],
            ["山西智华能源投资有限公司"],
        )
        self.assertEqual(
            data["中标候选人报价"],
            ["投标报价：电压等级10KV每度优惠0.0315元，电压等级35KV每度优惠0.0315元"],
        )

    def test_recommended_shortlisted_candidates_are_extracted(self):
        _, _, data, _ = HuaxinParser.parse(
            "hxr",
            {
                "annClassification": "2",
                "annTitle": "钻井工程中标候选人公示",
                "reviewSituation": """
                    <p>一、评标情况</p><p>001第一标段</p>
                    <p>推荐入围中标候选人1：甲工程有限公司,投标报价：20286500元,工期：30日历天</p>
                    <p>推荐入围中标候选人2：乙工程有限公司</p>
                    <p>二、提出异议的渠道和方式</p>
                """,
            },
        )
        self.assertEqual(
            data["中标候选人名称"],
            ["001第一标段：甲工程有限公司", "001第一标段：乙工程有限公司"],
        )
        self.assertEqual(
            data["中标候选人报价"],
            [Decimal("20286500.00"), None],
        )

    def test_plain_recommended_candidate_heading_uses_next_company(self):
        _, _, data, _ = HuaxinParser.parse(
            "hxr",
            {
                "annClassification": "2",
                "annTitle": "软件项目中标候选人公示",
                "annContent": """
                    <p>经评标委员会评审，推荐的中标候选人，现公示如下：</p>
                    <p>一、推荐中标候选人</p>
                    <p>中电金信软件有限公司</p>
                    <p>二、提出异议的渠道和方式</p>
                """,
            },
        )
        self.assertEqual(data["中标候选人名称"], ["中电金信软件有限公司"])
        self.assertEqual(data["中标候选人报价"], [None])

    def test_vertical_shortlist_table_without_price_keeps_all_candidates(self):
        _, _, data, _ = HuaxinParser.parse(
            "hxr",
            {
                "annClassification": "2",
                "annTitle": "框架协议入围候选人公示",
                "reviewSituation": """
                    <p>一、评标情况</p><p>序号</p><p>入围候选人名称</p>
                    <p>1</p><p>甲服务有限公司</p>
                    <p>2</p><p>乙服务有限公司</p>
                    <p>二、提出异议的渠道和方式</p>
                """,
            },
        )
        self.assertEqual(
            data["中标候选人名称"],
            ["甲服务有限公司", "乙服务有限公司"],
        )
        self.assertEqual(data["中标候选人报价"], [None, None])

    def test_changed_qualification_uses_final_effective_text_and_stops_at_supervision(self):
        _, _, data, _ = HuaxinParser.parse(
            "zbgg_zys",
            {
                "annClassification": "1",
                "annTitle": "资格要求变更公告",
                "annContent": """
                    <p>三、投标人资格要求</p><p>须具备TMMi3认证。</p>
                    <p>现变更为：</p>
                    <p>三、投标人资格要求</p><p>须具备TMMi3（含）以上认证。</p>
                    <p>二、监督部门</p><p>监督单位：测试部门</p>
                    <p>三、联系方式</p><p>招标人：测试单位</p>
                """,
            },
        )
        qualification = data["申请人资格要求/投标人资格要求"]
        self.assertIn("TMMi3（含）以上", qualification)
        self.assertNotIn("须具备TMMi3认证", qualification)
        self.assertNotIn("监督单位", qualification)


if __name__ == "__main__":
    unittest.main()
