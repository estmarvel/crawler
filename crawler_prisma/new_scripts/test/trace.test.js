"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const test = require("node:test");
const {
  SITE_CONFIG,
  compactDatabaseString,
  loadTraceSources,
  parseCrawlerDate,
  stableDigest,
  traceEnvelope,
} = require("../lib/runtime");
const {
  buildEvidence,
  buildExtractedFields,
  resolveNoticeType,
} = require("../import_notice_extractions");
const { databaseFileType } = require("../import_raw_notice_attachments");

test("legacy records without _trace remain supported", () => {
  assert.equal(traceEnvelope({ 公告ID: "legacy-1" }, "legacy"), null);
});

test("all current framework sites are supported split-storage import sources", () => {
  assert.deepEqual(SITE_CONFIG.sxzwfw.shortCodes, ["sxzwfw"]);
  assert.equal(SITE_CONFIG.sxzwfw.preferredDataSourceId, 21);
  assert.equal(SITE_CONFIG.bitbid.preferredDataSourceId, 12);
  assert.equal(SITE_CONFIG.sxjkzcpt.preferredDataSourceId, 9);
  assert.equal(SITE_CONFIG.trade365.preferredDataSourceId, 16);
  assert.equal(SITE_CONFIG.sxxindian.preferredDataSourceId, 18);
  assert.equal(SITE_CONFIG.sxbid.preferredDataSourceId, 22);
  assert.equal(SITE_CONFIG.qianji.preferredDataSourceId, 24);
  assert.equal(SITE_CONFIG.runshihua.preferredDataSourceId, null);
  assert.deepEqual(SITE_CONFIG.runshihua.shortCodes, ["runshihua"]);
  assert.equal(SITE_CONFIG.gxebidding.preferredDataSourceId, 26);
  assert.deepEqual(SITE_CONFIG.gxebidding.shortCodes, ["gxebidding", "guoxin_shanxi"]);
  assert.equal(SITE_CONFIG.sxzfcg.preferredDataSourceId, 27);
  assert.deepEqual(SITE_CONFIG.sxzfcg.shortCodes, ["sxzfcg", "ccgp_shanxi"]);
});

test("crawler dates accept both source dates and crawler datetimes", () => {
  assert.equal(
    parseCrawlerDate("2025-11-17", "发布日期", "row").toISOString(),
    "2025-11-16T16:00:00.000Z",
  );
  assert.equal(
    parseCrawlerDate("2026-08-12 16:22:23.123", "爬虫时间", "row").toISOString(),
    "2026-08-12T08:22:23.123Z",
  );
});

test("trace envelope accepts the crawler v1 contract", () => {
  const trace = {
    schemaVersion: "1.0",
    payload: { list: { id: "n-1" }, detail: { content: "raw" } },
    rawHtml: "<p>raw</p>",
    rawText: "raw",
    responseMetadata: { response: { status: 200 } },
    crawlerVersion: "sxjm-v1",
    fieldMeta: {
      fieldConfidences: { 项目名称: 1 },
      evidence: [{ field: "项目名称", source: "detail.name" }],
    },
    exportMetadata: {
      noticeSubtype: "zbxm.zbgg",
      missingFields: ["招标金额"],
    },
  };

  assert.equal(traceEnvelope({ _trace: trace }, "new"), trace);
});

test("trace envelope rejects values that MongoDB validators cannot store", () => {
  assert.throws(
    () => traceEnvelope({ _trace: { schemaVersion: "1.0", payload: {}, fieldMeta: { evidence: "bad" } } }, "bad"),
    /evidence must be an array/,
  );
});

