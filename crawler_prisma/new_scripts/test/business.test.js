"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const test = require("node:test");
const {
  buildBusinessDataset,
  mapBusinessRecord,
  normalizeIdentifier,
  syntheticNameCode,
  resolveNoticeType,
  syntheticTenderCode,
} = require("../lib/business");
const { streamJsonArray } = require("../lib/runtime");

function notice(id, fields = {}) {
  const source = {
    平台名称: "测试平台",
    平台代码: "bitbid",
    公告ID: id,
    公告类型: "TENDER",
    公告子类型: "tender",
    公告标题: `测试项目${id}招标公告`,
    解析状态: "PARSED",
    项目名称: "测试项目",
    项目编号: "",
    招标编号: "",
    发布时间: "2026-08-05 10:00:00.000",
    爬虫时间: "2026-08-05 11:00:00.000",
    详情页链接: `https://example.test/${id}`,
    抽取方式: "rule",
    抽取版本: "v1",
    ...fields,
  };
  return { site: "bitbid", fileName: "test.json", index: Number(id), context: `row ${id}`, source };
}

function atSite(record, site, platformName = `${site}测试平台`) {
  return {
    ...record,
    site,
    context: `${site}/${record.context}`,
    source: {
      ...record.source,
      平台代码: site,
      平台名称: platformName,
    },
  };
}

test("database notice type follows normalized transport code while subtype remains trace metadata", () => {
  assert.equal(
    resolveNoticeType({ 公告类型: "CANDIDATE", 公告子类型: "fzxm.cjhxr" }),
    "中标候选人公示",
  );
  assert.equal(
    resolveNoticeType({ 公告类型: "AWARD", 公告子类型: "fzxm.cjgg" }),
    "中标结果公示",
  );
});

test("name fallback gets a deterministic namespaced project code", () => {
  assert.equal(
    syntheticNameCode("bitbid", "测试项目"),
    syntheticNameCode("bitbid", "测试项目"),
  );
  assert.match(syntheticNameCode("bitbid", "测试项目"), /^NAME:bitbid:SHA256:[0-9a-f]{64}$/);
  assert.notEqual(
    syntheticNameCode("bitbid", "测试项目"),
    syntheticNameCode("huaxin", "测试项目"),
  );
});

test("project identity uses project code, then tender code, then project name", () => {
  const dataset = buildBusinessDataset([
    notice("1", { 项目编号: "P-001", 招标编号: "T-001" }),
    notice("2", { 招标编号: "T-001" }),
    notice("3", { 项目名称: "仅招标编号项目", 招标编号: "T-002" }),
    notice("4", { 项目名称: "名称兜底项目" }),
    notice("5", { 项目名称: "名称兜底项目" }),
  ], { includeContent: false, fieldMode: "project" });

  assert.equal(dataset.projects.length, 3);
  const coded = dataset.projects.find((row) => row.data.projectCode === "P-001");
  assert.equal(coded.records.length, 2);
  const tender = dataset.projects.find((row) => row.identitySource === "TENDER_CODE");
  assert.equal(tender.data.projectCode, syntheticTenderCode("bitbid", "T-002"));
  const named = dataset.projects.find((row) => row.identitySource === "PROJECT_NAME");
  assert.equal(named.records.length, 2);
  assert.equal(named.data.projectCode, syntheticNameCode("bitbid", "名称兜底项目"));
});

test("ambiguous tender code never merges two different project codes", () => {
  const dataset = buildBusinessDataset([
    notice("1", { 项目编号: "P-A1", 招标编号: "T-2026-SHARED" }),
    notice("2", { 项目编号: "P-B2", 招标编号: "T-2026-SHARED" }),
    notice("3", { 项目名称: "缺少项目编号", 招标编号: "T-2026-SHARED" }),
  ], { includeContent: false, fieldMode: "project" });
  assert.equal(dataset.projects.length, 3);
  assert.equal(
    dataset.projects.find((row) => row.records.some((record) => record.sourceNoticeId === "3")).identitySource,
    "PROJECT_NAME",
  );
});

