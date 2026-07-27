#!/usr/bin/env node

"use strict";

const fs = require("node:fs");
const path = require("node:path");

const PROJECT_ROOT = path.resolve(__dirname, "..");
const DEFAULT_OUTPUT_ROOT = path.resolve(PROJECT_ROOT, "../Crawler_Scrapy/output");

// Folder/platform code -> database foreign key mapping.
// short_code is intentionally not checked: the database uses ygcgpt for huaxin.
const SITE_CONFIG = Object.freeze({
  huaxin: Object.freeze({ dataSourceId: 6 }),
  jiubang: Object.freeze({ dataSourceId: 14 }),
});

function printHelp() {
  console.log(`Usage:
  npm run import:raw-notice-attachments -- [options]

Options:
  --commit              Write to MySQL. Without it, only validate and summarize.
  --site=<name>         all, huaxin, or jiubang (default: all).
  --output-root=<path>  Crawler output directory (default: ${DEFAULT_OUTPUT_ROOT}).
  --help                Show this help.

Examples:
  npm run import:raw-notice-attachments
  npm run import:raw-notice-attachments -- --site=huaxin
  npm run import:raw-notice-attachments -- --commit
`);
}

function parseArgs(argv) {
  const options = {
    commit: false,
    site: "all",
    outputRoot: DEFAULT_OUTPUT_ROOT,
  };

  for (const arg of argv) {
    if (arg === "--commit") {
      options.commit = true;
    } else if (arg === "--help" || arg === "-h") {
      options.help = true;
    } else if (arg.startsWith("--site=")) {
      options.site = arg.slice("--site=".length).trim().toLowerCase();
    } else if (arg.startsWith("--output-root=")) {
      options.outputRoot = path.resolve(arg.slice("--output-root=".length));
    } else {
      throw new Error(`Unknown argument: ${arg}`);
    }
  }

  if (!["all", ...Object.keys(SITE_CONFIG)].includes(options.site)) {
    throw new Error(`Invalid --site value: ${options.site}`);
  }
  return options;
}

function loadDatabaseUrlFromDotEnv() {
  if (process.env.DATABASE_URL) return;

  const envPath = path.join(PROJECT_ROOT, ".env");
  if (!fs.existsSync(envPath)) return;
  const line = fs
    .readFileSync(envPath, "utf8")
    .split(/\r?\n/)
    .find((candidate) => /^\s*DATABASE_URL\s*=/.test(candidate));
  if (!line) return;

  let value = line.replace(/^\s*DATABASE_URL\s*=\s*/, "").trim();
  if (
    value.length >= 2 &&
    ((value.startsWith('"') && value.endsWith('"')) ||
      (value.startsWith("'") && value.endsWith("'")))
  ) {
    value = value.slice(1, -1);
  }
  if (value) process.env.DATABASE_URL = value;
}

function nullableString(value) {
  if (value === null || value === undefined) return null;
  const text = String(value).trim();
  return text === "" ? null : text;
}

function requiredString(value, field, context) {
  const text = nullableString(value);
  if (text === null) throw new Error(`${context}: missing required field ${field}`);
  return text;
}

function assertMaxLength(value, max, field, context) {
  if (value !== null && [...value].length > max) {
    throw new Error(`${context}: ${field} exceeds ${max} characters`);
  }
}

function nullableBigInt(value, field, context) {
  if (value === null || value === undefined || value === "") return null;
  if (typeof value === "number" && !Number.isSafeInteger(value)) {
    throw new Error(`${context}: ${field} is not a safe integer; store it as a JSON string`);
  }
  const text = String(value).trim();
  if (!/^\d+$/.test(text)) throw new Error(`${context}: invalid ${field}: ${text}`);
  return BigInt(text);
}

function validateStorageFile(outputRoot, storagePath, parseStatus, context) {
  if (storagePath === null) return;

  const absolutePath = path.resolve(outputRoot, storagePath);
  const outputPrefix = `${path.resolve(outputRoot)}${path.sep}`;
  if (!absolutePath.startsWith(outputPrefix)) {
    throw new Error(`${context}: storage_path escapes output root: ${storagePath}`);
  }
  if (parseStatus.startsWith("DOWNLOADED") && !fs.existsSync(absolutePath)) {
    throw new Error(`${context}: downloaded attachment file does not exist: ${absolutePath}`);
  }
}

function attachmentIdentity(attachment) {
  if (attachment.storagePath) return `path:${attachment.storagePath}`;
  if (attachment.fileHash) return `hash:${attachment.fileHash}`;
  if (attachment.fileUrl) return `url:${attachment.fileUrl}`;
  if (attachment.fileName) return `name:${attachment.fileName}`;
  return null;
}

function attachmentIdentityCandidates(attachment) {
  return [
    attachment.storagePath && `path:${attachment.storagePath}`,
    attachment.fileHash && `hash:${attachment.fileHash}`,
    attachment.fileUrl && `url:${attachment.fileUrl}`,
    attachment.fileName && `name:${attachment.fileName}`,
  ].filter(Boolean);
}