test("v2 trace loads and verifies independent HTML and payload snapshots", () => {
  const outputRoot = fs.mkdtempSync(path.join(os.tmpdir(), "crawler-trace-v2-"));
  const html = Buffer.from("<p>原始页面</p>");
  const payload = Buffer.from(JSON.stringify({ detail: { id: "n-2" } }));
  fs.mkdirSync(path.join(outputRoot, "site", "snapshots"), { recursive: true });
  fs.mkdirSync(path.join(outputRoot, "site", "payloads"), { recursive: true });
  fs.writeFileSync(path.join(outputRoot, "site", "snapshots", "n-2.html"), html);
  fs.writeFileSync(path.join(outputRoot, "site", "payloads", "n-2.json"), payload);
  const digest = (value) => require("node:crypto").createHash("sha256").update(value).digest("hex");
  const source = {
    HTML快照路径: "site/snapshots/n-2.html",
    HTML快照SHA256: digest(html),
    _trace: {
      schemaVersion: "2.0",
      crawlerVersion: "crawler-v2",
      extractionVersion: "parser-v2",
      payloadSnapshot: {
        path: "site/payloads/n-2.json",
        sha256: digest(payload),
      },
    },
  };
  const record = { source, outputRoot, context: "v2 row" };
  const trace = traceEnvelope(source, record.context);
  const loaded = loadTraceSources(record, trace);
  assert.equal(loaded.rawHtml, "<p>原始页面</p>");
  assert.deepEqual(loaded.payload, { detail: { id: "n-2" } });
  fs.rmSync(outputRoot, { recursive: true, force: true });
});

test("long crawler versions are compacted deterministically for MySQL varchar columns", () => {
  const source = "sxzwfw-v5-engineering-live-fields";
  const compacted = compactDatabaseString(source, 32);
  assert.equal([...compacted].length, 32);
  assert.equal(compacted, compactDatabaseString(source, 32));
  assert.notEqual(compacted, compactDatabaseString(`${source}-other`, 32));
});

test("long Office MIME types keep full source semantics outside the VARCHAR(32) index", () => {
  assert.equal(
    databaseFileType(
      "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
      "供货明细.docx",
    ),
    "application/docx",
  );
  assert.equal(databaseFileType("application/pdf; charset=binary", "公告.pdf"), "application/pdf");
});

test("stable digest ignores object key order but preserves data differences", () => {
  assert.equal(stableDigest({ b: 2, a: { y: 2, x: 1 } }), stableDigest({ a: { x: 1, y: 2 }, b: 2 }));
  assert.notEqual(stableDigest({ value: "A" }), stableDigest({ value: "B" }));
});

test("all exported diagnostics have an existing extraction document destination", () => {
  const source = {
    平台名称: "华新阳光采购平台",
    平台代码: "huaxin",
    公告ID: "n-1",
    公告类型: "CORRECTION",
    公告子类型: "gzjg",
    公告标题: "更正公告",
    公告内容: "需要保留的更正内容",
    缺失字段: ["监督部门联系方式"],
    附件: [{ source_file_id: "f-1", file_name: "source.pdf" }],
    招标编号: "",
    中标金额: null,
    候选人列表: [],
    是否联合体: false,
    _trace: { schemaVersion: "1.0", payload: {}, fieldMeta: { site_parser: "v1" } },
  };

  const extracted = buildExtractedFields(source);
  assert.equal(extracted["公告内容"], "需要保留的更正内容");
  assert.equal(extracted["是否联合体"], false);
  assert.equal(extracted["招标编号"], "");
  assert.equal(extracted["中标金额"], null);
  assert.deepEqual(extracted["候选人列表"], []);
  const evidence = buildEvidence({ source, trace: source._trace });
  assert.deepEqual(evidence.at(-1).missingFields, ["监督部门联系方式"]);
  assert.equal(evidence.at(-1).fieldMeta.site_parser, "v1");
});

test("termination type overrides an sxjm source section that was misclassified", () => {
  assert.equal(
    resolveNoticeType(
      { 公告类型: "TERMINATION", 公告子类型: "fzxm.cggg" },
      "sxjm termination",
    ),
    "终止公告",
  );
});
