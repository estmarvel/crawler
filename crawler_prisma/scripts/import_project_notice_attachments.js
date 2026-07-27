#!/usr/bin/env node

"use strict";

const fs = require("node:fs");
const path = require("node:path");

const PROJECT_ROOT = path.resolve(__dirname, "..");

function printHelp() {
  console.log(`Usage:
  npm run import:project-notice-attachments -- [options]

Options:
  --commit           Write project_notice_attachment rows.
  --replace          Delete existing rows first, insert ids from 1, and reset
                     AUTO_INCREMENT to imported_count + 1.
  --raw-notice-ids=<ids>
                     Only process attachments belonging to these comma-separated
                     raw_notice ids. Cannot be combined with --replace.
  --batch-size=<n>   Rows per createMany batch (default: 200, max: 500).
  --help             Show this help.

Examples:
  npm run import:project-notice-attachments -- --replace
  npm run import:project-notice-attachments -- --commit --replace
`);
}

function parseArgs(argv) {
  const options = { commit: false, replace: false, batchSize: 200, rawNoticeIds: null };
  for (const arg of argv) {
    if (arg === "--commit") options.commit = true;
    else if (arg === "--replace") options.replace = true;
    else if (arg === "--help" || arg === "-h") options.help = true;
    else if (arg.startsWith("--raw-notice-ids=")) {
      const values = arg.slice("--raw-notice-ids=".length).split(",").map((value) => value.trim());
      if (values.length === 0 || values.some((value) => !/^\d+$/.test(value) || value === "0")) {
        throw new Error("--raw-notice-ids must contain comma-separated positive integers");
      }
      options.rawNoticeIds = [...new Set(values)].map((value) => BigInt(value));
    }
    else if (arg.startsWith("--batch-size=")) {
      options.batchSize = Number(arg.slice("--batch-size=".length));
    } else {
      throw new Error(`Unknown argument: ${arg}`);
    }
  }
  if (!Number.isInteger(options.batchSize) || options.batchSize < 1 || options.batchSize > 500) {
    throw new Error("--batch-size must be an integer from 1 to 500");
  }
  if (options.rawNoticeIds && options.replace) {
    throw new Error("--raw-notice-ids cannot be combined with --replace");
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

function chunks(values, size) {
  const result = [];
  for (let index = 0; index < values.length; index += size) {
    result.push(values.slice(index, index + size));
  }
  return result;
}

function assertMaxLength(value, max, field, context) {
  if (value !== null && [...value].length > max) {
    throw new Error(`${context}: ${field} exceeds ${max} characters`);
  }
}

function attachmentIdentity(row) {
  const identity = row.fileHash
    ? `hash:${row.fileHash}`
    : row.storagePath
      ? `path:${row.storagePath}`
      : row.fileUrl
        ? `url:${row.fileUrl}`
        : `name:${row.fileName}`;
  return `${row.noticeId}\u0000${identity}`;
}

function mapRawAttachments(rawAttachments) {
  const rows = [];
  for (const attachment of rawAttachments) {
    const context = `raw_notice_attachment.id=${attachment.id}`;
    const projectNoticeIds = [
      ...new Set(
        attachment.rawNotice.extractionResults
          .map((extraction) => extraction.projectNoticeId)
          .filter((id) => id !== null),
      ),
    ];
    if (projectNoticeIds.length === 0) {
      throw new Error(
        `${context}: parent notice is not linked; run import:project-notices -- --commit --replace first`,
      );
    }
    if (projectNoticeIds.length > 1) {
      throw new Error(
        `${context}: parent raw notice links to multiple project_notice ids: ${projectNoticeIds.join(", ")}`,
      );
    }

    const fileName = nullableString(attachment.fileName);
    const fileUrl = nullableString(attachment.fileUrl);
    const fileType = nullableString(attachment.fileType);
    const storagePath = nullableString(attachment.storagePath);
    const fileHash = nullableString(attachment.fileHash);
    const parseStatus = nullableString(attachment.parseStatus) || "PENDING";
    if (fileName === null) throw new Error(`${context}: file_name is required`);
    if (fileHash === null && storagePath === null && fileUrl === null && fileName === null) {
      throw new Error(`${context}: attachment has no usable identity`);
    }
    assertMaxLength(fileName, 191, "file_name", context);
    assertMaxLength(fileUrl, 1024, "file_url", context);
    assertMaxLength(fileType, 191, "file_type", context);
    assertMaxLength(storagePath, 1024, "storage_path", context);
    assertMaxLength(fileHash, 64, "file_hash", context);
    assertMaxLength(parseStatus, 32, "parse_status", context);

    const row = {
      rawAttachmentId: attachment.id,
      noticeId: projectNoticeIds[0],
      fileName,
      fileUrl,
      fileType,
      storagePath,
      fileHash,
      fileSizeBytes: attachment.fileSizeBytes,
      parseStatus,
    };
    row.identity = attachmentIdentity(row);
    rows.push(row);
  }

  const seen = new Map();
  for (const row of rows) {
    const previous = seen.get(row.identity);
    if (previous) {
      throw new Error(
        `Duplicate attachment identity ${row.identity}: raw ids ${previous.rawAttachmentId} and ${row.rawAttachmentId}`,
      );
    }
    seen.set(row.identity, row);
  }
  rows.sort((left, right) => {
    if (left.noticeId !== right.noticeId) return left.noticeId - right.noticeId;
    return left.rawAttachmentId < right.rawAttachmentId ? -1 : 1;
  });
  return rows;
}

function indexExisting(rows) {
  const byIdentity = new Map();
  for (const row of rows) {
    const mapped = {
      noticeId: row.noticeId,
      fileName: nullableString(row.fileName),
      fileUrl: nullableString(row.fileUrl),
      storagePath: nullableString(row.storagePath),
      fileHash: nullableString(row.fileHash),
    };
    const identity = attachmentIdentity(mapped);
    if (byIdentity.has(identity)) {
      throw new Error(`Existing project_notice_attachment duplicate: ${identity}`);
    }
    byIdentity.set(identity, row);
  }
  return byIdentity;
}

async function commitRows(prisma, rows, batchSize, replace) {
  await prisma.$transaction(
    async (transaction) => {
      let deleted = 0;
      if (replace) deleted = (await transaction.projectNoticeAttachment.deleteMany()).count;

      const existingRows = await transaction.projectNoticeAttachment.findMany({
        select: {
          id: true,
          noticeId: true,
          fileName: true,
          fileUrl: true,
          storagePath: true,
          fileHash: true,
        },
      });
      const existingByIdentity = indexExisting(existingRows);
      let updated = 0;
      if (!replace) {
        for (const row of rows) {
          const existing = existingByIdentity.get(row.identity);
          if (!existing) continue;
          await transaction.projectNoticeAttachment.update({
            where: { id: existing.id },
            data: {
              fileName: row.fileName,
              fileUrl: row.fileUrl,
              fileType: row.fileType,
              storagePath: row.storagePath,
              fileHash: row.fileHash,
              fileSizeBytes: row.fileSizeBytes,
              parseStatus: row.parseStatus,
            },
          });
          updated += 1;
        }
      }
      const missingRows = rows.filter((row) => !existingByIdentity.has(row.identity));
      const toCreate = replace
        ? missingRows.map((row, index) => ({ ...row, insertId: index + 1 }))
        : missingRows;

      let inserted = 0;
      for (const batch of chunks(toCreate, batchSize)) {
        const result = await transaction.projectNoticeAttachment.createMany({
          data: batch.map((row) => {
            const data = {
              noticeId: row.noticeId,
              fileName: row.fileName,
              fileUrl: row.fileUrl,
              fileType: row.fileType,
              storagePath: row.storagePath,
              fileHash: row.fileHash,
              fileSizeBytes: row.fileSizeBytes,
              parseStatus: row.parseStatus,
            };
            if (row.insertId !== undefined) data.id = row.insertId;
            return data;
          }),
        });
        inserted += result.count;
      }
      console.log(
        `Commit completed: deleted=${deleted}, inserted=${inserted}, existing_updated=${updated}.`,
      );
    },
    { maxWait: 10_000, timeout: 300_000 },
  );

  if (replace) {
    const nextAutoIncrement = rows.length + 1;
    await prisma.$executeRawUnsafe(
      `ALTER TABLE project_notice_attachment AUTO_INCREMENT = ${nextAutoIncrement}`,
    );
    console.log(
      `project_notice_attachment.id reset: inserted ids=1..${rows.length}, next AUTO_INCREMENT=${nextAutoIncrement}.`,
    );
  }
}

async function main() {
  const options = parseArgs(process.argv.slice(2));
  if (options.help) {
    printHelp();
    return;
  }
  loadDatabaseUrlFromDotEnv();
  if (!process.env.DATABASE_URL) {
    throw new Error("DATABASE_URL is not set and was not found in crawler_prisma/.env");
  }

  const { PrismaClient } = require("@prisma/client");
  const prisma = new PrismaClient();
  try {
    const rawAttachments = await prisma.rawNoticeAttachment.findMany({
      where: options.rawNoticeIds ? { rawNoticeId: { in: options.rawNoticeIds } } : undefined,
      select: {
        id: true,
        fileName: true,
        fileUrl: true,
        fileType: true,
        storagePath: true,
        fileHash: true,
        fileSizeBytes: true,
        parseStatus: true,
        rawNotice: {
          select: {
            extractionResults: {
              where: { projectNoticeId: { not: null } },
              select: { projectNoticeId: true },
            },
          },
        },
      },
      orderBy: { id: "asc" },
    });
    if (rawAttachments.length === 0) {
      console.log("No raw_notice_attachment rows found; nothing to import.");
      return;
    }
    const rows = mapRawAttachments(rawAttachments);
    const existingCount = await prisma.projectNoticeAttachment.count();
    const noticeIds = new Set(rows.map((row) => row.noticeId));
    const statusCounts = {};
    const typeCounts = {};
    for (const row of rows) {
      statusCounts[row.parseStatus] = (statusCounts[row.parseStatus] || 0) + 1;
      const type = row.fileType || "NULL";
      typeCounts[type] = (typeCounts[type] || 0) + 1;
    }

    console.log(
      `Mode: ${options.commit ? (options.replace ? "COMMIT + REPLACE" : "COMMIT") : "DRY RUN (no database writes)"}`,
    );
    console.log(`raw_notice_attachment rows read: ${rawAttachments.length}`);
    if (options.rawNoticeIds) {
      console.log(`raw_notice scope: ${options.rawNoticeIds.map(String).join(",")}`);
    }
    console.log(`Validated project_notice_attachment rows: ${rows.length}`);
    console.log(`Parent project_notice rows referenced: ${noticeIds.size}`);
    console.log(
      `Parse status counts: ${Object.entries(statusCounts)
        .map(([status, count]) => `${status}=${count}`)
        .join(", ")}`,
    );
    console.log(
      `File type counts: ${Object.entries(typeCounts)
        .map(([type, count]) => `${type}=${count}`)
        .join(", ")}`,
    );
    console.log(`Existing project_notice_attachment rows: ${existingCount}`);
    if (!options.commit) {
      console.log(options.rawNoticeIds
        ? "Dry run complete. Add --commit with the same scope to upsert affected attachments."
        : "Dry run complete. Add --commit --replace to replace attachments and reset ids from 1.");
      return;
    }
    await commitRows(prisma, rows, options.batchSize, options.replace);
  } finally {
    await prisma.$disconnect();
  }
}

main().catch((error) => {
  console.error(`Import failed: ${error.message}`);
  process.exitCode = 1;
});
