#!/usr/bin/env node

"use strict";

const fs = require("node:fs");
const path = require("node:path");

const PROJECT_ROOT = path.resolve(__dirname, "..");
const DEFAULT_OUTPUT_ROOT = path.resolve(PROJECT_ROOT, "../Crawler_Scrapy/output");

// These IDs are the existing foreign keys confirmed for this crawl run.
const SITE_CONFIG = Object.freeze({
  huaxin: Object.freeze({ dataSourceId: 6, crawlTaskId: "1" }),
  jiubang: Object.freeze({ dataSourceId: 14, crawlTaskId: "2" }),
});

function printHelp() {
  console.log(`Usage:
  npm run import:raw-notices -- [options]

Options:
  --commit              Write to MySQL. Without it, only validate and summarize.
  --site=<name>         all, huaxin, or jiubang (default: all).
  --crawl-task-id=<id>  Override crawl_task_id; requires one explicit --site.
  --output-root=<path>  Crawler output directory (default: ${DEFAULT_OUTPUT_ROOT}).
  --batch-size=<n>      Rows per INSERT batch (default: 200, max: 500).
  --help                Show this help.

Examples:
  npm run import:raw-notices
  npm run import:raw-notices -- --site=huaxin
  npm run import:raw-notices -- --commit
`);
}

function parseArgs(argv) {
  const options = {
    commit: false,
    site: "all",
    outputRoot: DEFAULT_OUTPUT_ROOT,
    batchSize: 200,
    crawlTaskId: null,
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
    } else if (arg.startsWith("--batch-size=")) {
      options.batchSize = Number(arg.slice("--batch-size=".length));
    } else if (arg.startsWith("--crawl-task-id=")) {
      options.crawlTaskId = arg.slice("--crawl-task-id=".length).trim();
    } else {
      throw new Error(`Unknown argument: ${arg}`);
    }
  }

  if (!["all", ...Object.keys(SITE_CONFIG)].includes(options.site)) {
    throw new Error(`Invalid --site value: ${options.site}`);
  }
  if (!Number.isInteger(options.batchSize) || options.batchSize < 1 || options.batchSize > 500) {
    throw new Error("--batch-size must be an integer from 1 to 500");
  }
  if (options.crawlTaskId !== null && !/^[1-9][0-9]*$/.test(options.crawlTaskId)) {
    throw new Error("--crawl-task-id must be a positive integer");
  }
  if (options.crawlTaskId !== null && options.site === "all") {
    throw new Error("--crawl-task-id requires one explicit --site");
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

// Preserve the crawler's local wall-clock value when writing to MySQL DATETIME.
// Avoiding JavaScript Date here prevents an accidental UTC offset conversion.
function mysqlDateTime(value, field, context, required = false) {
  const text = nullableString(value);
  if (text === null) {
    if (required) throw new Error(`${context}: missing required field ${field}`);
    return null;
  }

  const match = text.match(
    /^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2}):(\d{2})(?:\.(\d{1,6}))?$/,
  );
  if (!match) throw new Error(`${context}: invalid ${field}: ${text}`);

  const [, year, month, day, hour, minute, second, fraction = ""] = match;
  const numbers = [month, day, hour, minute, second].map(Number);
  const [monthNumber, dayNumber, hourNumber, minuteNumber, secondNumber] = numbers;
  if (
    monthNumber < 1 ||
    monthNumber > 12 ||
    dayNumber < 1 ||
    dayNumber > 31 ||
    hourNumber > 23 ||
    minuteNumber > 59 ||
    secondNumber > 59
  ) {
    throw new Error(`${context}: invalid ${field}: ${text}`);
  }

  const milliseconds = fraction.padEnd(3, "0").slice(0, 3);
  return `${year}-${month}-${day} ${hour}:${minute}:${second}.${milliseconds}`;
}