function mapAttachment(source, parent, site, fileName, index, attachmentIndex, outputRoot) {
  const context = `${site}/${fileName} item ${index + 1} attachment ${attachmentIndex + 1}`;
  if (source === null || typeof source !== "object" || Array.isArray(source)) {
    throw new Error(`${context}: expected an attachment object`);
  }

  const mapped = {
    site,
    dataSourceId: SITE_CONFIG[site].dataSourceId,
    sourceNoticeId: requiredString(parent["公告ID"], "公告ID", context),
    sourceFileId: nullableString(source.source_file_id),
    fileName: nullableString(source.file_name),
    fileUrl: nullableString(source.file_url),
    storagePath: nullableString(source.storage_path),
    fileHash: nullableString(source.file_hash),
    fileSizeBytes: nullableBigInt(source.file_size_bytes, "file_size_bytes", context),
    fileType: nullableString(source.file_type),
    parseStatus: (nullableString(source.parse_status) || "PENDING").toUpperCase(),
  };

  assertMaxLength(mapped.sourceNoticeId, 256, "公告ID/source_notice_id", context);
  assertMaxLength(mapped.fileName, 512, "file_name", context);
  assertMaxLength(mapped.fileUrl, 1024, "file_url", context);
  assertMaxLength(mapped.storagePath, 1024, "storage_path", context);
  assertMaxLength(mapped.fileHash, 64, "file_hash", context);
  assertMaxLength(mapped.fileType, 32, "file_type", context);
  assertMaxLength(mapped.parseStatus, 32, "parse_status", context);

  if (attachmentIdentity(mapped) === null) {
    throw new Error(`${context}: attachment has no usable identity field`);
  }
  validateStorageFile(outputRoot, mapped.storagePath, mapped.parseStatus, context);
  return mapped;
}

function readAttachments(outputRoot, sites) {
  const attachments = [];
  let noticeCount = 0;
  let jsonFileCount = 0;

  for (const site of sites) {
    const jsonDirectory = path.join(outputRoot, site, "json");
    if (!fs.existsSync(jsonDirectory)) {
      throw new Error(`JSON directory does not exist: ${jsonDirectory}`);
    }
    const files = fs
      .readdirSync(jsonDirectory, { withFileTypes: true })
      .filter((entry) => entry.isFile() && entry.name.toLowerCase().endsWith(".json"))
      .map((entry) => entry.name)
      .sort((left, right) => left.localeCompare(right, "zh-CN"));
    if (files.length === 0) throw new Error(`No JSON files found in: ${jsonDirectory}`);

    for (const fileName of files) {
      jsonFileCount += 1;
      const filePath = path.join(jsonDirectory, fileName);
      let notices;
      try {
        notices = JSON.parse(fs.readFileSync(filePath, "utf8"));
      } catch (error) {
        throw new Error(`Cannot parse ${filePath}: ${error.message}`);
      }
      if (!Array.isArray(notices)) throw new Error(`${filePath}: top-level JSON must be an array`);

      notices.forEach((notice, index) => {
        noticeCount += 1;
        if (notice === null || typeof notice !== "object" || Array.isArray(notice)) {
          throw new Error(`${site}/${fileName} item ${index + 1}: expected an object`);
        }
        const platformCode = requiredString(
          notice["平台代码"],
          "平台代码",
          `${site}/${fileName} item ${index + 1}`,
        ).toLowerCase();
        if (platformCode !== site) {
          throw new Error(
            `${site}/${fileName} item ${index + 1}: 平台代码 is ${platformCode}, expected ${site}`,
          );
        }

        const values = notice["附件"];
        if (values === null || values === undefined) return;
        if (!Array.isArray(values)) {
          throw new Error(`${site}/${fileName} item ${index + 1}: 附件 must be an array`);
        }
        values.forEach((attachment, attachmentIndex) => {
          attachments.push(
            mapAttachment(attachment, notice, site, fileName, index, attachmentIndex, outputRoot),
          );
        });
      });
    }
  }

  const uniqueByKey = new Map();
  let duplicateCount = 0;
  for (const attachment of attachments) {
    const key = [
      attachment.dataSourceId,
      attachment.sourceNoticeId,
      attachmentIdentity(attachment),
    ].join("\u0000");
    if (uniqueByKey.has(key)) duplicateCount += 1;
    uniqueByKey.set(key, attachment);
  }

  return { attachments: [...uniqueByKey.values()], jsonFileCount, noticeCount, duplicateCount };
}

