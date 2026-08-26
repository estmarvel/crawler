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
    def test_correction_title_suffixes_do_not_pollute_project_name(self):
        cases = {
            "道路监理项目(1标段)重新招标控制价": "道路监理项目(1标段)",
            "仓储项目总承包招标变更公告": "仓储项目总承包",
            "道路工程施工二次延期公告": "道路工程施工",
            "设备采购招标补充公告": "设备采购",
            "配套设备采购货物流标公告": "配套设备采购货物",
            "公路施工001标段 评标报告": "公路施工001标段",
        }
        for title, expected in cases.items():
            with self.subTest(title=title):
                page = detail_html(title, "<p>有效投标人不足三家。</p>")
                parsed = SxzwfwParser.parse(
                    "qt",
                    page,
                    {"notice_id": title},
                    f"https://prec.sxzwfw.gov.cn/jyxxgcqt/{abs(hash(title))}.jhtml",
                )
                self.assertEqual(parsed.data["项目名称"], expected)

    def test_identifiers_prefer_transaction_project_code_and_drop_prose(self):
        page = detail_html(
            "产业园建设项目招标公告",
            """
            <p>项目编号：ZLZX招【2026】0487号</p>
            <p>（招标项目编号：D3201150734fdpsb1v19）由太原市行政审批服务管理局批准建设</p>
            <p>招标编号：RC2026F012</p>
            """,
        )
        parsed = SxzwfwParser.parse(
            "zbgg_zys", page, {"notice_id": "clean-identifiers-1"},
            "https://prec.sxzwfw.gov.cn/jyxxgczb/clean-identifiers-1.jhtml",
        )

        self.assertEqual(parsed.data["项目编号"], "D3201150734fdpsb1v19")
        self.assertEqual(parsed.data["招标编号"], "RC2026F012")
        self.assertEqual(
            parsed.data["项目编号/招标编号"],
            "D3201150734fdpsb1v19；ZLZX招【2026】0487号；RC2026F012",
        )

    def test_identifiers_stop_before_approval_text_and_keep_balanced_brackets(self):
        page = detail_html(
            "生活污水治理项目招标公告",
            """
            <p>投资项目统一代码：2502-140109-89-01-287745批准建设</p>
            <p>项目编号：E1401000267024234001001）由太原市行政审批服务管理局批准</p>
            <p>招标编号：晋新招字（2026）第043号</p>
            """,
        )
        parsed = SxzwfwParser.parse(
            "zbgg_zys", page, {"notice_id": "clean-identifiers-2"},
            "https://prec.sxzwfw.gov.cn/jyxxgczb/clean-identifiers-2.jhtml",
        )

        self.assertEqual(parsed.data["项目编号"], "2502-140109-89-01-287745")
        self.assertEqual(parsed.data["招标编号"], "晋新招字（2026）第043号")
        self.assertNotIn("批准", parsed.data["项目编号/招标编号"])
        self.assertNotIn("由太原市", parsed.data["项目编号/招标编号"])

    def test_wrapped_identifier_uses_complete_value_instead_of_short_fragment(self):
        page = detail_html(
            "国土绿化项目招标公告",
            """
            <p>(项目编号：2026GC010064)</p>
            <p>本项目（招标项目编号：202<br/>6GC010064）,已批准建设。</p>
            """,
        )
        parsed = SxzwfwParser.parse(
            "zbgg_zys", page, {"notice_id": "wrapped-identifier"},
            "https://prec.sxzwfw.gov.cn/jyxxgczb/wrapped-identifier.jhtml",
        )

        self.assertEqual(parsed.data["项目编号"], "2026GC010064")
        self.assertEqual(parsed.data["项目编号/招标编号"], "2026GC010064")

    def test_placeholder_identifier_is_treated_as_missing(self):
        page = detail_html(
            "占位编号项目招标公告",
            "<p>项目编号：null</p><p>招标编号：无</p>",
        )
        parsed = SxzwfwParser.parse(
            "zbgg_zys", page, {"notice_id": "placeholder-identifier"},
            "https://prec.sxzwfw.gov.cn/jyxxgczb/placeholder-identifier.jhtml",
        )

        self.assertEqual(parsed.data["项目编号"], "")
        self.assertEqual(parsed.data["招标编号"], "")

    def test_identifiers_stop_at_inline_numbered_fields_and_nested_labels(self):
        page = detail_html(
            "字段粘连项目招标公告",
            """
            <p>备案项目代码:2509-140602-89-05-864976 项目总投资为：831.110万元</p>
            <p>项目编号：E1405000297A02059001001 2.3 项目地址：泽州县</p>
            <p>项目编号：141001-20240913-SJ003-02 招标编号：GC141000202400657001</p>
            """,
        )
        parsed = SxzwfwParser.parse(
            "zbgg_zys", page, {"notice_id": "inline-boundaries"},
            "https://prec.sxzwfw.gov.cn/jyxxgczb/inline-boundaries.jhtml",
        )
        self.assertEqual(parsed.data["项目编号"], "2509-140602-89-05-864976")
        self.assertEqual(parsed.data["招标编号"], "GC141000202400657001")

    def test_empty_and_unbalanced_identifiers_are_missing(self):
        page = detail_html(
            "空编号项目招标公告",
            """
            <p>项目编号：</p><p>招标控制总价：201.2702万元</p>
            <p>招标编号：ZXX-GC-020（2024</p>
            <p>招标项目编号：M1401000155207882001</p>
            """,
        )
        parsed = SxzwfwParser.parse(
            "zbgg_zys", page, {"notice_id": "empty-unbalanced"},
            "https://prec.sxzwfw.gov.cn/jyxxgczb/empty-unbalanced.jhtml",
        )
        self.assertEqual(parsed.data["项目编号"], "M1401000155207882001")
        self.assertEqual(parsed.data["招标编号"], "")

    def test_project_and_tender_amounts_stop_before_following_prose(self):
        page = detail_html(
            "工程质量检测招标公告",
            """
            <p>项目总投资：40700.22万元，本次招标：根据收费标准的65%计取。</p>
            <p>本次招标金额约：127万元。</p>
            """,
        )
        parsed = SxzwfwParser.parse(
            "zbgg_zys", page, {"notice_id": "clean-amounts"},
            "https://prec.sxzwfw.gov.cn/jyxxgczb/clean-amounts.jhtml",
        )

        self.assertEqual(parsed.data["项目总投资/估算金额"], Decimal("407002200.00"))
        self.assertEqual(parsed.data["招标金额"], Decimal("1270000.00"))

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
        self.assertEqual(parsed.data["项目地点"], "晋城市.泽州县")
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

    def test_award_table_separates_consortium_leader_member_and_price(self):
        page = detail_html(
            "高速公路咨询服务中标结果公示",
            """
            <p>一、中标人信息</p>
            <table>
              <tr><th>标段</th><th>中标人</th><th>中标价（元）</th></tr>
              <tr><td>001标段</td><td>
                <p>牵头人：山西省交通环境保护中心站（有限公司）</p>
                <p>成员：山西同创坤宇泰科技有限公司</p>
              </td><td>3758000</td></tr>
            </table>
            <p>二、其他公示内容</p>
            """,
        )
        parsed = SxzwfwParser.parse(
            "gs", page, {"notice_id": "award-consortium-table"},
            "https://prec.sxzwfw.gov.cn/jyxxgcgs/award-consortium-table.jhtml",
        )

        self.assertEqual(
            parsed.data["中标人名称"],
            ["山西省交通环境保护中心站（有限公司）"],
        )
        self.assertEqual(
            parsed.data["联合体成员"], ["山西同创坤宇泰科技有限公司"]
        )
        self.assertEqual(parsed.data["中标价"], [Decimal("3758000.00")])

    def test_award_table_supports_consortium_unit_name_labels(self):
        page = detail_html(
            "加工项目EPC中标结果公示",
            """
            <table>
              <tr><th>排序</th><th>中标人</th><th>中标价</th></tr>
              <tr><td>1</td><td>牵头人单位名称:晋城市建工集团有限公司
                联合体单位名称:中国医药集团联合工程有限公司</td>
                <td>建安工程费(元)：265234200 设计费(元)：2780000</td></tr>
            </table>
            """,
        )
        parsed = SxzwfwParser.parse(
            "gs", page, {"notice_id": "award-consortium-unit"},
            "https://prec.sxzwfw.gov.cn/jyxxgcgs/award-consortium-unit.jhtml",
        )

        self.assertEqual(parsed.data["中标人名称"], ["晋城市建工集团有限公司"])
        self.assertEqual(
            parsed.data["联合体成员"], ["中国医药集团联合工程有限公司"]
        )
        self.assertEqual(
            parsed.data["中标价"],
            ["建安工程费(元)：265234200 设计费(元)：2780000"],
        )

    def test_award_spaced_labels_are_extracted(self):
        page = detail_html(
            "节能改造项目中标结果公示",
            """
            <p>一、中标人信息</p><p>001不分标段：</p>
            <p>中&nbsp;标&nbsp;人：甲建筑有限公司和乙设计有限公司联合体</p>
            <p>中&nbsp;标&nbsp;价：521.028699 万元</p>
            <p>二、其他公示内容</p>
            """,
        )
        parsed = SxzwfwParser.parse(
            "gs", page, {"notice_id": "spaced-award"},
            "https://prec.sxzwfw.gov.cn/jyxxgcgs/spaced-award.jhtml",
        )

        self.assertEqual(
            parsed.data["中标结果明细"],
            [{
                "标段": "001不分标段",
                "中标人名称": "甲建筑有限公司和乙设计有限公司联合体",
                "中标价": Decimal("5210286.99"),
            }],
        )

    def test_award_narrative_winner_and_quote_are_extracted(self):
        page = detail_html(
            "施工项目中标结果公示",
            """
            <p>招标人确定中临建工（河南）有限公司为该项目的中标人，现予以公示。</p>
            <p>投标报价：299.274105万元</p>
            <p>计划工期：730日历天</p>
            """,
        )
        parsed = SxzwfwParser.parse(
            "gs", page, {"notice_id": "narrative-award"},
            "https://prec.sxzwfw.gov.cn/jyxxgcgs/narrative-award.jhtml",
        )

        self.assertEqual(parsed.data["中标人名称"], ["中临建工（河南）有限公司"])
        self.assertEqual(parsed.data["中标价"], [Decimal("2992741.05")])

    def test_award_inline_labels_and_next_line_price_are_extracted(self):
        inline = detail_html(
            "道路工程中标结果公示",
            "<p>一、中标人山西振尔建设工程有限公司一、中标价格488.242168万元</p>",
        )
        parsed_inline = SxzwfwParser.parse(
            "gs", inline, {"notice_id": "inline-award"},
            "https://prec.sxzwfw.gov.cn/jyxxgcgs/inline-award.jhtml",
        )
        self.assertEqual(
            parsed_inline.data["中标结果明细"],
            [{
                "标段": "",
                "中标人名称": "山西振尔建设工程有限公司",
                "中标价": Decimal("4882421.68"),
            }],
        )

        split = detail_html(
            "机场工程中标结果公示",
            "<p>中标人: 山西机场建设有限公司</p><p>中标价格:</p><p>11836557.62</p>",
        )
        parsed_split = SxzwfwParser.parse(
            "gs", split, {"notice_id": "split-award"},
            "https://prec.sxzwfw.gov.cn/jyxxgcgs/split-award.jhtml",
        )
        self.assertEqual(parsed_split.data["中标人名称"], ["山西机场建设有限公司"])
        self.assertEqual(parsed_split.data["中标价"], [Decimal("11836557.62")])

    def test_other_termination_uses_correction_schema(self):
        page = detail_html(
            "某工程施工废标公告",
            "<p>因有效投标人不足三家，本项目废标。</p>",
        )
        parsed = SxzwfwParser.parse(
            "qt", page, {"notice_id": "terminated"},
            "https://prec.sxzwfw.gov.cn/jyxxgcyc/terminated.jhtml",
        )

        self.assertEqual((parsed.subtype, parsed.notice_type), ("gzjg", "更正结果公示"))
        self.assertEqual(parsed.data["公共类型"], "废标公告")
        self.assertIn("本项目废标", parsed.raw_text)

    def test_other_abnormal_notice_uses_body_to_detect_termination(self):
        page = detail_html(
            "某工程施工项目公告",
            "<p>异常情况描述：经评标委员会评审，有效投标人不足3家。</p>",
        )
        parsed = SxzwfwParser.parse(
            "qt", page, {"notice_id": "abnormal"},
            "https://prec.sxzwfw.gov.cn/jyxxgcyc/abnormal.jhtml",
        )

        self.assertEqual((parsed.subtype, parsed.notice_type), ("gzjg", "更正结果公示"))
        self.assertEqual(parsed.data["公共类型"], "废标公告")
        self.assertTrue(parsed.source_nature.startswith("异常（"))

    def test_contract_segment_is_not_misclassified_as_contract_notice(self):
        tender = SxzwfwParser.parse(
            "zbgg_zys",
            detail_html(
                "道路工程第二合同段施工招标公告",
                "<p>招标项目编号：E1401</p><p>一、招标内容与范围</p><p>道路施工。</p>",
            ),
            {"notice_id": "contract-segment"},
            "https://prec.sxzwfw.gov.cn/jyxxgczb/contract-segment.jhtml",
        )
        plan = SxzwfwParser.parse(
            "zbjh",
            detail_html("绿色农田建设（国内合同包项目）", "<p>项目名称：绿色农田建设</p>"),
            {"notice_id": "contract-package"},
            "https://prec.sxzwfw.gov.cn/jyxxgcjh/contract-package.jhtml",
        )
        self.assertEqual((tender.subtype, tender.notice_type), ("zbgg", "招标公告"))
        self.assertEqual((plan.subtype, plan.notice_type), ("zbjh", "招标计划"))

    def test_control_price_and_withdrawal_use_correction_schema(self):
        control = SxzwfwParser.parse(
            "zbgg_zys",
            detail_html(
                "某工程招标控制价公示",
                "<p>一、内容</p><p>招标控制总价：1000万元</p><p>二、联系方式</p>",
            ),
            {"notice_id": "control-price"},
            "https://prec.sxzwfw.gov.cn/jyxxgczb/control-price.jhtml",
        )
        withdrawn = SxzwfwParser.parse(
            "qt",
            detail_html("某工程招标撤销（终止）公告", "<p>一、内容</p><p>本项目终止。</p>"),
            {"notice_id": "withdrawn"},
            "https://prec.sxzwfw.gov.cn/jyxxgcyc/withdrawn.jhtml",
        )
        self.assertEqual((control.subtype, control.notice_type), ("gzjg", "更正结果公示"))
        self.assertEqual(control.data["公共类型"], "变更公告")
        self.assertEqual(control.data["项目名称"], "某工程")
        self.assertEqual(withdrawn.data["公共类型"], "撤销公告")
        self.assertEqual(withdrawn.data["项目名称"], "某工程")

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

    def test_embedded_pdf_windows_path_is_normalized_without_losing_notice(self):
        page = detail_html(
            "道路工程招标公告",
            '<iframe src="http://59.49.21.12:25006\\upload\\wj\\notice.pdf"></iframe>',
        )
        parsed = SxzwfwParser.parse(
            "zbgg_zys",
            page,
            {"notice_id": "windows-pdf-path"},
            "https://prec.sxzwfw.gov.cn/jyxxgczb/windows-pdf-path.jhtml",
        )

        self.assertEqual(len(parsed.attachments), 1)
        self.assertEqual(
            parsed.attachments[0]["file_url"],
            "http://59.49.21.12:25006/upload/wj/notice.pdf",
        )
        self.assertTrue(parsed.attachments[0]["is_notice_body"])

    def test_hidden_nodes_do_not_pollute_visible_text(self):
        page = detail_html(
            "隐藏内容测试招标公告",
            '<p>有效正文<span style="display:none">隐藏一</span>'
            '<span style="visibility: hidden">隐藏二</span></p>',
        )
        text = visible_content_text(page)
        self.assertEqual(text, "有效正文")

    def test_common_label_variants_preserve_explicit_name_scope_duration_and_qualification(self):
        page = detail_html(
            "标题中使用简称的项目招标公告",
            """
            <p>招标项目名称：正文中的完整项目名称</p>
            <p>合同履行期限：自合同签订之日起180日历天</p>
            <p>二、招标内容与范围</p><p>本次招标包括勘察、设计和施工。</p>
            <p>三、投标人资格能力要求</p><p>须具备相应工程设计资质。</p>
            <p>四、采购文件的获取</p><p>采购文件获取时间：2026-07-17 09:00</p>
            """,
        )
        parsed = SxzwfwParser.parse(
            "zbgg_zys",
            page,
            {"notice_id": "variant-1"},
            "https://prec.sxzwfw.gov.cn/jyxxgczb/variant-1.jhtml",
        )

        self.assertEqual(parsed.data["项目名称"], "正文中的完整项目名称")
        self.assertEqual(
            parsed.data["工期/服务期/供货日期"],
            "自合同签订之日起180日历天",
        )
        self.assertIn("勘察、设计和施工", parsed.data["招标内容与范围"])
        self.assertIn(
            "工程设计资质", parsed.data["申请人资格要求/投标人资格要求"]
        )
        self.assertEqual(
            parsed.data["预审文件获取时间"], "2026-07-17 09:00"
        )

    def test_real_numbered_labels_investment_name_and_complex_number(self):
        page = detail_html(
            "穿越工程(1标段)招标公告",
            """
            <p>(招标编号：晋新招字（2026）第043号)</p>
            <p>二、项目概况与招标范围</p>
            <p>项目规模：管道穿越工程。投资额：总投资422.88万元。</p>
            <p>2.4、建设工期：90日历天；</p>
            <p>2.5、质量要求：合格；</p>
            <p>2.6、计划投资：约257万元</p>
            <p>三、投标人资格要求</p><p>具有独立法人资格。</p>
            """,
        )

        parsed = SxzwfwParser.parse(
            "zbgg_zys",
            page,
            {"notice_id": "real-variants"},
            "https://prec.sxzwfw.gov.cn/jyxxgczb/real-variants.jhtml",
        )

        self.assertEqual(parsed.data["项目名称"], "穿越工程(1标段)")
        self.assertEqual(parsed.data["项目编号/招标编号"], "晋新招字（2026）第043号")
        self.assertEqual(parsed.data["项目编号"], "")
        self.assertEqual(parsed.data["招标编号"], "晋新招字（2026）第043号")
        self.assertEqual(
            parsed.data["项目总投资/估算金额"], Decimal("2570000.00")
        )
        self.assertEqual(parsed.data["工期/服务期/供货日期"], "90日历天")
        self.assertEqual(parsed.data["质量要求"], "合格")

    def test_live_tender_label_variants_are_normalized(self):
        page = detail_html(
            "道路工程招标公告",
            """
            <p>（标段编号：E14090000G3002061001003）</p>
            <p>最高投标限价总价：3265836.4元</p>
            <p>建设资金为除申请上级资金外，其余由县财政筹措。</p>
            <p>（1）电子招标文件获取时间:2026年8月5日至2026年8月10日</p>
            <p>（2）电子招标文件获取方式：登录交易平台免费下载</p>
            <p>开标时间：2026年9月8日9时00分</p>
            """,
        )
        parsed = SxzwfwParser.parse(
            "zbgg_zys", page, {"notice_id": "live-variants"},
            "https://prec.sxzwfw.gov.cn/jyxxgczb/live-variants.jhtml",
        )

        self.assertEqual(parsed.data["项目编号/招标编号"], "E14090000G3002061001003")
        self.assertEqual(parsed.data["项目编号"], "")
        self.assertEqual(parsed.data["招标编号"], "")
        self.assertEqual(parsed.data["招标金额"], Decimal("3265836.40"))
        self.assertEqual(
            parsed.data["资金来源"], "除申请上级资金外，其余由县财政筹措"
        )
        self.assertEqual(parsed.data["开标时间"], datetime(2026, 9, 8, 9, 0))
        self.assertEqual(parsed.data["开启时间"], "2026-09-08 09:00")
        self.assertEqual(
            parsed.data["预审文件获取时间"], "2026年8月5日至2026年8月10日"
        )
        self.assertEqual(parsed.data["获取方式"], "登录交易平台免费下载")

        fiscal_page = detail_html(
            "体育公园招标公告",
            "<p>财政审定金额：11456434.41元（一标段5256738.76元、二标段6199695.65元）。</p>",
        )
        fiscal = SxzwfwParser.parse(
            "zbgg_zys", fiscal_page, {"notice_id": "fiscal-total"},
            "https://prec.sxzwfw.gov.cn/jyxxgczb/fiscal-total.jhtml",
        )
        self.assertEqual(fiscal.data["招标金额"], Decimal("11456434.41"))

    def test_candidate_name_list_does_not_duplicate_section_prefix(self):
        page = detail_html(
            "道路工程第二标段中标候选人公示",
            """
            <p>一、评标情况</p><p>第二标段：</p>
            <p>第1名：甲建设有限公司，投标报价：100万元。</p>
            <p>第2名：乙建设有限公司，投标报价：110万元。</p>
            <p>二、提出异议</p>
            """,
        )

        parsed = SxzwfwParser.parse(
            "hxr", page, {"notice_id": "candidate-pure-name"},
            "https://prec.sxzwfw.gov.cn/jyxxgchxr/candidate-pure-name.jhtml",
        )

        self.assertEqual(
            parsed.data["中标候选人名称"],
            ["甲建设有限公司", "乙建设有限公司"],
        )
        self.assertEqual(
            parsed.data["中标候选人明细"][0]["候选人名称"],
            "甲建设有限公司",
        )

    def test_candidate_result_table_keeps_name_price_rows_and_merged_section(self):
        page = detail_html(
            "灾毁恢复工程中标候选人公示",
            """
            <p>001第一标段：</p>
            <table>
              <tr><th>标段</th><th>中标候选人名称</th><th>投标报价（元）</th><th>排名</th></tr>
              <tr><td>001第一标段</td><td>甲建设有限公司</td><td>7906382</td><td>1</td></tr>
              <tr><td>乙建设有限公司</td><td>7936193</td><td>2</td></tr>
            </table>
            """,
        )

        parsed = SxzwfwParser.parse(
            "hxr", page, {"notice_id": "candidate-table"},
            "https://prec.sxzwfw.gov.cn/jyxxgchxr/candidate-table.jhtml",
        )

        self.assertEqual(
            parsed.data["中标候选人名称"],
            ["甲建设有限公司", "乙建设有限公司"],
        )
        self.assertEqual(
            parsed.data["中标候选人报价"],
            [Decimal("7906382.00"), Decimal("7936193.00")],
        )
        self.assertTrue(
            all(item["标段"] == "001第一标段" for item in parsed.data["中标候选人明细"])
        )

    def test_candidate_correction_keeps_changed_names_in_correction_content(self):
        page = detail_html(
            "施工中标候选人更正公告",
            """
            <p>现更正为：</p><p>中标候选人名称</p>
            <p>甲集团有限公司</p><p>（联合体牵头人）</p>
            <p>证书信息</p><p>乙建设有限公司</p><p>（联合体成员）</p>
            <p>丙建设有限公司</p><p>二、提出异议的渠道和方式</p>
            """,
        )

        parsed = SxzwfwParser.parse(
            "bg", page, {"notice_id": "candidate-visual"},
            "https://prec.sxzwfw.gov.cn/jyxxgcbg/candidate-visual.jhtml",
        )

        self.assertEqual((parsed.subtype, parsed.notice_type), ("gzjg", "更正结果公示"))
        self.assertEqual(parsed.data["公共类型"], "更正公告")
        self.assertIn("甲集团有限公司", parsed.data["公告内容"])
        self.assertIn("乙建设有限公司", parsed.data["公告内容"])
        self.assertIn("丙建设有限公司", parsed.data["公告内容"])
        self.assertNotIn("中标候选人名称", parsed.data)

    def test_correction_content_and_broken_contact_lines_keep_semantic_boundaries(self):
        page = detail_html(
            "设备采购项目更正公告",
            """
            <p>一、内容</p><p>现将投标文件递交截止时间延期至2026年8月25日9时。</p>
            <p>二、提出异议的渠道和方式</p><p>通过交易平台提出。</p>
            <p>三、联系方式</p>
            <p>招 标 人：甲有限公司</p>
            <p>联 系 人：刘宁 电 话：18800001111 邮 箱：liuning@example.com</p>
            <p>招标代理机构：乙招标有限公司</p>
            <p>联 系 人：李婕</p><p>电</p><p>邮</p>
            <p>话：0355-2222222</p><p>箱：lijie@example.com</p>
            """,
        )

        parsed = SxzwfwParser.parse(
            "bg", page, {"notice_id": "broken-contact-lines"},
            "https://prec.sxzwfw.gov.cn/jyxxgcbg/broken-contact-lines.jhtml",
        )

        self.assertIn("延期至2026年8月25日9时", parsed.data["公告内容"])
        self.assertNotIn("提出异议", parsed.data["公告内容"])
        self.assertNotIn("联系方式", parsed.data["公告内容"])
        self.assertEqual(parsed.data["招标人联系人"], "刘宁")
        self.assertEqual(parsed.data["招标人联系方式"], "18800001111")
        self.assertEqual(parsed.data["招标代理机构联系人"], "李婕")
        self.assertEqual(parsed.data["招标代理机构联系方式"], "0355-2222222")


if __name__ == "__main__":
    unittest.main()