function mapRawNotice(source, site, fileName, index, crawlTaskId) {
  const config = SITE_CONFIG[site];
  const context = `${site}/${fileName} item ${index + 1}`;
  const platformCode = requiredString(source["平台代码"], "平台代码", context).toLowerCase();
  if (platformCode !== site) {
    throw new Error(`${context}: 平台代码 is ${platformCode}, expected ${site}`);
  }

  const noticeType = requiredString(source["公告类型"], "公告类型", context).toUpperCase();
  const sourceUrl = requiredString(source["详情页链接"], "详情页链接", context);
  const sourceNoticeId = requiredString(source["公告ID"], "公告ID", context);
  const title = nullableString(source["公告标题"]);
  const parseStatus = (nullableString(source["解析状态"]) || "PENDING").toUpperCase();
  const fingerprint = nullableString(source["内容指纹"]);

  // PLAN records intentionally skip 公告正文 until the spider can fetch it.
  const rawText =
    noticeType === "PLAN"
      ? null
      : nullableString(source["公告正文"]) || nullableString(source["公告内容"]);

  assertMaxLength(sourceUrl, 1024, "详情页链接/source_url", context);
  assertMaxLength(sourceNoticeId, 256, "公告ID/source_notice_id", context);
  assertMaxLength(title, 512, "公告标题/title", context);
  assertMaxLength(parseStatus, 32, "解析状态/parse_status", context);
  assertMaxLength(fingerprint, 64, "内容指纹/fingerprint", context);

  return {
    site,
    noticeType,
    dataSourceId: config.dataSourceId,
    crawlTaskId: crawlTaskId || config.crawlTaskId,
    sourceUrl,
    sourceNoticeId,
    title,
    rawHtml: null,
    rawText,
    publishDate: mysqlDateTime(
      source["发布时间"] ?? source["发布日期"],
      "发布时间/发布日期",
      context,
    ),
    crawlTime: mysqlDateTime(source["爬虫时间"], "爬虫时间", context, true),
    parseStatus,
    fingerprint,
  };
}

function readRows(outputRoot, sites, crawlTaskId) {
  const rowsByKey = new Map();
  const fileCounts = new Map();
  let duplicateCount = 0;

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
      const filePath = path.join(jsonDirectory, fileName);
      let values;
      try {
        values = JSON.parse(fs.readFileSync(filePath, "utf8"));
      } catch (error) {
        throw new Error(`Cannot parse ${filePath}: ${error.message}`);
      }
      if (!Array.isArray(values)) throw new Error(`${filePath}: top-level JSON must be an array`);

      fileCounts.set(`${site}/${fileName}`, values.length);
      values.forEach((value, index) => {
        if (value === null || typeof value !== "object" || Array.isArray(value)) {
          throw new Error(`${site}/${fileName} item ${index + 1}: expected an object`);
        }
        const row = mapRawNotice(value, site, fileName, index, crawlTaskId);
        const key = `${row.dataSourceId}\u0000${row.sourceNoticeId}`;
        if (rowsByKey.has(key)) duplicateCount += 1;
        // Keep the last occurrence because it normally contains the newest detail-page payload.
        rowsByKey.set(key, row);
      });
    }
  }
  return { rows: [...rowsByKey.values()], fileCounts, duplicateCount };
}

function printSummary(rows, fileCounts, duplicateCount, options) {
  console.log(`Mode: ${options.commit ? "COMMIT" : "DRY RUN (no database writes)"}`);
  console.log(`Output root: ${options.outputRoot}`);
  console.log(`Validated JSON files: ${fileCounts.size}`);
  console.log(`Validated notices: ${rows.length}`);
  console.log(`Duplicate JSON notices skipped: ${duplicateCount}`);

  for (const site of Object.keys(SITE_CONFIG)) {
    const siteRows = rows.filter((row) => row.site === site);
    if (siteRows.length === 0) continue;
    const config = SITE_CONFIG[site];
    const crawlTaskId = siteRows[0].crawlTaskId;
    const typeCounts = new Map();
    for (const row of siteRows) {
      typeCounts.set(row.noticeType, (typeCounts.get(row.noticeType) || 0) + 1);
    }
    const types = [...typeCounts.entries()]
      .sort(([left], [right]) => left.localeCompare(right))
      .map(([type, count]) => `${type}=${count}`)
      .join(", ");
    console.log(
      `  ${site}: ${siteRows.length} rows -> data_source_id=${config.dataSourceId}, crawl_task_id=${crawlTaskId}; ${types}`,
    );
  }

  const planCount = rows.filter((row) => row.noticeType === "PLAN").length;
  console.log(`PLAN raw_text forced to NULL: ${planCount}`);
}

