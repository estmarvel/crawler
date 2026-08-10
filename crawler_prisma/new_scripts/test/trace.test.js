"use strict";

const assert = require("node:assert/strict");
const test = require("node:test");
const {
  SITE_CONFIG,
  compactDatabaseString,
  stableDigest,
  traceEnvelope,
} = require("../lib/runtime");
const { exportMetadata } = require("../import_raw_notices");
const {
  buildEvidence,
  buildExtractedFields,
  resolveNoticeType,
} = require("../import_notice_extractions");

test("legacy records without _trace remain supported", () => {
  assert.equal(traceEnvelope({ 公告ID: "legacy-1" }, "legacy"), null);
});

test("sxzwfw output is a supported split-storage import source", () => {
  assert.deepEqual(SITE_CONFIG.sxzwfw.shortCodes, ["sxzwfw"]);
  assert.equal(SITE_CONFIG.sxzwfw.preferredDataSourceId, 21);
  assert.equal(SITE_CONFIG.bitbid.preferredDataSourceId, 12);
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

test("long crawler versions are compacted deterministically for MySQL varchar columns", () => {
  const source = "sxzwfw-v5-engineering-live-fields";
  const compacted = compactDatabaseString(source, 32);
  assert.equal([...compacted].length, 32);
  assert.equal(compacted, compactDatabaseString(source, 32));
  assert.notEqual(compacted, compactDatabaseString(`${source}-other`, 32));
});

test("stable digest ignores object key order but preserves data differences", () => {
  assert.equal(stableDigest({ b: 2, a: { y: 2, x: 1 } }), stableDigest({ a: { x: 1, y: 2 }, b: 2 }));
  assert.notEqual(stableDigest({ value: "A" }), stableDigest({ value: "B" }));
});

test("all exported diagnostics have an existing MongoDB document destination", () => {
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
    _trace: { schemaVersion: "1.0", payload: {}, fieldMeta: { site_parser: "v1" } },
  };

  assert.equal(buildExtractedFields(source)["公告内容"], "需要保留的更正内容");
  const metadata = exportMetadata({ source });
  assert.equal(metadata.noticeSubtype, "gzjg");
  assert.equal(metadata.attachments[0].source_file_id, "f-1");
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
