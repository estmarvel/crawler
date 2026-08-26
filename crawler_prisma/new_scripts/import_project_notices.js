#!/usr/bin/env node

"use strict";

const {
  iterateJsonNotices,
  openStores,
  parseCommonArgs,
  resolveDataSources,
} = require("./lib/runtime");
const {
  businessRecordDigest,
  isBusinessReady,
  loadBusinessDataset,
  mapBusinessRecord,
} = require("./lib/business");

function printHelp() {
  console.log(`Usage:
  node import_project_notices.js [options]

Options:
  --commit                 Upsert project_notice and extraction links.
  --site=<site>            all or any configured crawler site.
  --output-root=<path>     Crawler new_output root.
  --env-file=<path>        crawler_prisma environment file (default: .env).
  --help                   Show this help.

Run raw-notice, extraction, and project import stages first. Notice types are
stored with the current database's Chinese values, not crawler transport codes.
`);
}

function chunks(values, size = 500) {
  const result = [];
  for (let index = 0; index < values.length; index += size) {
    result.push(values.slice(index, index + size));
  }
  return result;
}

function chooseExtraction(rawNotice, row) {
  const exact = rawNotice.extractionResults.find(
    (candidate) => candidate.extractionModel === row.extractionModel
      && candidate.extractionVersion === row.extractionVersion,
  );
  if (exact) return exact;
  return [...rawNotice.extractionResults].sort(
    (left, right) => right.updatedAt.getTime() - left.updatedAt.getTime(),
  )[0] || null;
}

async function loadRawNoticeIndex(prisma, records, dataSources) {
  const result = new Map();
  for (const [site, dataSource] of dataSources) {
    const ids = [...new Set(
      records.filter((row) => row.site === site).map((row) => row.sourceNoticeId),
    )];
    for (const batch of chunks(ids)) {
      const rows = await prisma.rawNotice.findMany({
        where: { dataSourceId: dataSource.id, sourceNoticeId: { in: batch } },
        include: { extractionResults: true },
      });
      for (const row of rows) result.set(`${site}\u0000${row.sourceNoticeId}`, row);
    }
  }
  return result;
}

async function loadProjectIndex(prisma, projects) {
  const codes = [...new Set(projects.flatMap((project) => [project.data.projectCode, ...project.aliases]).filter(Boolean))];
  const names = [...new Set(
    projects.filter((project) => project.data.projectCode === null).map((project) => project.data.projectName),
  )];
  const databaseProjects = [];
  for (const batch of chunks(codes)) {
    databaseProjects.push(...await prisma.project.findMany({ where: { projectCode: { in: batch } } }));
  }
  for (const batch of chunks(names)) {
    databaseProjects.push(...await prisma.project.findMany({ where: { projectName: { in: batch } } }));
  }
  const byCode = new Map();
  const byName = new Map();
  for (const project of databaseProjects) {
    if (project.projectCode) byCode.set(project.projectCode, project);
    if (!byName.has(project.projectName)) byName.set(project.projectName, []);
    if (!byName.get(project.projectName).some((row) => row.id === project.id)) {
      byName.get(project.projectName).push(project);
    }
  }
  const result = new Map();
  for (const project of projects) {
    const matches = [...new Set(
      [project.data.projectCode, ...project.aliases]
        .filter(Boolean)
        .map((code) => byCode.get(code))
        .filter(Boolean),
    )];
    if (matches.length > 1) {
      throw new Error(`${project.groupKey}: identity maps to multiple project rows`);
    }
    const existing = matches[0]
      || (project.data.projectCode === null && (byName.get(project.data.projectName) || []).length === 1
        ? byName.get(project.data.projectName)[0]
        : null);
    if (!existing) {
      throw new Error(`${project.groupKey}: project not found; run import_projects.js --commit first`);
    }
    result.set(project.groupKey, existing);
  }
  return result;
}

function buildRows(dataset, dataSources, rawIndex, projectIndex) {
  return dataset.records.map((row) => {
    const rawNotice = rawIndex.get(`${row.site}\u0000${row.sourceNoticeId}`);
    if (!rawNotice) {
      throw new Error(`${row.context}: raw_notice not found; run import_raw_notices.js --commit first`);
    }
    const extraction = chooseExtraction(rawNotice, row);
    if (!extraction) {
      throw new Error(`${row.context}: notice_extraction not found; run import_notice_extractions.js --commit first`);
    }
    const groupKey = dataset.recordGroupKeys.get(`${row.site}\u0000${row.sourceNoticeId}`);
    const project = projectIndex.get(groupKey);
    if (!project) throw new Error(`${row.context}: project group ${groupKey} is not in the database`);
    return {
      site: row.site,
      extractionId: extraction.id,
      mongoDocumentId: extraction.mongoDocumentId,
      data: {
        projectId: project.id,
        noticeType: row.noticeType,
        title: [...row.title].slice(0, 512).join(""),
        content: row.content,
        structuredData: row.fields,
        publishDate: row.publishDate,
        sourceSite: dataSources.get(row.site).name,
        sourceUrl: row.sourceUrl,
        sourceNoticeId: row.sourceNoticeId,
        crawlTime: row.crawlTime,
      },
    };
  });
}

