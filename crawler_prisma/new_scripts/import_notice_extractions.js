#!/usr/bin/env node

"use strict";

const { randomUUID } = require("node:crypto");
const {
  assertMaxLength,
  booleanValue,
  deduplicate,
  nullableString,
  openStores,
  parseCommonArgs,
  readJsonNotices,
  requiredString,
  resolveDataSources,
  traceEnvelope,
} = require("./lib/runtime");

const SUBTYPE_TO_NOTICE_TYPE = Object.freeze({
  zbjh: "招标计划",
  zbys: "资格预审公告",
  zbgg: "招标公告",
  cggg: "采购公告",
  hxr: "中标候选人公示",
  cjhxr: "成交候选人公示",
  dbhxr: "定标候选人公示",
  zbjg: "中标结果公示",
  cjgg: "成交公告",
  gzjg: "更正结果公示",
  zzgg: "终止公告",
  htly: "合同与履约",
});

const FALLBACK_NOTICE_TYPES = Object.freeze({
  PLAN: "招标计划",
  PREQUALIFICATION: "资格预审公告",
  TENDER: "招标公告",
  CANDIDATE: "中标候选人公示",
  FINAL_CANDIDATE: "定标候选人公示",
  AWARD: "中标结果公示",
  CORRECTION: "更正结果公示",
  TERMINATION: "终止公告",
  CONTRACT: "合同与履约",
});

const NON_EXTRACTION_FIELDS = new Set([
  "平台名称", "平台代码", "公告ID", "公告类型", "公告子类型", "公告标题",
  "发布时间", "公告正文", "解析状态", "内容指纹", "抽取方式",
  "抽取版本", "是否已核验", "爬虫时间", "详情页链接", "HTML快照路径",
  "HTML快照SHA256", "附件", "缺失字段",
  "_trace",
]);

function printHelp() {
  console.log(`Usage:
  node import_notice_extractions.js [options]

Options:
  --commit                 Write extraction details to MongoDB and indexes to MySQL.
  --site=<site>            all, huaxin, jiubang, sxjm, or sxzwfw (default: all).
  --output-root=<path>     Crawler output root.
  --api-root=<path>        ProjectRecommendationSystem/api directory.
  --help                   Show this help.

Run import_raw_notices.js first. Existing project_notice and verification links
are preserved when an extraction is updated.
`);
}

function resolveNoticeType(source, context) {
  const rawType = requiredString(source["公告类型"], "公告类型", context);
  const fallbackType = FALLBACK_NOTICE_TYPES[rawType.toUpperCase()] || rawType;
  // sxjm 偶尔把撤销/终止公告放在普通采购栏目。导出器已按完整标题将
  // 公告类型纠正为 TERMINATION，此时应优先于栏目子类型，避免再次映射回采购公告。
  if (rawType.toUpperCase() === "TERMINATION") return fallbackType;
  const subtype = nullableString(source["公告子类型"]);
  const subtypeSuffix = subtype?.toLowerCase().split(".").at(-1);
  if (subtypeSuffix && SUBTYPE_TO_NOTICE_TYPE[subtypeSuffix]) {
    return SUBTYPE_TO_NOTICE_TYPE[subtypeSuffix];
  }
  return fallbackType;
}

function buildExtractedFields(source) {
  return Object.fromEntries(
    Object.entries(source).filter(([key]) => !NON_EXTRACTION_FIELDS.has(key)),
  );
}

function buildEvidence(row) {
  const existing = row.trace?.fieldMeta?.evidence;
  const evidence = Array.isArray(existing) ? [...existing] : [];
  evidence.push({
    type: "CRAWLER_DIAGNOSTICS",
    noticeSubtype: nullableString(row.source["公告子类型"]),
    missingFields: Array.isArray(row.source["缺失字段"])
      ? row.source["缺失字段"]
      : [],
    fieldMeta: row.trace?.fieldMeta || {},
  });
  return evidence;
}

function mapRecord(record) {
  const { source, context } = record;
  const noticeType = resolveNoticeType(source, context);
  const extractionModel = requiredString(source["抽取方式"], "抽取方式", context);
  const extractionVersion = requiredString(source["抽取版本"], "抽取版本", context);
  const sourceNoticeId = requiredString(source["公告ID"], "公告ID", context);
  const trace = traceEnvelope(source, context);
  assertMaxLength(noticeType, 64, "notice_type", context);
  assertMaxLength(extractionModel, 64, "extraction_model", context);
  assertMaxLength(extractionVersion, 32, "extraction_version", context);
  assertMaxLength(sourceNoticeId, 256, "source_notice_id", context);
  return {
    ...record,
    sourceNoticeId,
    trace,
    noticeType,
    extractionModel,
    extractionVersion,
    extractedFields: buildExtractedFields(source),
    isVerified: booleanValue(source["是否已核验"], "是否已核验", context),
    sourceTextSnippet: nullableString(source["公告正文"] || source["公告内容"])?.slice(0, 4000) || null,
  };
}

function validObjectId(ObjectId, value) {
  return typeof value === "string" && ObjectId.isValid(value) && new ObjectId(value).toHexString() === value;
}

