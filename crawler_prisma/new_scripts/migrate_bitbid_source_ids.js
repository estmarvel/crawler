#!/usr/bin/env node
"use strict";

const { iterateJsonNotices, openStores, parseCommonArgs, resolveDataSources } = require("./lib/runtime");

async function expectedIdentityIndex(outputRoot) {
  const byUrl = new Map();
  const rawCounts = new Map();
  for await (const record of iterateJsonNotices(outputRoot, ["bitbid"])) {
    const sourceNoticeId = String(record.source["公告ID"] || "").trim();
    const sourceUrl = String(record.source["详情页链接"] || "").trim();
    if (!/^(plan|tender|candidate|award):[^:]+$/u.test(sourceNoticeId)) {
      throw new Error(`${record.context}: expected category-prefixed 公告ID, got ${sourceNoticeId}`);
    }
    if (byUrl.has(sourceUrl)) throw new Error(`duplicate Bitbid source URL: ${sourceUrl}`);
    byUrl.set(sourceUrl, sourceNoticeId);
    const rawId = sourceNoticeId.split(":", 2)[1];
    rawCounts.set(rawId, (rawCounts.get(rawId) || 0) + 1);
  }
  return { byUrl, collisionIds: new Set([...rawCounts].filter(([, count]) => count > 1).map(([id]) => id)) };
}

function identityFromUrl(sourceUrl) {
  const url = new URL(sourceUrl);
  const rawId = url.searchParams.get("id");
  const type = url.searchParams.get("type");
  let category = null;
  if (url.pathname.endsWith("/details") && type === "2") category = "plan";
  else if (url.pathname.endsWith("/detail") && type === "0") category = "tender";
  else if (url.pathname.endsWith("/detail") && type === "1") category = "candidate";
  else if (url.pathname.endsWith("/detail") && type === "2") category = "award";
  return category && rawId ? `${category}:${rawId}` : null;
}