async function commitRows(stores, rows) {
  const result = { inserted: 0, updated: 0 };
  let processed = 0;
  for (const batch of chunks(rows, 200)) {
    await stores.prisma.$transaction(async (transaction) => {
      for (const row of batch) {
        const existing = await transaction.projectNotice.findFirst({
          where: {
            sourceSite: row.data.sourceSite,
            sourceNoticeId: row.data.sourceNoticeId,
          },
        });
        const stored = existing
          ? await transaction.projectNotice.update({ where: { id: existing.id }, data: row.data })
          : await transaction.projectNotice.create({ data: row.data });
        if (existing) result.updated += 1;
        else result.inserted += 1;
        row.projectNoticeId = stored.id;
        await transaction.noticeExtraction.update({
          where: { id: row.extractionId },
          data: { projectNoticeId: stored.id },
        });
      }
    }, { maxWait: 10_000, timeout: 600_000 });

    const operations = batch
      .filter((row) => typeof row.mongoDocumentId === "string" && stores.ObjectId.isValid(row.mongoDocumentId))
      .map((row) => ({
        updateOne: {
          filter: { _id: new stores.ObjectId(row.mongoDocumentId) },
          update: { $set: { projectNoticeId: row.projectNoticeId } },
        },
      }));
    if (operations.length) {
      await stores.mongo.collection("notice_extractions").bulkWrite(operations, { ordered: false });
    }
    processed += batch.length;
    console.log(`  Processed ${processed}/${rows.length}`);
  }
  return result;
}

async function main() {
  const options = parseCommonArgs(process.argv.slice(2));
  if (options.help) return printHelp();
  const dataset = await loadBusinessDataset(options.outputRoot, options.sites, {
    includeContent: false,
    fieldMode: "project",
  });
  console.log(`Mode: ${options.commit ? "COMMIT" : "DRY RUN (no database writes)"}`);
  console.log(`Validated notices: ${dataset.records.length}`);
  console.log(`Non-PARSED notices kept only in raw/extraction storage: ${dataset.skippedNonParsedCount}`);
  console.log(`Resolved projects: ${dataset.projects.length}`);
  const typeCounts = new Map();
  for (const row of dataset.records) {
    typeCounts.set(row.noticeType, (typeCounts.get(row.noticeType) || 0) + 1);
  }
  for (const [type, count] of [...typeCounts].sort()) console.log(`  ${type}: ${count}`);
  if (!options.commit) return console.log("Dry run complete. Add --commit after earlier stages succeed.");

  const stores = await openStores(options.envFile, { mongo: true });
  try {
    const dataSources = await resolveDataSources(stores.prisma, options.sites);
    const projectIndex = await loadProjectIndex(stores.prisma, dataset.projects);
    const totals = { inserted: 0, updated: 0, links: 0 };
    let batch = [];
    const processedIdentities = new Map();
    async function flush() {
      if (!batch.length) return;
      const rawIndex = await loadRawNoticeIndex(stores.prisma, batch, dataSources);
      const rows = buildRows({ ...dataset, records: batch }, dataSources, rawIndex, projectIndex);
      const result = await commitRows(stores, rows);
      totals.inserted += result.inserted;
      totals.updated += result.updated;
      totals.links += rows.length;
      batch = [];
    }
    for await (const record of iterateJsonNotices(options.outputRoot, options.sites)) {
      if (!isBusinessReady(record)) continue;
      const row = mapBusinessRecord(record);
      const identity = `${row.site}\u0000${row.sourceNoticeId}`;
      const digest = businessRecordDigest(row);
      const previous = processedIdentities.get(identity);
      // loadBusinessDataset 已经验证相同身份只能是内容完全一致的重复项；
      // 正式写入只处理一次，避免同一批数据重复更新关联和 Mongo 文档。
      if (previous) {
        if (previous.digest !== digest) {
          throw new Error(`${row.context}: conflicting duplicate 公告ID=${row.sourceNoticeId}`);
        }
        continue;
      }
      // row.content 可能是完整正文，去重表只保留摘要，批次写入后即可释放正文。
      processedIdentities.set(identity, { digest, context: row.context });
      batch.push(row);
      if (batch.length >= 200) await flush();
    }
    await flush();
    console.log(`Commit completed: inserted=${totals.inserted}, updated=${totals.updated}, links=${totals.links}.`);
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
  buildRows,
  chooseExtraction,
  commitRows,
  loadProjectIndex,
  loadRawNoticeIndex,
};
