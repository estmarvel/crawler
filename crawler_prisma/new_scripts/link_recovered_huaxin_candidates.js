#!/usr/bin/env node

"use strict";

const fs = require("node:fs");
const { openStores } = require("./lib/runtime");

const API_ROOT = "/home/intsig/ProjectRecommendationSystem/api";
const DEFAULT_BUNDLE = "/home/intsig/backups/huaxin_missing_recovery_2026-07-29.json";
const STATUS_RANK = new Map([
  ["PLAN", 0], ["PREQUALIFICATION", 1], ["TENDER", 2], ["EVALUATING", 3],
  ["CANDIDATE", 4], ["FINAL_CANDIDATE", 5], ["AWARD", 6], ["CONTRACT", 7],
]);

function parseArgs(argv) {
  const options = { commit: false, bundle: DEFAULT_BUNDLE };
  for (const arg of argv) {
    if (arg === "--commit") options.commit = true;
    else if (arg.startsWith("--bundle=")) options.bundle = arg.slice(9);
    else if (arg === "--help" || arg === "-h") options.help = true;
    else throw new Error(`Unknown argument: ${arg}`);
  }
  return options;
}

function loadBundle(file) {
  const bundle = JSON.parse(fs.readFileSync(file, "utf8"));
  if (bundle.schemaVersion !== 1 || !Array.isArray(bundle.records) || bundle.records.length < 1) {
    throw new Error(`Invalid recovery bundle: ${file}`);
  }
  return bundle.records;
}

async function loadPlans(stores, records) {
  const plans = [];
  for (const material of records) {
    const raw = await stores.prisma.rawNotice.findFirst({
      where: { dataSourceId: 6, sourceNoticeId: material.sourceNoticeId },
      include: { dataSource: true, extractionResults: true },
    });
    if (!raw) throw new Error(`${material.sourceNoticeId}: raw_notice not found`);
    if (raw.title !== material.title) throw new Error(`${material.sourceNoticeId}: MySQL title mismatch`);
    const extraction = raw.extractionResults.find((row) => row.noticeType === material.noticeType);
    if (!extraction) throw new Error(`${material.sourceNoticeId}: notice_extraction not found`);
    if (!raw.mongoDocumentId || !stores.ObjectId.isValid(raw.mongoDocumentId)) {
      throw new Error(`${material.sourceNoticeId}: raw Mongo pointer is missing; run repair:missing-mongo first`);
    }
    if (!stores.ObjectId.isValid(extraction.mongoDocumentId)) {
      throw new Error(`${material.sourceNoticeId}: extraction Mongo pointer is invalid`);
    }
    const [rawDocument, extractionDocument] = await Promise.all([
      stores.mongo.collection("raw_notices").findOne({ _id: new stores.ObjectId(raw.mongoDocumentId) }),
      stores.mongo.collection("notice_extractions").findOne({ _id: new stores.ObjectId(extraction.mongoDocumentId) }),
    ]);
    if (!rawDocument || !extractionDocument) {
      throw new Error(`${material.sourceNoticeId}: recovered Mongo documents are missing`);
    }
    const project = await stores.prisma.project.findUnique({
      where: { projectCode: material.expectedProjectCode },
    });
    if (!project) throw new Error(`${material.sourceNoticeId}: project ${material.expectedProjectCode} not found`);
    if (project.projectName !== material.expectedProjectName) {
      throw new Error(`${material.sourceNoticeId}: project name mismatch for ${project.projectCode}`);
    }
    const existingNotice = await stores.prisma.projectNotice.findFirst({
      where: { sourceSite: raw.dataSource.name, sourceNoticeId: raw.sourceNoticeId },
    });
    if (extraction.projectNoticeId && existingNotice?.id !== extraction.projectNoticeId) {
      throw new Error(`${material.sourceNoticeId}: conflicting project_notice link`);
    }
    plans.push({ material, raw, extraction, project, existingNotice });
  }
  return plans;
}

async function commitPlan(stores, plan) {
  const { material, raw, extraction, project } = plan;
  const desiredStatus = material.desiredProjectStatus;
  if (!STATUS_RANK.has(desiredStatus)) {
    throw new Error(`${material.sourceNoticeId}: unsupported desired project status ${desiredStatus}`);
  }
  const currentRank = STATUS_RANK.get(project.currentStatus);
  const desiredRank = STATUS_RANK.get(desiredStatus);
  const nextStatus = currentRank === undefined || currentRank < desiredRank
    ? desiredStatus
    : project.currentStatus;

  const result = await stores.prisma.$transaction(async (tx) => {
    const data = {
      projectId: project.id,
      noticeType: material.noticeType,
      title: raw.title,
      content: material.rawText,
      structuredData: material.extractedFields,
      publishDate: raw.publishDate,
      sourceSite: raw.dataSource.name,
      sourceUrl: raw.sourceUrl,
      sourceNoticeId: raw.sourceNoticeId,
      crawlTime: raw.crawlTime,
    };
    const existing = await tx.projectNotice.findFirst({
      where: { sourceSite: data.sourceSite, sourceNoticeId: data.sourceNoticeId },
    });
    const notice = existing
      ? await tx.projectNotice.update({ where: { id: existing.id }, data })
      : await tx.projectNotice.create({ data });
    await tx.noticeExtraction.update({
      where: { id: extraction.id },
      data: { projectNoticeId: notice.id },
    });
    if (nextStatus !== project.currentStatus) {
      await tx.project.update({ where: { id: project.id }, data: { currentStatus: nextStatus } });
    }
    return { notice, inserted: !existing, nextStatus };
  }, { maxWait: 10_000, timeout: 60_000 });

  await stores.mongo.collection("notice_extractions").updateOne(
    { _id: new stores.ObjectId(extraction.mongoDocumentId) },
    { $set: { projectNoticeId: result.notice.id } },
  );
  return {
    sourceNoticeId: material.sourceNoticeId,
    projectCode: project.projectCode,
    projectNoticeId: result.notice.id,
    action: result.inserted ? "inserted" : "updated",
    status: result.nextStatus,
  };
}

async function main() {
  const options = parseArgs(process.argv.slice(2));
  if (options.help) {
    console.log("Usage: node link_recovered_huaxin_candidates.js [--commit] [--bundle=<path>]");
    return;
  }
  const records = loadBundle(options.bundle);
  const stores = await openStores(API_ROOT, { mongo: true });
  try {
    const plans = await loadPlans(stores, records);
    console.log(`Mode: ${options.commit ? "COMMIT" : "DRY RUN"}`);
    for (const plan of plans) {
      console.log(JSON.stringify({
        sourceNoticeId: plan.material.sourceNoticeId,
        extractionId: plan.extraction.id.toString(),
        projectCode: plan.project.projectCode,
        currentStatus: plan.project.currentStatus,
        desiredStatus: plan.material.desiredProjectStatus,
        existingProjectNoticeId: plan.existingNotice?.id || null,
      }));
    }
    if (!options.commit) return;
    for (const plan of plans) console.log("linked", await commitPlan(stores, plan));
  } finally {
    await stores.close();
  }
}

main().catch((error) => {
  console.error(error.stack || error.message);
  process.exitCode = 1;
});