async function main() {
  const options = parseCommonArgs([...process.argv.slice(2), "--site=bitbid"]);
  const expected = await expectedIdentityIndex(options.outputRoot);
  const stores = await openStores(options.envFile, { mongo: true });
  try {
    const sources = await resolveDataSources(stores.prisma, ["bitbid"]);
    const dataSourceId = sources.get("bitbid").id;
    const rows = await stores.prisma.rawNotice.findMany({
      where: { dataSourceId },
      select: { id: true, sourceUrl: true, sourceNoticeId: true, mongoDocumentId: true, contentVersion: true },
    });
    const mappedRows = [];
    const migrations = [];
    const unresolved = [];
    for (const row of rows) {
      const target = expected.byUrl.get(row.sourceUrl) || identityFromUrl(row.sourceUrl);
      if (!target) unresolved.push(`${row.id}:${row.sourceUrl}`);
      else {
        mappedRows.push({ row, target });
        if (row.sourceNoticeId !== target) migrations.push({ row, target });
      }
    }
    if (unresolved.length) throw new Error(`Bitbid rows have no JSON identity mapping: ${unresolved.slice(0, 10).join(", ")}`);

    const collection = stores.mongo.collection("raw_notices");
    const mongoDocs = await collection.find(
      { dataSourceId },
      { projection: { _id: 1, sourceNoticeId: 1, contentVersion: 1 } },
    ).toArray();
    const referenced = new Set(rows.map((row) => row.mongoDocumentId).filter(Boolean));
    const allOrphans = mongoDocs.filter((doc) => !referenced.has(doc._id.toHexString()));
    const orphans = allOrphans.filter((doc) => expected.collisionIds.has(String(doc.sourceNoticeId)));
    const mongoById = new Map(mongoDocs.map((doc) => [doc._id.toHexString(), doc]));
    const mongoMigrations = mappedRows.filter(({ row, target }) => {
      const document = mongoById.get(String(row.mongoDocumentId));
      const collision = expected.collisionIds.has(target.split(":", 2)[1]);
      return document && (
        document.sourceNoticeId !== target
        || (collision && document.contentVersion !== 1)
      );
    });
    const dataSourceName = sources.get("bitbid").name;
    const existingProjectNotices = await stores.prisma.projectNotice.findMany({
      where: { sourceSite: dataSourceName },
      select: { sourceNoticeId: true },
    });
    const projectNoticeCounts = new Map();
    for (const projectNotice of existingProjectNotices) {
      const identity = String(projectNotice.sourceNoticeId);
      projectNoticeCounts.set(identity, (projectNoticeCounts.get(identity) || 0) + 1);
    }

    console.log(`Mode: ${options.commit ? "COMMIT" : "DRY RUN (no database writes)"}`);
    console.log(`Bitbid JSON identities: ${expected.byUrl.size}`);
    console.log(`MySQL raw notices: ${rows.length}`);
    console.log(`Rows to namespace: ${migrations.length}`);
    console.log(`Mongo current documents to synchronize: ${mongoMigrations.length}`);
    console.log(`Cross-category raw ID collisions: ${expected.collisionIds.size}`);
    console.log(`Collision-version Mongo documents to remove: ${orphans.length}`);
    console.log(`Other pre-existing unreferenced Mongo documents left untouched: ${allOrphans.length - orphans.length}`);
    console.log(`Existing project_notice identities to update: ${[...projectNoticeCounts.values()].reduce((a, b) => a + b, 0)}`);
    if (!options.commit) return console.log("Dry run complete. Add --commit to apply this one-time correction.");

    for (let offset = 0; offset < migrations.length; offset += 500) {
      const batch = migrations.slice(offset, offset + 500);
      await stores.prisma.$transaction(
        batch.map(({ row, target }) => stores.prisma.rawNotice.update({
          where: { id: row.id },
          data: {
            sourceNoticeId: target,
            contentVersion: expected.collisionIds.has(String(row.sourceNoticeId)) ? 1 : row.contentVersion,
          },
        })),
      );
      console.log(`  MySQL identities ${Math.min(offset + batch.length, migrations.length)}/${migrations.length}`);
    }
    for (const { row, target } of migrations) {
      if (!projectNoticeCounts.has(String(row.sourceNoticeId))) continue;
      await stores.prisma.projectNotice.updateMany({
        where: { sourceSite: dataSourceName, sourceNoticeId: String(row.sourceNoticeId) },
        data: { sourceNoticeId: target },
      });
    }
    // 碰撞旧版本与当前文档共用 rawNoticeUid；必须先删无 MySQL 引用的旧版本，
    // 再把当前文档的错误 version=2 归一为 version=1。
    if (orphans.length) {
      const result = await collection.deleteMany({ _id: { $in: orphans.map((doc) => doc._id) } });
      if (result.deletedCount !== orphans.length) throw new Error(`expected to remove ${orphans.length} collision documents, removed ${result.deletedCount}`);
    }
    for (let offset = 0; offset < mongoMigrations.length; offset += 500) {
      const operations = mongoMigrations.slice(offset, offset + 500).map(({ row, target }) => ({
        updateOne: {
          filter: { _id: new stores.ObjectId(row.mongoDocumentId) },
          update: { $set: {
            sourceNoticeId: target,
            contentVersion: expected.collisionIds.has(target.split(":", 2)[1]) ? 1 : row.contentVersion,
            "responseMetadata.trace.exportMetadata.sourceNoticeId": target,
          } },
        },
      }));
      if (operations.length) await collection.bulkWrite(operations, { ordered: true });
    }
    console.log(`Migration completed: mysql_namespaced=${migrations.length}, mongo_synchronized=${mongoMigrations.length}, collision_documents_removed=${orphans.length}.`);
  } finally {
    await stores.close();
  }
}

main().catch((error) => {
  console.error(`Bitbid identity migration failed: ${error.stack || error.message}`);
  process.exitCode = 1;
});
