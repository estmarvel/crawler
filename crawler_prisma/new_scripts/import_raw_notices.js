#!/usr/bin/env node

"use strict";

const { randomUUID } = require("node:crypto");
const {
  assertMaxLength,
  iterateJsonNotices,
  jsonNoticeFiles,
  nullableString,
  openStores,
  parseCommonArgs,
  parseCrawlerDate,
  requiredString,
  resolveDataSources,
  stableDigest,
  traceEnvelope,
} = require("./lib/runtime");

function printHelp() {
  console.log(`Usage:
  node import_raw_notices.js [options]

Options:
  --commit                 Write MongoDB documents and MySQL index rows.
  --site=<site>            all, sxjm, sxzwfw, bitbid, huaxin, or jiubang.
  --crawl-task-id=<id>     Optional existing crawl_task id; requires one site.
  --output-root=<path>     Crawler output root.
  --api-root=<path>        Project recommendation API directory.
  --help                   Show this help.

Without --commit this command only validates JSON and does not connect to storage.
`);
}

function mapRecord(record) {
  const { source, context } = record;
  const sourceUrl = requiredString(source["详情页链接"], "详情页链接", context);
  const sourceNoticeId = requiredString(source["公告ID"], "公告ID", context);
  const title = nullableString(source["公告标题"]);
  const parseStatus = (nullableString(source["解析状态"]) || "PENDING").toUpperCase();
  const fingerprint = nullableString(source["内容指纹"]);
  const noticeType = requiredString(source["公告类型"], "公告类型", context).toUpperCase();
  const trace = traceEnvelope(source, context);
  const rawText = nullableString(trace?.rawText) || (noticeType === "PLAN"
    ? null
    : nullableString(source["公告正文"]) || nullableString(source["公告内容"]));

  assertMaxLength(sourceUrl, 1024, "详情页链接/source_url", context);
  assertMaxLength(sourceNoticeId, 256, "公告ID/source_notice_id", context);
  assertMaxLength(title, 512, "公告标题/title", context);
  assertMaxLength(parseStatus, 32, "解析状态/parse_status", context);
  assertMaxLength(fingerprint, 64, "内容指纹/fingerprint", context);

  return {
    ...record,
    sourceUrl,
    sourceNoticeId,
    title,
    parseStatus,
    fingerprint,
    rawText,
    trace,
    publishDate: parseCrawlerDate(source["发布时间"] ?? source["发布日期"], "发布时间/发布日期", context),
    crawlTime: parseCrawlerDate(source["爬虫时间"], "爬虫时间", context, true),
  };
}

function contentChanged(existing, row) {
  if (existing.fingerprint && row.fingerprint) return existing.fingerprint !== row.fingerprint;
  return existing.sourceUrl !== row.sourceUrl || existing.title !== row.title;
}

function recordDigest(row) {
  return stableDigest({
    sourceUrl: row.sourceUrl,
    title: row.title,
    parseStatus: row.parseStatus,
    fingerprint: row.fingerprint,
    rawText: row.rawText,
    publishDate: row.publishDate,
  });
}

function validObjectId(ObjectId, value) {
  return typeof value === "string" && ObjectId.isValid(value) && new ObjectId(value).toHexString() === value;
}

function exportMetadata(row) {
  const source = row.source;
  return {
    platformName: nullableString(source["平台名称"]),
    platformCode: nullableString(source["平台代码"]),
    sourceNoticeId: nullableString(source["公告ID"]),
    noticeType: nullableString(source["公告类型"]),
    noticeSubtype: nullableString(source["公告子类型"]),
    title: nullableString(source["公告标题"]),
    publishTime: source["发布时间"] ?? null,
    detailUrl: nullableString(source["详情页链接"]),
    parseStatus: nullableString(source["解析状态"]),
    isVerified: source["是否已核验"] ?? false,
    missingFields: Array.isArray(source["缺失字段"]) ? source["缺失字段"] : [],
    snapshotPath: nullableString(source["HTML快照路径"]),
    snapshotSha256: nullableString(source["HTML快照SHA256"]),
    attachments: Array.isArray(source["附件"]) ? source["附件"] : [],
  };
}