async function validateForeignKeys(prisma, rows) {
  const configs = [...new Map(rows.map((row) => [row.site, {
    site: row.site,
    dataSourceId: row.dataSourceId,
    crawlTaskId: row.crawlTaskId,
  }])).values()];
  const dataSourceIds = configs.map((config) => config.dataSourceId);
  const crawlTaskIds = configs.map((config) => BigInt(config.crawlTaskId));

  const [dataSources, crawlTasks] = await Promise.all([
    prisma.dataSource.findMany({ where: { id: { in: dataSourceIds } } }),
    prisma.crawlTask.findMany({ where: { id: { in: crawlTaskIds } } }),
  ]);
  const dataSourceById = new Map(dataSources.map((source) => [source.id, source]));
  const taskById = new Map(crawlTasks.map((task) => [task.id.toString(), task]));

  for (const config of configs) {
    const dataSource = dataSourceById.get(config.dataSourceId);
    if (!dataSource) throw new Error(`data_source.id=${config.dataSourceId} does not exist`);

    const task = taskById.get(config.crawlTaskId);
    if (!task) throw new Error(`crawl_task.id=${config.crawlTaskId} does not exist`);
    if (task.dataSourceId !== config.dataSourceId) {
      throw new Error(
        `crawl_task.id=${config.crawlTaskId} belongs to data_source_id=${task.dataSourceId}, expected ${config.dataSourceId}`,
      );
    }
  }
}

function chunks(values, size) {
  const result = [];
  for (let index = 0; index < values.length; index += size) {
    result.push(values.slice(index, index + size));
  }
  return result;
}

async function writeBatch(transaction, rows) {
  const columnsPerRow = 11;
  const placeholders = rows
    .map(() => `(${new Array(columnsPerRow).fill("?").join(", ")})`)
    .join(",\n");
  const sql = `
    INSERT INTO raw_notice (
      data_source_id,
      crawl_task_id,
      source_url,
      source_notice_id,
      title,
      raw_html,
      raw_text,
      publish_date,
      crawl_time,
      parse_status,
      fingerprint
    ) VALUES
    ${placeholders}
    ON DUPLICATE KEY UPDATE
      source_url = VALUES(source_url),
      title = VALUES(title),
      raw_text = VALUES(raw_text),
      publish_date = VALUES(publish_date),
      crawl_time = VALUES(crawl_time),
      parse_status = VALUES(parse_status),
      fingerprint = VALUES(fingerprint)
  `;

  const parameters = rows.flatMap((row) => [
    row.dataSourceId,
    row.crawlTaskId,
    row.sourceUrl,
    row.sourceNoticeId,
    row.title,
    row.rawHtml,
    row.rawText,
    row.publishDate,
    row.crawlTime,
    row.parseStatus,
    row.fingerprint,
  ]);

  // The query text contains only static identifiers/placeholders; JSON values
  // are passed separately as bound parameters.
  return transaction.$executeRawUnsafe(sql, ...parameters);
}

function noticeKey(row) {
  return `${row.dataSourceId}\u0000${row.sourceNoticeId}`;
}

function contentChanged(previous, current) {
  if (previous.fingerprint && current.fingerprint) return previous.fingerprint !== current.fingerprint;
  const previousPublishDate = previous.publishDate
    ? previous.publishDate.toISOString().slice(0, 23).replace("T", " ")
    : null;
  return (
    previous.sourceUrl !== current.sourceUrl ||
    previous.title !== current.title ||
    previous.rawText !== current.rawText ||
    previousPublishDate !== current.publishDate
  );
}

