#!/usr/bin/env node

"use strict";

const { openStores, parseCommonArgs, resolveDataSources } = require("./lib/runtime");

function printHelp() {
  console.log(`Usage:
  node import_project_notice_attachments.js [options]

Options:
  --commit                 Upsert project attachment metadata.
  --site=<site>            all or any configured crawler site.
  --output-root=<path>     Crawler new_output root (used to select available sites).
  --env-file=<path>        crawler_prisma environment file (default: .env).

Copies attachment metadata and existing MinIO object references from
raw_notice_attachment to project_notice_attachment. No file is uploaded again.
`);
}

function identity(row) {
  return `${row.noticeId}\u0000${row.bucketName || ""}\u0000${row.objectKey || row.fileHash || row.fileUrl || row.fileName}`;
}

async function main() {
  const options = parseCommonArgs(process.argv.slice(2));
  if (options.help) return printHelp();
  const stores = await openStores(options.envFile);
  try {
    const dataSources = await resolveDataSources(stores.prisma, options.sites);
    const dataSourceIds = [...dataSources.values()].map((source) => source.id);
    const rawAttachments = await stores.prisma.rawNoticeAttachment.findMany({
      where: { rawNotice: { dataSourceId: { in: dataSourceIds } } },
      include: {
        rawNotice: {
          include: { extractionResults: { where: { projectNoticeId: { not: null } } } },
        },
      },
    });
    const rows = [];
    let unlinked = 0;
    for (const attachment of rawAttachments) {
      const projectNoticeIds = [...new Set(
        attachment.rawNotice.extractionResults.map((row) => row.projectNoticeId).filter((id) => id !== null),
      )];
      if (projectNoticeIds.length === 0) {
        unlinked += 1;
        continue;
      }
      if (projectNoticeIds.length > 1) {
        throw new Error(`raw_notice_attachment.id=${attachment.id} links to multiple project notices`);
      }
      if (!attachment.fileName) throw new Error(`raw_notice_attachment.id=${attachment.id} has no file_name`);
      rows.push({
        noticeId: projectNoticeIds[0],
        fileName: attachment.fileName,
        fileUrl: attachment.fileUrl,
        fileType: attachment.fileType,
        storageProvider: attachment.storageProvider,
        bucketName: attachment.bucketName,
        objectKey: attachment.objectKey,
        fileHash: attachment.fileHash,
        fileSizeBytes: attachment.fileSizeBytes,
        parseStatus: attachment.parseStatus,
        mongoParseDocumentId: attachment.mongoParseDocumentId,
      });
    }
    const seen = new Set();
    for (const row of rows) {
      const key = identity(row);
      if (seen.has(key)) throw new Error(`Duplicate project attachment identity: ${key}`);
      seen.add(key);
    }
    console.log(`Mode: ${options.commit ? "COMMIT" : "DRY RUN (read only)"}`);
    console.log(`Raw attachments read: ${rawAttachments.length}`);
    console.log(`Project attachments to upsert: ${rows.length}`);
    console.log(`Attachments without project notice link: ${unlinked}`);
    if (!options.commit) return console.log("Dry run complete. Add --commit to write project_notice_attachment.");

    let inserted = 0;
    let updated = 0;
    await stores.prisma.$transaction(async (transaction) => {
      const existingRows = await transaction.projectNoticeAttachment.findMany();
      const existingByIdentity = new Map(existingRows.map((row) => [identity(row), row]));
      for (const row of rows) {
        const existing = existingByIdentity.get(identity(row));
        if (existing) {
          await transaction.projectNoticeAttachment.update({ where: { id: existing.id }, data: row });
          updated += 1;
        } else {
          await transaction.projectNoticeAttachment.create({ data: row });
          inserted += 1;
        }
      }
    }, { maxWait: 10_000, timeout: 300_000 });
    console.log(`Commit completed: inserted=${inserted}, updated=${updated}.`);
  } finally {
    await stores.close();
  }
}

main().catch((error) => {
  console.error(`Import failed: ${error.stack || error.message}`);
  process.exitCode = 1;
});