async function writeOccurrence(prisma, crawlTaskId, rawNotice, previousFingerprint, currentFingerprint) {
  if (crawlTaskId === null) return;
  const existing = await prisma.crawlTaskNotice.findFirst({
    where: { crawlTaskId, rawNoticeId: rawNotice.id },
  });
  const data = {
    isNew: previousFingerprint === undefined,
    isUpdated: previousFingerprint !== undefined && previousFingerprint !== currentFingerprint,
    contentHash: currentFingerprint,
  };
  if (existing) {
    await prisma.crawlTaskNotice.update({ where: { id: existing.id }, data });
  } else {
    await prisma.crawlTaskNotice.create({
      data: { crawlTaskId, rawNoticeId: rawNotice.id, ...data },
    });
  }
}

async function importOne(stores, row, dataSourceId, crawlTaskId) {
  const { prisma, mongo, ObjectId } = stores;
  const collection = mongo.collection("raw_notices");
  const existing = await prisma.rawNotice.findFirst({
    where: { dataSourceId, sourceNoticeId: row.sourceNoticeId },
  });
  const uid = existing?.uid || randomUUID();
  const changed = existing ? contentChanged(existing, row) : false;
  const contentVersion = existing ? (changed ? existing.contentVersion + 1 : existing.contentVersion) : 1;
  let previousDocument = null;
  if (existing && !changed && validObjectId(ObjectId, existing.mongoDocumentId)) {
    previousDocument = await collection.findOne({ _id: new ObjectId(existing.mongoDocumentId) });
  }
  if (existing && !changed && !previousDocument) {
    previousDocument = await collection.findOne({ rawNoticeUid: uid, contentVersion });
  }
  const reuseCurrentDocument = Boolean(previousDocument);
  const mongoId = previousDocument?._id || new ObjectId();
  const documentUid = previousDocument?.documentUid || randomUUID();
  const createdAt = previousDocument?.createdAt || new Date();
  const document = {
    _id: mongoId,
    documentUid,
    rawNoticeUid: uid,
    dataSourceId,
    crawlTaskId: crawlTaskId?.toString() || null,
    sourceNoticeId: row.sourceNoticeId,
    sourceUrl: row.sourceUrl,
    title: row.title,
    contentVersion,
    payload: row.trace?.payload || row.source,
    rawHtml: nullableString(row.trace?.rawHtml),
    rawText: row.rawText,
    responseMetadata: {
      ...(row.trace?.responseMetadata || {}),
      import: {
        importedFrom: `${row.site}/json/${row.fileName}`,
        importer: "crawler_prisma/new_scripts/import_raw_notices.js",
      },
      trace: row.trace ? {
        schemaVersion: row.trace.schemaVersion,
        noticeSchemaVersion: row.trace.noticeSchemaVersion || null,
        extractionModel: row.trace.extractionModel || null,
        extractionVersion: row.trace.extractionVersion || null,
        fieldMeta: row.trace.fieldMeta || null,
        exportMetadata: row.trace.exportMetadata || exportMetadata(row),
        integrity: row.trace.integrity || null,
      } : {
        schemaVersion: null,
        exportMetadata: exportMetadata(row),
      },
    },
    fingerprint: row.fingerprint,
    crawlerVersion: nullableString(row.trace?.crawlerVersion),
    crawledAt: row.crawlTime,
    createdAt,
  };
  if (existing?.id) document.mysqlRawNoticeId = existing.id.toString();

  if (reuseCurrentDocument) {
    await collection.replaceOne({ _id: mongoId }, document, { upsert: true });
  } else {
    await collection.insertOne(document);
  }

  let stored;
  try {
    const mysqlData = {
      uid,
      dataSourceId,
      crawlTaskId,
      sourceUrl: row.sourceUrl,
      sourceNoticeId: row.sourceNoticeId,
      title: row.title,
      publishDate: row.publishDate,
      crawlTime: row.crawlTime,
      parseStatus: row.parseStatus,
      fingerprint: row.fingerprint,
      contentVersion,
      mongoDocumentId: mongoId.toHexString(),
    };
    stored = existing
      ? await prisma.rawNotice.update({ where: { id: existing.id }, data: mysqlData })
      : await prisma.rawNotice.create({ data: mysqlData });
  } catch (error) {
    if (reuseCurrentDocument && previousDocument) {
      await collection.replaceOne({ _id: mongoId }, previousDocument, { upsert: true });
    } else {
      await collection.deleteOne({ _id: mongoId });
    }
    throw error;
  }
  await collection.updateOne(
    { _id: mongoId },
    { $set: { mysqlRawNoticeId: stored.id.toString() } },
  );
  await writeOccurrence(
    prisma,
    crawlTaskId,
    stored,
    existing ? existing.fingerprint : undefined,
    row.fingerprint,
  );
  return existing ? (changed ? "versioned" : "updated") : "inserted";
}