async function commitRows(rows, batchSize) {
  loadDatabaseUrlFromDotEnv();
  if (!process.env.DATABASE_URL) {
    throw new Error("DATABASE_URL is not set and was not found in crawler_prisma/.env");
  }

  const { PrismaClient } = require("@prisma/client");
  const prisma = new PrismaClient();
  try {
    await validateForeignKeys(prisma, rows);
    const existingRows = await prisma.rawNotice.findMany({
      where: {
        OR: [...new Set(rows.map((row) => row.dataSourceId))].map((dataSourceId) => ({
          dataSourceId,
          sourceNoticeId: {
            in: rows.filter((row) => row.dataSourceId === dataSourceId).map((row) => row.sourceNoticeId),
          },
        })),
      },
      select: {
        id: true,
        dataSourceId: true,
        sourceNoticeId: true,
        sourceUrl: true,
        title: true,
        rawText: true,
        publishDate: true,
        fingerprint: true,
      },
    });
    const existingByKey = new Map(existingRows.map((row) => [noticeKey(row), row]));
    const batches = chunks(rows, batchSize);
    let affectedRows = 0;
    let occurrenceCount = 0;

    await prisma.$transaction(
      async (transaction) => {
        for (let index = 0; index < batches.length; index += 1) {
          affectedRows += await writeBatch(transaction, batches[index]);
          console.log(`  Wrote batch ${index + 1}/${batches.length} (${batches[index].length} rows)`);
        }
        const storedRows = await transaction.rawNotice.findMany({
          where: {
            OR: [...new Set(rows.map((row) => row.dataSourceId))].map((dataSourceId) => ({
              dataSourceId,
              sourceNoticeId: {
                in: rows.filter((row) => row.dataSourceId === dataSourceId).map((row) => row.sourceNoticeId),
              },
            })),
          },
          select: { id: true, dataSourceId: true, sourceNoticeId: true },
        });
        const storedByKey = new Map(storedRows.map((row) => [noticeKey(row), row]));
        const occurrences = rows.map((row) => {
          const stored = storedByKey.get(noticeKey(row));
          if (!stored) throw new Error(`Stored raw_notice cannot be resolved: ${noticeKey(row)}`);
          const previous = existingByKey.get(noticeKey(row));
          return {
            crawlTaskId: BigInt(row.crawlTaskId),
            rawNoticeId: stored.id,
            isNew: !previous,
            isUpdated: previous ? contentChanged(previous, row) : false,
            contentHash: row.fingerprint,
          };
        });
        occurrenceCount = (
          await transaction.crawlTaskNotice.createMany({ data: occurrences, skipDuplicates: true })
        ).count;
      },
      { maxWait: 10_000, timeout: 300_000 },
    );

    console.log(`Commit completed: ${rows.length} source rows processed atomically.`);
    console.log(`Task notice occurrences inserted: ${occurrenceCount}.`);
    console.log(`MySQL affected-row count: ${affectedRows} (updates may count as 2).`);
  } finally {
    await prisma.$disconnect();
  }
}

async function main() {
  const options = parseArgs(process.argv.slice(2));
  if (options.help) {
    printHelp();
    return;
  }

  const sites = options.site === "all" ? Object.keys(SITE_CONFIG) : [options.site];
  const { rows, fileCounts, duplicateCount } = readRows(options.outputRoot, sites, options.crawlTaskId);
  printSummary(rows, fileCounts, duplicateCount, options);

  if (!options.commit) {
    console.log("Dry run complete. Add --commit to write these rows to MySQL.");
    return;
  }
  await commitRows(rows, options.batchSize);
}

main().catch((error) => {
  console.error(`Import failed: ${error.message}`);
  process.exitCode = 1;
});
