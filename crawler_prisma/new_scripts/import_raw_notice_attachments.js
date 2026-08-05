#!/usr/bin/env node

"use strict";

const fs = require("node:fs");
const path = require("node:path");
const { randomUUID } = require("node:crypto");
const {
  assertMaxLength,
  bigintValue,
  deduplicate,
  nullableString,
  openStores,
  parseCommonArgs,
  parseCrawlerDate,
  readJsonNotices,
  requiredString,
  resolveDataSources,
  safeObjectName,
} = require("./lib/runtime");

function printHelp() {
  console.log(`Usage:
  node import_raw_notice_attachments.js [options]

Options:
  --commit                 Upload files to MinIO and write MySQL metadata.
  --site=<site>            all, huaxin, jiubang, sxjm, or sxzwfw (default: all).
  --output-root=<path>     Crawler output root.
  --api-root=<path>        ProjectRecommendationSystem/api directory.
  --allow-missing-files    Keep metadata with storage_provider=SOURCE when a
                           JSON attachment has no readable local file.
  --help                   Show this help.

Run import_raw_notices.js first. Without --commit this only validates JSON/files.
`);
}

function resolveStorageFile(outputRoot, storagePath, context) {
  if (storagePath === null) return null;
  const absolutePath = path.resolve(outputRoot, storagePath);
  const root = path.resolve(outputRoot);
  if (absolutePath !== root && !absolutePath.startsWith(`${root}${path.sep}`)) {
    throw new Error(`${context}: storage_path escapes output root: ${storagePath}`);
  }
  return absolutePath;
}

function identity(row) {
  return row.fileHash
    ? `hash:${row.fileHash}`
    : row.sourceFileId
      ? `source:${row.sourceFileId}`
      : row.fileUrl
        ? `url:${row.fileUrl}`
        : `name:${row.fileName}`;
}

function mapAttachments(loaded, outputRoot, allowMissingFiles) {
  const rows = [];
  for (const record of loaded.records) {
    const values = record.source["附件"];
    if (values === null || values === undefined) continue;
    if (!Array.isArray(values)) throw new Error(`${record.context}: 附件 must be an array`);
    values.forEach((source, attachmentIndex) => {
      const context = `${record.context} attachment ${attachmentIndex + 1}`;
      if (!source || typeof source !== "object" || Array.isArray(source)) {
        throw new Error(`${context}: expected an object`);
      }
      const row = {
        ...record,
        context,
        sourceNoticeId: requiredString(record.source["公告ID"], "公告ID", context),
        sourceFileId: nullableString(source.source_file_id),
        fileName: nullableString(source.file_name),
        fileUrl: nullableString(source.file_url),
        storagePath: nullableString(source.storage_path),
        fileHash: nullableString(source.file_hash),
        declaredFileSize: bigintValue(source.file_size_bytes, "file_size_bytes", context),
        fileType: nullableString(source.file_type),
        parseStatus: (nullableString(source.parse_status) || "PENDING").toUpperCase(),
        publishDate: parseCrawlerDate(
          record.source["发布时间"] ?? record.source["发布日期"],
          "发布时间/发布日期",
          context,
        ),
      };
      if (!row.fileName && !row.fileUrl && !row.storagePath && !row.fileHash) {
        throw new Error(`${context}: attachment has no usable identity`);
      }
      assertMaxLength(row.sourceNoticeId, 256, "source_notice_id", context);
      assertMaxLength(row.fileName, 512, "file_name", context);
      assertMaxLength(row.fileUrl, 1024, "file_url", context);
      assertMaxLength(row.fileHash, 64, "file_hash", context);
      assertMaxLength(row.fileType, 32, "file_type", context);
      assertMaxLength(row.parseStatus, 32, "parse_status", context);

      row.absolutePath = resolveStorageFile(outputRoot, row.storagePath, context);
      row.fileExists = Boolean(row.absolutePath && fs.existsSync(row.absolutePath));
      if (row.fileExists && !fs.statSync(row.absolutePath).isFile()) {
        throw new Error(`${context}: storage_path is not a regular file: ${row.absolutePath}`);
      }
      if (!row.fileExists && !allowMissingFiles) {
        throw new Error(
          `${context}: attachment file is missing (${row.absolutePath || "storage_path is empty"}); use --allow-missing-files only if metadata-only rows are intentional`,
        );
      }
      if (row.fileExists) {
        row.actualFileSize = BigInt(fs.statSync(row.absolutePath).size);
        if (row.declaredFileSize !== null && row.declaredFileSize !== row.actualFileSize) {
          throw new Error(`${context}: file_size_bytes=${row.declaredFileSize} but actual size=${row.actualFileSize}`);
        }
      } else {
        row.actualFileSize = row.declaredFileSize;
      }
      rows.push(row);
    });
  }
  return deduplicate(rows, (row) => `${row.site}\u0000${row.sourceNoticeId}\u0000${identity(row)}`);
}

function existingIdentityMatches(existing, row) {
  if (row.fileHash && existing.fileHash === row.fileHash) return true;
  if (row.fileUrl && existing.fileUrl === row.fileUrl) return true;
  return Boolean(row.fileName && existing.fileName === row.fileName);
}