function printSummary(attachments, jsonFileCount, noticeCount, duplicateCount, options) {
  console.log(`Mode: ${options.commit ? "COMMIT" : "DRY RUN (no database writes)"}`);
  console.log(`Output root: ${options.outputRoot}`);
  console.log(`Validated JSON files: ${jsonFileCount}`);
  console.log(`Scanned notices: ${noticeCount}`);
  console.log(`Validated attachments: ${attachments.length}`);
  console.log(`Duplicate JSON attachments skipped: ${duplicateCount}`);

  for (const site of Object.keys(SITE_CONFIG)) {
    const siteAttachments = attachments.filter((attachment) => attachment.site === site);
    if (siteAttachments.length === 0) continue;
    const statusCounts = new Map();
    for (const attachment of siteAttachments) {
      statusCounts.set(
        attachment.parseStatus,
        (statusCounts.get(attachment.parseStatus) || 0) + 1,
      );
    }
    const statuses = [...statusCounts]
      .map(([status, count]) => `${status}=${count}`)
      .join(", ");
    console.log(
      `  ${site}: ${siteAttachments.length} attachments -> data_source_id=${SITE_CONFIG[site].dataSourceId}; ${statuses}`,
    );
  }
  console.log("source_file_id is not stored because raw_notice_attachment has no matching column.");
}

async function resolveParents(prisma, attachments, sites) {
  const conditions = sites.map((site) => ({
    dataSourceId: SITE_CONFIG[site].dataSourceId,
    sourceNoticeId: {
      in: [
        ...new Set(
          attachments
            .filter((attachment) => attachment.site === site)
            .map((attachment) => attachment.sourceNoticeId),
        ),
      ],
    },
  }));

  const parents = await prisma.rawNotice.findMany({
    where: { OR: conditions },
    select: { id: true, dataSourceId: true, sourceNoticeId: true },
  });
  const parentByKey = new Map(
    parents.map((parent) => [`${parent.dataSourceId}\u0000${parent.sourceNoticeId}`, parent]),
  );

  const missing = [];
  for (const attachment of attachments) {
    const key = `${attachment.dataSourceId}\u0000${attachment.sourceNoticeId}`;
    const parent = parentByKey.get(key);
    if (!parent) {
      missing.push(`data_source_id=${attachment.dataSourceId}, source_notice_id=${attachment.sourceNoticeId}`);
    } else {
      attachment.rawNoticeId = parent.id;
    }
  }
  if (missing.length > 0) {
    const sample = missing.slice(0, 10).join("; ");
    throw new Error(
      `${missing.length} parent raw_notice rows were not found (${sample}). Import raw_notice first.`,
    );
  }
}

function databaseIdentityKeys(attachment) {
  const parent = attachment.rawNoticeId.toString();
  return attachmentIdentityCandidates(attachment).map((identity) => `${parent}\u0000${identity}`);
}

function databaseData(attachment) {
  return {
    rawNoticeId: attachment.rawNoticeId,
    fileName: attachment.fileName,
    fileUrl: attachment.fileUrl,
    storagePath: attachment.storagePath,
    fileHash: attachment.fileHash,
    fileSizeBytes: attachment.fileSizeBytes,
    fileType: attachment.fileType,
    parseStatus: attachment.parseStatus,
  };
}

async function commitAttachments(prisma, attachments) {
  let inserted = 0;
  let updated = 0;

  await prisma.$transaction(
    async (transaction) => {
      const parentIds = [...new Set(attachments.map((attachment) => attachment.rawNoticeId))];
      const existingRows = await transaction.rawNoticeAttachment.findMany({
        where: { rawNoticeId: { in: parentIds } },
      });
      const existingByKey = new Map();
      for (const existing of existingRows) {
        for (const key of databaseIdentityKeys(existing)) {
          if (!existingByKey.has(key)) existingByKey.set(key, existing);
        }
      }

      const toCreate = [];
      for (const attachment of attachments) {
        const existing = databaseIdentityKeys(attachment)
          .map((key) => existingByKey.get(key))
          .find(Boolean);
        if (existing) {
          await transaction.rawNoticeAttachment.update({
            where: { id: existing.id },
            data: databaseData(attachment),
          });
          updated += 1;
        } else {
          toCreate.push(databaseData(attachment));
        }
      }

      if (toCreate.length > 0) {
        const result = await transaction.rawNoticeAttachment.createMany({ data: toCreate });
        inserted = result.count;
      }
    },
    { maxWait: 10_000, timeout: 120_000 },
  );

  console.log(`Commit completed: inserted=${inserted}, updated=${updated}.`);
}

async function main() {
  const options = parseArgs(process.argv.slice(2));
  if (options.help) {
    printHelp();
    return;
  }

  const sites = options.site === "all" ? Object.keys(SITE_CONFIG) : [options.site];
  const { attachments, jsonFileCount, noticeCount, duplicateCount } = readAttachments(options.outputRoot, sites);
  printSummary(attachments, jsonFileCount, noticeCount, duplicateCount, options);

  if (!options.commit) {
    console.log("Dry run complete. Add --commit to write these attachments to MySQL.");
    return;
  }

  loadDatabaseUrlFromDotEnv();
  if (!process.env.DATABASE_URL) {
    throw new Error("DATABASE_URL is not set and was not found in crawler_prisma/.env");
  }
  const { PrismaClient } = require("@prisma/client");
  const prisma = new PrismaClient();
  try {
    await resolveParents(prisma, attachments, sites);
    await commitAttachments(prisma, attachments);
  } finally {
    await prisma.$disconnect();
  }
}

main().catch((error) => {
  console.error(`Import failed: ${error.message}`);
  process.exitCode = 1;
});
