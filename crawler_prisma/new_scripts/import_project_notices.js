#!/usr/bin/env node

"use strict";

const path = require("node:path");
const { DEFAULT_API_ROOT, DEFAULT_OUTPUT_ROOT, openStores, resolveDataSources } = require("./lib/runtime");
const { chunks, hydrateMappings, projectLookupKey, readMappings } = require("./lib/derived");

function parseArgs(argv) {
  const options = {
    commit: false,
    apiRoot: process.env.PROJECT_RECOMMENDATION_API_ROOT || DEFAULT_API_ROOT,
    mappingRoot: path.join(DEFAULT_OUTPUT_ROOT, "project_identity_mapping"),
  };
  for (const arg of argv) {
    if (arg === "--commit") options.commit = true;
    else if (arg === "--help" || arg === "-h") options.help = true;
    else if (arg.startsWith("--api-root=")) options.apiRoot = path.resolve(arg.slice(11));
    else if (arg.startsWith("--mapping-root=")) options.mappingRoot = path.resolve(arg.slice(15));
    else throw new Error(`Unknown argument: ${arg}`);
  }
  return options;
}

function printHelp() {
  console.log(`Usage:
  node import_project_notices.js [--commit] [--api-root=<path>] [--mapping-root=<path>]

Run import_projects.js first. MongoDB rawText/extractedFields become formal
MySQL project_notice content/structured_data, and extraction links are backfilled.
`);
}

async function loadProjectIndex(prisma, hydrated) {
  const codes = [...new Set(hydrated.map((row) => row.mapping.projectCode).filter(Boolean))];
  const standaloneNames = [...new Set(hydrated.map((row) => row.mapping.standaloneProjectName).filter(Boolean))];
  const projects = await prisma.project.findMany({
    where: {
      OR: [
        { projectCode: { in: codes } },
        { projectCode: null, projectName: { in: standaloneNames } },
      ],
    },
  });
  const index = new Map();
  for (const project of projects) {
    if (project.projectCode) {
      index.set(projectLookupKey(project.projectCode, null, null), project);
    } else {
      const matchingSites = hydrated
        .filter((row) => !row.mapping.projectCode && row.mapping.standaloneProjectName === project.projectName)
        .map((row) => row.mapping.site);
      for (const site of new Set(matchingSites)) {
        const key = projectLookupKey(null, site, project.projectName);
        if (index.has(key)) throw new Error(`Ambiguous standalone project: ${site}/${project.projectName}`);
        index.set(key, project);
      }
    }
  }
  return index;
}

async function commitRows(stores, rows) {
  const { prisma } = stores;
  let inserted = 0;
  let updated = 0;
  await prisma.$transaction(async (transaction) => {
    for (const batch of chunks(rows, 200)) {
      for (const row of batch) {
        const existing = await transaction.projectNotice.findFirst({
          where: { sourceSite: row.data.sourceSite, sourceNoticeId: row.data.sourceNoticeId },
        });
        const stored = existing
          ? await transaction.projectNotice.update({ where: { id: existing.id }, data: row.data })
          : await transaction.projectNotice.create({ data: row.data });
        if (existing) updated += 1;
        else inserted += 1;
        row.projectNoticeId = stored.id;
        await transaction.noticeExtraction.update({
          where: { id: row.extractionId },
          data: { projectNoticeId: stored.id },
        });
      }
    }
  }, { maxWait: 10_000, timeout: 600_000 });

  const mongoOperations = rows.map((row) => ({
    updateOne: {
      filter: { _id: new stores.ObjectId(row.mongoDocumentId) },
      update: { $set: { projectNoticeId: row.projectNoticeId } },
    },
  }));
  for (const batch of chunks(mongoOperations, 500)) {
    await stores.mongo.collection("notice_extractions").bulkWrite(batch, { ordered: false });
  }
  return { inserted, updated };
}

async function main() {
  const options = parseArgs(process.argv.slice(2));
  if (options.help) return printHelp();
  const sites = ["huaxin", "jiubang"];
  const mappings = readMappings(options.mappingRoot, sites);
  const stores = await openStores(options.apiRoot, { mongo: true });
  try {
    const dataSources = await resolveDataSources(stores.prisma, sites);
    const hydratedResult = await hydrateMappings(stores, mappings.selected, dataSources);
    const projectIndex = await loadProjectIndex(stores.prisma, hydratedResult.hydrated);
    const rows = hydratedResult.hydrated.map((row) => {
      const key = projectLookupKey(
        row.mapping.projectCode,
        row.mapping.site,
        row.mapping.standaloneProjectName,
      );
      const project = projectIndex.get(key);
      if (!project) throw new Error(`${row.mapping.site}/${row.mapping.sourceNoticeId}: project not found; run import_projects.js first`);
      return {
        extractionId: row.extraction.id,
        mongoDocumentId: row.extraction.mongoDocumentId,
        data: {
          projectId: project.id,
          noticeType: row.noticeType,
          title: row.rawNotice.title,
          content: row.rawDocument.rawText || null,
          structuredData: row.extractedFields,
          publishDate: row.rawNotice.publishDate,
          sourceSite: row.rawNotice.dataSource.name,
          sourceUrl: row.rawNotice.sourceUrl,
          sourceNoticeId: row.rawNotice.sourceNoticeId,
          crawlTime: row.rawNotice.crawlTime,
        },
      };
    });
    const duplicateKeys = new Set();
    for (const row of rows) {
      const key = `${row.data.sourceSite}\u0000${row.data.sourceNoticeId}`;
      if (duplicateKeys.has(key)) throw new Error(`Duplicate project_notice identity: ${key}`);
      duplicateKeys.add(key);
    }
    console.log(`Mode: ${options.commit ? "COMMIT" : "DRY RUN (read only)"}`);
    console.log(`Project notices to upsert: ${rows.length}`);
    console.log(`Stale mappings ignored: ${hydratedResult.stale.length}`);
    console.log(`Review-required mappings excluded: ${mappings.review.length}`);
    if (!options.commit) return console.log("Dry run complete. Add --commit to upsert project_notice and links.");
    const result = await commitRows(stores, rows);
    console.log(`Commit completed: inserted=${result.inserted}, updated=${result.updated}, links=${rows.length}.`);
  } finally {
    await stores.close();
  }
}

main().catch((error) => {
  console.error(`Import failed: ${error.stack || error.message}`);
  process.exitCode = 1;
});
