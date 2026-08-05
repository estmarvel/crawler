#!/usr/bin/env node

"use strict";

const fs = require("node:fs");
const { randomUUID } = require("node:crypto");
const { openStores } = require("./lib/runtime");

const API_ROOT = "/home/intsig/ProjectRecommendationSystem/api";
const OLD_EXPORT = "/home/intsig/backups/missing_from_old_mysql_utf8_2026-07-29.jsonl";
const HUAXIN_RECOVERY_BUNDLE = "/home/intsig/backups/huaxin_missing_recovery_2026-07-29.json";
const FORMAL_RESULT_EXTRACTION_IDS = [3846n, 3847n, 3848n, 3849n];

function parseArgs(argv) {
  const options = { commit: false };
  for (const arg of argv) {
    if (arg === "--commit") options.commit = true;
    else if (arg === "--help" || arg === "-h") options.help = true;
    else throw new Error(`Unknown argument: ${arg}`);
  }
  return options;
}

function oldMaterial() {
  const line = fs.readFileSync(OLD_EXPORT, "utf8").trim();
  if (!line) throw new Error(`Recovery export is empty: ${OLD_EXPORT}`);
  const source = JSON.parse(line);
  return {
    sourceNoticeId: source.sourceNoticeId,
    rawHtml: source.rawHtml || null,
    rawText: source.rawText || null,
    extractedFields: source.extractedFields || {},
    fieldConfidences: source.fieldConfidences || null,
    sourceTextSnippet: source.sourceTextSnippet || source.rawText?.slice(0, 4000) || null,
    recoverySource: "old-docker-mysql",
  };
}

function huaxinRecoveryMaterials() {
  if (!fs.existsSync(HUAXIN_RECOVERY_BUNDLE)) return [];
  const bundle = JSON.parse(fs.readFileSync(HUAXIN_RECOVERY_BUNDLE, "utf8"));
  if (bundle.schemaVersion !== 1 || !Array.isArray(bundle.records) || bundle.records.length < 1) {
    throw new Error(`Invalid Huaxin recovery bundle: ${HUAXIN_RECOVERY_BUNDLE}`);
  }
  return bundle.records;
}

async function loadCandidates(stores) {
  const old = oldMaterial();
  const huaxinMaterials = huaxinRecoveryMaterials();
  const huaxinBySourceId = new Map(
    huaxinMaterials.map((material) => [material.sourceNoticeId, material]),
  );
  const rows = await stores.prisma.noticeExtraction.findMany({
    where: {
      OR: [
        { rawNotice: { sourceNoticeId: old.sourceNoticeId, dataSourceId: 6 } },
        { id: { in: FORMAL_RESULT_EXTRACTION_IDS } },
        ...(huaxinMaterials.length
          ? [{ rawNotice: { sourceNoticeId: { in: [...huaxinBySourceId.keys()] }, dataSourceId: 6 } }]
          : []),
      ],
    },
    include: { rawNotice: true, projectNotice: true },
    orderBy: { id: "asc" },
  });
  const expectedRows = 5 + huaxinMaterials.length;
  if (rows.length !== expectedRows) {
    throw new Error(`Expected ${expectedRows} recoverable rows, found ${rows.length}`);
  }
  return rows.map((row) => {
    const material = huaxinBySourceId.get(row.rawNotice.sourceNoticeId)
      || (row.rawNotice.sourceNoticeId === old.sourceNoticeId
        ? old
        : {
          sourceNoticeId: row.rawNotice.sourceNoticeId,
          rawHtml: null,
          rawText: row.projectNotice?.content || null,
          extractedFields: row.projectNotice?.structuredData || {},
          fieldConfidences: null,
          sourceTextSnippet: row.projectNotice?.content?.slice(0, 4000) || null,
          recoverySource: "mysql-project-notice-formal-result",
        });
    if (material.title && material.title !== row.rawNotice.title) {
      throw new Error(`extraction.id=${row.id}: recovery title mismatch`);
    }
    if (!material.rawText && Object.keys(material.extractedFields).length === 0) {
      throw new Error(`extraction.id=${row.id}: no recoverable content`);
    }
    return { row, material };
  });
}