test("tender and name fallbacks are isolated by source site", () => {
  const dataset = buildBusinessDataset([
    notice("20", { 项目编号: "P-BITBID-20", 招标编号: "T-SHARED-2026", 项目名称: "比比项目" }),
    atSite(
      notice("21", { 招标编号: "T-SHARED-2026", 项目名称: "华新项目" }),
      "huaxin",
    ),
    notice("22", { 项目名称: "跨站同名项目" }),
    atSite(notice("23", { 项目名称: "跨站同名项目" }), "huaxin"),
  ], { includeContent: false, fieldMode: "project" });

  assert.equal(dataset.projects.length, 4);
  const huaxinTender = dataset.projects.find(
    (row) => row.records.some((record) => record.sourceNoticeId === "21"),
  );
  assert.equal(huaxinTender.identitySource, "TENDER_CODE");
  assert.equal(huaxinTender.data.projectCode, syntheticTenderCode("huaxin", "T-SHARED-2026"));
  const named = dataset.projects.filter((row) => row.data.projectName === "跨站同名项目");
  assert.equal(named.length, 2);
  assert.notEqual(named[0].data.projectCode, named[1].data.projectCode);
});

test("exact duplicate notices are skipped but conflicting identities are rejected", () => {
  const first = notice("30", { 项目编号: "P-30" });
  const exact = structuredClone(first);
  exact.context = "row 30 duplicate";
  const dataset = buildBusinessDataset([first, exact], {
    includeContent: false,
    fieldMode: "project",
  });
  assert.equal(dataset.records.length, 1);
  assert.equal(dataset.duplicateCount, 1);

  assert.throws(
    () => buildBusinessDataset([
      first,
      notice("30", { 项目编号: "P-OTHER-31", 项目名称: "另一个项目" }),
    ], { includeContent: false, fieldMode: "project" }),
    /conflicting duplicate 公告ID=30/u,
  );
});

test("identifier validation rejects composites and prose before database grouping", () => {
  assert.throws(
    () => normalizeIdentifier("E140100001；ZB-2026-01", "项目编号", "row 1"),
    /contains multiple identifiers/u,
  );
  assert.throws(
    () => normalizeIdentifier("E140100001已由审批局批准建设", "项目编号", "row 2"),
    /contains prose/u,
  );
  assert.equal(normalizeIdentifier("无", "项目编号", "row 3"), null);
  assert.throws(
    () => normalizeIdentifier("NXQC-2022-(采", "项目编号", "row 4"),
    /unbalanced brackets/u,
  );
  assert.throws(
    () => normalizeIdentifier("某项目3#楼_经评审招标公告", "招标编号", "row 5"),
    /field-label or HTML residue|prose/,
  );
});

test("legacy combined identifier is used only when both explicit identifiers are missing", () => {
  const legacy = mapBusinessRecord(notice("6", {
    项目编号: "",
    招标编号: "",
    "项目编号/招标编号": "ZB-2026-006",
  }), { includeContent: false });
  assert.equal(legacy.projectCode, null);
  assert.equal(legacy.tenderCode, "ZB-2026-006");

  const explicitProject = mapBusinessRecord(notice("7", {
    项目编号: "P-2026-007",
    招标编号: "",
    "项目编号/招标编号": "P-2026-007；ZB-2026-007",
  }), { includeContent: false });
  assert.equal(explicitProject.projectCode, "P-2026-007");
  assert.equal(explicitProject.tenderCode, null);

  const ambiguousCombined = mapBusinessRecord(notice("9", {
    项目编号: "",
    招标编号: "",
    "项目编号/招标编号": "P-2026-009；ZB-2026-009",
  }), { includeContent: false });
  assert.equal(ambiguousCombined.projectCode, null);
  assert.equal(ambiguousCombined.tenderCode, null);
});

test("project import refuses unparsed records", () => {
  assert.throws(
    () => mapBusinessRecord(notice("8", { 解析状态: "FAILED" })),
    /requires 解析状态=PARSED/u,
  );
});

test("project amount accepts absolute currency and leaves fee-rate text out of DECIMAL", () => {
  const dataset = buildBusinessDataset([
    notice("10", {
      项目编号: "P-2026-010",
      "项目总投资/估算金额": "12.5万元",
      招标金额: "按收费标准的65%计取",
    }),
  ], { includeContent: false, fieldMode: "project" });
  assert.equal(dataset.projects[0].data.estimatedAmount, "125000.00");
  assert.equal(dataset.projects[0].data.tenderAmount, null);
});

test("streamJsonArray handles nested objects, arrays, braces and escaped quotes", async () => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), "crawler-import-test-"));
  const file = path.join(directory, "records.json");
  const expected = [
    { id: "1", text: "包含 } 和 \\\" 引号", nested: { values: [1, { ok: true }] } },
    { id: "2", text: "第二条" },
  ];
  fs.writeFileSync(file, JSON.stringify(expected, null, 2));
  const actual = [];
  for await (const row of streamJsonArray(file)) actual.push(row);
  assert.deepEqual(actual, expected);
  fs.rmSync(directory, { recursive: true, force: true });
});