async function importOne(stores, row, parent) {
  const { prisma, mongo, ObjectId } = stores;
  const collection = mongo.collection("notice_extractions");
  const rawCollection = mongo.collection("raw_notices");
  const existing = await prisma.noticeExtraction.findFirst({
    where: {
      rawNoticeId: parent.id,
      extractionModel: row.extractionModel,
      extractionVersion: row.extractionVersion,
    },
  });
  const reuseDocument = Boolean(existing && validObjectId(ObjectId, existing.mongoDocumentId));
  const mongoId = reuseDocument ? new ObjectId(existing.mongoDocumentId) : new ObjectId();
  const previousDocument = reuseDocument ? await collection.findOne({ _id: mongoId }) : null;
  const rawDocument = validObjectId(ObjectId, parent.mongoDocumentId)
    ? await rawCollection.findOne({ _id: new ObjectId(parent.mongoDocumentId) }, { projection: { documentUid: 1 } })
    : null;
  const uid = existing?.uid || randomUUID();
  const document = {
    _id: mongoId,
    documentUid: previousDocument?.documentUid || randomUUID(),
    extractionUid: uid,
    rawNoticeUid: parent.uid,
    mysqlExtractionId: existing?.id?.toString() || null,
    projectNoticeId: existing?.projectNoticeId || null,
    noticeType: row.noticeType,
    extractedFields: row.extractedFields,
    fieldConfidences: row.trace?.fieldMeta?.fieldConfidences || null,
    evidence: buildEvidence(row),
    sourceTextSnippet: row.sourceTextSnippet,
    extractionModel: row.extractionModel,
    extractionVersion: row.extractionVersion,
    promptVersion: null,
    inputDocumentUid: rawDocument?.documentUid || null,
    tokenUsage: null,
    durationMs: null,
    createdAt: previousDocument?.createdAt || new Date(),
  };

  if (reuseDocument) await collection.replaceOne({ _id: mongoId }, document, { upsert: true });
  else await collection.insertOne(document);

  let stored;
  try {
    const data = {
      uid,
      rawNoticeId: parent.id,
      noticeType: row.noticeType,
      extractionModel: row.extractionModel,
      extractionVersion: row.extractionVersion,
      mongoDocumentId: mongoId.toHexString(),
      isVerified: existing?.isVerified || row.isVerified,
    };
    stored = existing
      ? await prisma.noticeExtraction.update({ where: { id: existing.id }, data })
      : await prisma.noticeExtraction.create({ data });
  } catch (error) {
    if (reuseDocument && previousDocument) {
      await collection.replaceOne({ _id: mongoId }, previousDocument, { upsert: true });
    } else {
      await collection.deleteOne({ _id: mongoId });
    }
    throw error;
  }
  await collection.updateOne(
    { _id: mongoId },
    { $set: { mysqlExtractionId: stored.id.toString(), projectNoticeId: stored.projectNoticeId } },
  );
  return existing ? "updated" : "inserted";
}

async function main() {
  const options = parseCommonArgs(process.argv.slice(2));
  if (options.help) return printHelp();
  const loaded = readJsonNotices(options.outputRoot, options.sites);
  const mapped = loaded.records.map(mapRecord);
  const deduplicated = deduplicate(
    mapped,
    (row) => `${row.site}\u0000${row.sourceNoticeId}\u0000${row.extractionModel}\u0000${row.extractionVersion}`,
  );
  console.log(`Mode: ${options.commit ? "COMMIT" : "DRY RUN (no storage writes)"}`);
  console.log(`Validated JSON files: ${loaded.jsonFileCount}`);
  console.log(`Validated extractions: ${deduplicated.records.length}`);
  console.log(`Duplicate JSON extractions skipped: ${deduplicated.duplicateCount}`);
  const typeCounts = new Map();
  for (const row of deduplicated.records) typeCounts.set(row.noticeType, (typeCounts.get(row.noticeType) || 0) + 1);
  for (const [type, count] of [...typeCounts].sort()) console.log(`  ${type}: ${count}`);
  if (!options.commit) return console.log("Dry run complete. Add --commit to write MongoDB and MySQL.");

  const stores = await openStores(options.apiRoot, { mongo: true });
  try {
    const dataSources = await resolveDataSources(stores.prisma, options.sites);
    const parentCache = new Map();
    const counts = { inserted: 0, updated: 0 };
    for (let index = 0; index < deduplicated.records.length; index += 1) {
      const row = deduplicated.records[index];
      const dataSourceId = dataSources.get(row.site).id;
      const parentKey = `${dataSourceId}\u0000${row.sourceNoticeId}`;
      let parent = parentCache.get(parentKey);
      if (!parent) {
        parent = await stores.prisma.rawNotice.findFirst({
          where: { dataSourceId, sourceNoticeId: row.sourceNoticeId },
        });
        if (!parent) throw new Error(`${row.context}: parent raw_notice not found; run import_raw_notices.js first`);
        parentCache.set(parentKey, parent);
      }
      const result = await importOne(stores, row, parent);
      counts[result] += 1;
      if ((index + 1) % 100 === 0 || index + 1 === deduplicated.records.length) {
        console.log(`  Processed ${index + 1}/${deduplicated.records.length}`);
      }
    }
    console.log(`Commit completed: inserted=${counts.inserted}, updated=${counts.updated}.`);
  } finally {
    await stores.close();
  }
}

if (require.main === module) {
  main().catch((error) => {
    console.error(`Import failed: ${error.stack || error.message}`);
    process.exitCode = 1;
  });
}

module.exports = {
  buildEvidence,
  buildExtractedFields,
  mapRecord,
  resolveNoticeType,
};