async function repairOne(stores, candidate) {
  const { row, material } = candidate;
  const raw = row.rawNotice;
  const rawCollection = stores.mongo.collection("raw_notices");
  const extractionCollection = stores.mongo.collection("notice_extractions");
  let rawId = raw.mongoDocumentId && stores.ObjectId.isValid(raw.mongoDocumentId)
    ? new stores.ObjectId(raw.mongoDocumentId)
    : new stores.ObjectId();
  let rawDocument = await rawCollection.findOne({ _id: rawId });
  let insertedRaw = false;
  let insertedExtraction = false;
  const previousRawPointer = raw.mongoDocumentId;

  try {
    if (!rawDocument) {
      const documentUid = randomUUID();
      rawDocument = {
        _id: rawId,
        documentUid,
        rawNoticeUid: raw.uid,
        mysqlRawNoticeId: raw.id.toString(),
        dataSourceId: raw.dataSourceId,
        crawlTaskId: raw.crawlTaskId?.toString() || null,
        sourceNoticeId: raw.sourceNoticeId,
        sourceUrl: raw.sourceUrl,
        title: raw.title,
        contentVersion: raw.contentVersion,
        payload: material.sourcePayload || {
          ...material.extractedFields,
          公告ID: raw.sourceNoticeId,
          公告标题: raw.title,
          详情页链接: raw.sourceUrl,
          公告正文: material.rawText,
        },
        rawHtml: material.rawHtml,
        rawText: material.rawText,
        responseMetadata: {
          recoveredAt: new Date(),
          recoveredFrom: material.recoverySource,
          recoveryScript: "repair_missing_mongo_documents.js",
        },
        fingerprint: raw.fingerprint,
        crawlerVersion: null,
        crawledAt: raw.crawlTime,
        createdAt: raw.createdAt,
      };
      await rawCollection.insertOne(rawDocument);
      insertedRaw = true;
    }

    if (raw.mongoDocumentId !== rawId.toHexString()) {
      await stores.prisma.rawNotice.update({
        where: { id: raw.id },
        data: { mongoDocumentId: rawId.toHexString() },
      });
    }

    if (!stores.ObjectId.isValid(row.mongoDocumentId)) {
      throw new Error(`extraction.id=${row.id}: invalid mongo_document_id`);
    }
    const extractionId = new stores.ObjectId(row.mongoDocumentId);
    const existingExtraction = await extractionCollection.findOne({ _id: extractionId });
    if (!existingExtraction) {
      await extractionCollection.insertOne({
        _id: extractionId,
        documentUid: randomUUID(),
        extractionUid: row.uid,
        rawNoticeUid: raw.uid,
        mysqlExtractionId: row.id.toString(),
        projectNoticeId: row.projectNoticeId,
        noticeType: row.noticeType,
        extractedFields: material.extractedFields,
        fieldConfidences: material.fieldConfidences,
        evidence: null,
        sourceTextSnippet: material.sourceTextSnippet,
        extractionModel: row.extractionModel,
        extractionVersion: row.extractionVersion,
        promptVersion: null,
        inputDocumentUid: rawDocument.documentUid,
        tokenUsage: null,
        durationMs: null,
        createdAt: row.createdAt,
      });
      insertedExtraction = true;
    }
    return { rawNoticeId: raw.id.toString(), extractionId: row.id.toString(), sourceNoticeId: raw.sourceNoticeId };
  } catch (error) {
    if (insertedExtraction) await extractionCollection.deleteOne({ _id: new stores.ObjectId(row.mongoDocumentId) }).catch(() => {});
    if (insertedRaw) await rawCollection.deleteOne({ _id: rawId }).catch(() => {});
    if (previousRawPointer !== rawId.toHexString()) {
      await stores.prisma.rawNotice.update({
        where: { id: raw.id },
        data: { mongoDocumentId: previousRawPointer },
      }).catch(() => {});
    }
    throw error;
  }
}

async function main() {
  const options = parseArgs(process.argv.slice(2));
  if (options.help) {
    console.log("Usage: node repair_missing_mongo_documents.js [--commit]");
    return;
  }
  const stores = await openStores(API_ROOT, { mongo: true });
  try {
    const candidates = await loadCandidates(stores);
    console.log(`Mode: ${options.commit ? "COMMIT" : "DRY RUN"}`);
    for (const { row, material } of candidates) {
      console.log(`${row.id}: ${row.rawNotice.sourceNoticeId} <- ${material.recoverySource}`);
    }
    if (!options.commit) return;
    for (const candidate of candidates) {
      console.log("repaired", await repairOne(stores, candidate));
    }
  } finally {
    await stores.close();
  }
}

main().catch((error) => {
  console.error(error.stack || error.message);
  process.exitCode = 1;
});