function buildObjectKey(row, parent, attachmentUid) {
  const sourceDate = nullableString(row.source["发布时间"] ?? row.source["发布日期"]);
  const matchedDate = sourceDate?.match(/^(\d{4})-(\d{2})/);
  const now = new Date();
  const year = matchedDate?.[1] || String(now.getFullYear());
  const month = matchedDate?.[2] || String(now.getMonth() + 1).padStart(2, "0");
  return [
    safeObjectName(row.site),
    year,
    month,
    parent.uid,
    attachmentUid,
    safeObjectName(row.fileName || path.basename(row.absolutePath || "attachment.bin")),
  ].join("/");
}

async function objectAlreadyMatches(minio, bucketName, objectKey, size) {
  try {
    const stat = await minio.statObject(bucketName, objectKey);
    return BigInt(stat.size) === size;
  } catch (error) {
    if (error.code === "NotFound" || error.code === "NoSuchKey" || error.code === "NoSuchObject") return false;
    throw error;
  }
}

async function importOne(stores, row, parent) {
  const { prisma, minio, bucketName } = stores;
  const candidates = await prisma.rawNoticeAttachment.findMany({ where: { rawNoticeId: parent.id } });
  const existing = candidates.find((candidate) => existingIdentityMatches(candidate, row)) || null;
  const uid = existing?.uid || randomUUID();
  let storageProvider = existing?.storageProvider || "SOURCE";
  let storedBucketName = existing?.bucketName || null;
  let objectKey = existing?.objectKey || null;
  let uploadedNewObject = false;

  if (row.fileExists) {
    storageProvider = "MINIO";
    storedBucketName = bucketName;
    objectKey = objectKey || buildObjectKey(row, parent, uid);
    assertMaxLength(objectKey, 700, "object_key", row.context);
    const alreadyStored = await objectAlreadyMatches(minio, bucketName, objectKey, row.actualFileSize);
    if (!alreadyStored) {
      await minio.putObject(
        bucketName,
        objectKey,
        fs.createReadStream(row.absolutePath),
        Number(row.actualFileSize),
        {
          "Content-Type": row.fileType || "application/octet-stream",
          "X-Amz-Meta-Attachment-Uid": uid,
          "X-Amz-Meta-Raw-Notice-Uid": parent.uid,
          "X-Amz-Meta-Source-File-Id": row.sourceFileId || "",
        },
      );
      uploadedNewObject = !existing?.objectKey;
    }
  }

  const data = {
    uid,
    rawNoticeId: parent.id,
    fileName: row.fileName,
    fileUrl: row.fileUrl,
    storageProvider,
    bucketName: storedBucketName,
    objectKey,
    fileHash: row.fileHash,
    fileSizeBytes: row.actualFileSize,
    fileType: row.fileType,
    parseStatus: row.parseStatus,
  };
  try {
    if (existing) {
      await prisma.rawNoticeAttachment.update({ where: { id: existing.id }, data });
      return "updated";
    }
    await prisma.rawNoticeAttachment.create({ data });
    return "inserted";
  } catch (error) {
    if (uploadedNewObject) await minio.removeObject(bucketName, objectKey).catch(() => {});
    throw error;
  }
}

async function main() {
  const options = parseCommonArgs(process.argv.slice(2), { allowMissingFiles: false });
  if (options.help) return printHelp();
  const loaded = readJsonNotices(options.outputRoot, options.sites);
  const mapped = mapAttachments(loaded, options.outputRoot, options.allowMissingFiles);

  console.log(`Mode: ${options.commit ? "COMMIT" : "DRY RUN (no storage writes)"}`);
  console.log(`Validated JSON files: ${loaded.jsonFileCount}`);
  console.log(`Validated attachments: ${mapped.records.length}`);
  console.log(`Duplicate JSON attachments skipped: ${mapped.duplicateCount}`);
  console.log(`Files ready for MinIO: ${mapped.records.filter((row) => row.fileExists).length}`);
  console.log(`Metadata-only attachments: ${mapped.records.filter((row) => !row.fileExists).length}`);
  if (!options.commit) return console.log("Dry run complete. Add --commit to upload MinIO objects and write MySQL metadata.");

  const stores = await openStores(options.apiRoot, { minio: true });
  try {
    if (!await stores.minio.bucketExists(stores.bucketName)) {
      await stores.minio.makeBucket(stores.bucketName);
    }
    const dataSources = await resolveDataSources(stores.prisma, options.sites);
    const parentCache = new Map();
    const counts = { inserted: 0, updated: 0 };
    for (let index = 0; index < mapped.records.length; index += 1) {
      const row = mapped.records[index];
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
      if ((index + 1) % 50 === 0 || index + 1 === mapped.records.length) {
        console.log(`  Processed ${index + 1}/${mapped.records.length}`);
      }
    }
    console.log(`Commit completed: inserted=${counts.inserted}, updated=${counts.updated}.`);
  } finally {
    await stores.close();
  }
}

main().catch((error) => {
  console.error(`Import failed: ${error.stack || error.message}`);
  process.exitCode = 1;
});