async function main() {
  const options = parseCommonArgs(process.argv.slice(2), { crawlTaskId: null });
  if (options.help) return printHelp();
  console.log(`Mode: ${options.commit ? "COMMIT" : "DRY RUN (no storage writes)"}`);
  console.log(`Output root: ${options.outputRoot}`);
  console.log(`Validated JSON files: ${jsonNoticeFiles(options.outputRoot, options.sites).length}`);

  const stores = options.commit ? await openStores(options.apiRoot, { mongo: true }) : null;
  try {
    const dataSources = stores ? await resolveDataSources(stores.prisma, options.sites) : null;
    let crawlTaskId = null;
    if (options.crawlTaskId !== null) {
      crawlTaskId = BigInt(options.crawlTaskId);
      if (stores) {
        const task = await stores.prisma.crawlTask.findUnique({ where: { id: crawlTaskId } });
        const expectedDataSourceId = dataSources.get(options.sites[0]).id;
        if (!task) throw new Error(`crawl_task.id=${crawlTaskId} does not exist`);
        if (task.dataSourceId !== expectedDataSourceId) {
          throw new Error(`crawl_task.id=${crawlTaskId} belongs to data_source_id=${task.dataSourceId}, expected ${expectedDataSourceId}`);
        }
      }
    }

    const counts = { inserted: 0, updated: 0, versioned: 0 };
    const siteCounts = new Map(options.sites.map((site) => [site, 0]));
    const identities = new Map();
    let duplicateCount = 0;
    let processed = 0;
    for await (const record of iterateJsonNotices(options.outputRoot, options.sites)) {
      const row = mapRecord(record);
      const key = `${row.site}\u0000${row.sourceNoticeId}`;
      if (identities.has(key)) {
        duplicateCount += 1;
        if (recordDigest(identities.get(key)) !== recordDigest(row)) {
          throw new Error(
            `${row.context}: conflicting duplicate 公告ID=${row.sourceNoticeId}; `
            + `first seen at ${identities.get(key).context}`,
          );
        }
        continue;
      } else {
        identities.set(key, row);
        siteCounts.set(row.site, siteCounts.get(row.site) + 1);
      }
      if (stores) {
        const result = await importOne(stores, row, dataSources.get(row.site).id, crawlTaskId);
        counts[result] += 1;
      }
      processed += 1;
      if (processed % 100 === 0) {
        console.log(`  Processed ${processed}`);
      }
    }
    console.log(`Validated notices: ${identities.size}`);
    console.log(`Duplicate JSON notices encountered: ${duplicateCount}`);
    for (const site of options.sites) console.log(`  ${site}: ${siteCounts.get(site)}`);
    if (!stores) return console.log("Dry run complete. Add --commit to write MongoDB and MySQL.");
    console.log(`Commit completed: inserted=${counts.inserted}, updated=${counts.updated}, new_versions=${counts.versioned}.`);
  } finally {
    if (stores) await stores.close();
  }
}

if (require.main === module) {
  main().catch((error) => {
    console.error(`Import failed: ${error.stack || error.message}`);
    process.exitCode = 1;
  });
}

module.exports = { exportMetadata, mapRecord, recordDigest };
