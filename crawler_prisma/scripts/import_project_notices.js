#!/usr/bin/env node

"use strict";

const fs = require("node:fs");
const path = require("node:path");

const PROJECT_ROOT = path.resolve(__dirname, "..");
const DEFAULT_MAPPING_ROOT = path.resolve(
  PROJECT_ROOT,
  "../Crawler_Scrapy/output/project_identity_mapping",
);
const SITE_MAPPING_FILES = Object.freeze([
  "huaxin_project_mapping.json",
  "jiubang_project_mapping.json",
]);

function printHelp() {
  console.log(`Usage:
  npm run import:project-notices -- [options]

Options:
  --commit                 Write project_notice rows and update
                           notice_extraction.project_notice_id.
  --replace                Replace all existing project_notice rows first.
                           Dependent business tables must be empty. New rows
                           receive ids from 1 and AUTO_INCREMENT is reset.
  --clear-only             Only clear project_notice and its extraction links.
                           Requires --replace; useful before resetting project.id.
  --mapping-root=<path>    Project relationship mapping directory.
                           Default: ${DEFAULT_MAPPING_ROOT}
  --site=<site>            Read only one mapping file (huaxin or jiubang).
  --batch-size=<n>         Batch size (default: 200, max: 500).
  --help                   Show this help.

Examples:
  npm run import:project-notices -- --replace
  npm run import:project-notices -- --commit --replace
  npm run import:project-notices -- --commit --replace --clear-only
`);
}

function parseArgs(argv) {
  const options = {
    commit: false,
    replace: false,
    clearOnly: false,
    mappingRoot: DEFAULT_MAPPING_ROOT,
    batchSize: 200,
    site: null,
  };
  for (const arg of argv) {
    if (arg === "--commit") options.commit = true;
    else if (arg === "--replace") options.replace = true;
    else if (arg === "--clear-only") options.clearOnly = true;
    else if (arg === "--help" || arg === "-h") options.help = true;
    else if (arg.startsWith("--mapping-root=")) {
      options.mappingRoot = path.resolve(arg.slice("--mapping-root=".length));
    } else if (arg.startsWith("--site=")) {
      options.site = arg.slice("--site=".length);
      if (!["huaxin", "jiubang"].includes(options.site)) {
        throw new Error("--site must be huaxin or jiubang");
      }
    } else if (arg.startsWith("--batch-size=")) {
      options.batchSize = Number(arg.slice("--batch-size=".length));
    } else {
      throw new Error(`Unknown argument: ${arg}`);
    }
  }
  if (!Number.isInteger(options.batchSize) || options.batchSize < 1 || options.batchSize > 500) {
    throw new Error("--batch-size must be an integer from 1 to 500");
  }
  if (options.clearOnly && !options.replace) {
    throw new Error("--clear-only requires --replace");
  }
  if (options.site && options.replace) {
    throw new Error("--site cannot be combined with --replace");
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

function noticeKey(sourceSite, sourceNoticeId) {
  return `${sourceSite}\u0000${sourceNoticeId}`;
}

function canonicalJson(value) {
  if (Array.isArray(value)) return value.map(canonicalJson);
  if (value && typeof value === "object") {
    return Object.fromEntries(
      Object.keys(value).sort().map((key) => [key, canonicalJson(value[key])]),
    );
  }
  return value;
}

function sameDate(left, right) {
  return (left?.getTime?.() ?? null) === (right?.getTime?.() ?? null);
}

function projectNoticeChanged(existing, row) {
  return (
    existing.projectId !== row.projectId ||
    existing.noticeType !== row.noticeType ||
    existing.title !== row.title ||
    existing.content !== row.content ||
    JSON.stringify(canonicalJson(existing.structuredData)) !== JSON.stringify(canonicalJson(row.structuredData)) ||
    !sameDate(existing.publishDate, row.publishDate) ||
    existing.sourceSite !== row.sourceSite ||
    existing.sourceUrl !== row.sourceUrl ||
    existing.sourceNoticeId !== row.sourceNoticeId ||
    !sameDate(existing.crawlTime, row.crawlTime)
  );
}

function readMatchedMappings(mappingRoot, site = null) {
  const mappings = [];
  const fileNames = site ? [`${site}_project_mapping.json`] : SITE_MAPPING_FILES;
  for (const fileName of fileNames) {
    const filePath = path.join(mappingRoot, fileName);
    if (!fs.existsSync(filePath)) {
      throw new Error(
        `Mapping file does not exist: ${filePath}. Run npm run import:projects -- --replace first.`,
      );
    }
    const document = JSON.parse(fs.readFileSync(filePath, "utf8"));
    if (!Array.isArray(document.records)) {
      throw new Error(`${filePath}: records must be an array`);
    }
    for (const record of document.records) {
      if (!["MATCHED", "STANDALONE_PROJECT"].includes(record["匹配状态"])) continue;
      const extractionId = nullableString(record.notice_extraction_id);
      const projectCode = nullableString(record["项目编号"]);
      const standaloneProjectName = nullableString(record["独立项目名称"]);
      if (extractionId === null || !/^\d+$/u.test(extractionId)) {
        throw new Error(`${filePath}: invalid notice_extraction_id ${extractionId}`);
      }
      if (
        record["匹配状态"] === "MATCHED" &&
        projectCode === null
      ) {
        throw new Error(`${filePath}: MATCHED record has no 项目编号`);
      }
      if (
        record["匹配状态"] === "STANDALONE_PROJECT" &&
        standaloneProjectName === null
      ) {
        throw new Error(`${filePath}: STANDALONE_PROJECT record has no 独立项目名称`);
      }
      mappings.push({
        extractionId: BigInt(extractionId),
        extractionIdText: extractionId,
        projectCode,
        standaloneProjectName,
        matchStatus: record["匹配状态"],
        site: nullableString(record["平台代码"]),
        sourceNoticeId: nullableString(record["公告ID"]),
        noticeType: nullableString(record["公告类型"]),
      });
    }
  }

  const seenExtractionIds = new Set();
  for (const mapping of mappings) {
    if (seenExtractionIds.has(mapping.extractionIdText)) {
      throw new Error(`Duplicate notice_extraction_id in mappings: ${mapping.extractionIdText}`);
    }
    seenExtractionIds.add(mapping.extractionIdText);
  }
  return mappings;
}

async function loadExtractions(prisma, mappings, batchSize) {
  const rows = [];
  for (const batch of chunks(mappings, batchSize)) {
    rows.push(
      ...(await prisma.noticeExtraction.findMany({
        where: { id: { in: batch.map((mapping) => mapping.extractionId) } },
        select: {
          id: true,
          noticeType: true,
          extractedFields: true,
          projectNoticeId: true,
          rawNotice: {
            select: {
              sourceNoticeId: true,
              title: true,
              rawText: true,
              publishDate: true,
              sourceUrl: true,
              crawlTime: true,
              dataSource: { select: { name: true } },
            },
          },
        },
      })),
    );
  }
  return rows;
}

async function loadProjects(prisma, projectCodes, batchSize, includeStandalone) {
  const rows = [];
  for (const batch of chunks(projectCodes, batchSize)) {
    rows.push(
      ...(await prisma.project.findMany({
        where: { projectCode: { in: batch } },
        select: { id: true, projectCode: true },
      })),
    );
  }
  if (includeStandalone) {
    rows.push(
      ...(await prisma.project.findMany({
        where: { projectCode: null },
        select: { id: true, projectCode: true, projectName: true },
      })),
    );
  }
  return rows;
}

function buildImportRows(mappings, extractions, projects) {
  const extractionById = new Map(extractions.map((row) => [row.id.toString(), row]));
  const projectIdsByCode = new Map();
  const standaloneProjectIdsByName = new Map();
  for (const project of projects) {
    if (project.projectCode === null) {
      if (!standaloneProjectIdsByName.has(project.projectName)) {
        standaloneProjectIdsByName.set(project.projectName, []);
      }
      standaloneProjectIdsByName.get(project.projectName).push(project.id);
      continue;
    }
    if (!projectIdsByCode.has(project.projectCode)) projectIdsByCode.set(project.projectCode, []);
    projectIdsByCode.get(project.projectCode).push(project.id);
  }

  const rows = [];
  for (const mapping of mappings) {
    const extraction = extractionById.get(mapping.extractionIdText);
    if (!extraction) {
      throw new Error(`notice_extraction.id=${mapping.extractionIdText} does not exist`);
    }
    const projectIds =
      mapping.projectCode !== null
        ? projectIdsByCode.get(mapping.projectCode) || []
        : standaloneProjectIdsByName.get(mapping.standaloneProjectName) || [];
    if (projectIds.length === 0) {
      throw new Error(
        mapping.projectCode !== null
          ? `project_code=${mapping.projectCode} does not exist. Import the rebuilt project table first.`
          : `standalone plan project=${mapping.standaloneProjectName} does not exist. Import the rebuilt project table first.`,
      );
    }
    if (projectIds.length > 1) {
      throw new Error(
        mapping.projectCode !== null
          ? `project_code=${mapping.projectCode} occurs ${projectIds.length} times in project`
          : `standalone plan project=${mapping.standaloneProjectName} occurs ${projectIds.length} times in project`,
      );
    }
    if (mapping.noticeType !== extraction.noticeType) {
      throw new Error(
        `notice_extraction.id=${mapping.extractionIdText}: mapping notice type ${mapping.noticeType} does not match ${extraction.noticeType}`,
      );
    }

    const rawNotice = extraction.rawNotice;
    const sourceSite = nullableString(rawNotice.dataSource.name);
    const sourceNoticeId = nullableString(rawNotice.sourceNoticeId);
    const title = nullableString(rawNotice.title);
    const sourceUrl = nullableString(rawNotice.sourceUrl);
    if (sourceSite === null) {
      throw new Error(`notice_extraction.id=${mapping.extractionIdText}: data_source.name is empty`);
    }
    if (sourceNoticeId === null) {
      throw new Error(`notice_extraction.id=${mapping.extractionIdText}: source_notice_id is empty`);
    }
    if (title === null) {
      throw new Error(`notice_extraction.id=${mapping.extractionIdText}: title is empty`);
    }
    if ([...title].length > 512) {
      throw new Error(`notice_extraction.id=${mapping.extractionIdText}: title exceeds 512 characters`);
    }
    if ([...sourceSite].length > 191) {
      throw new Error(`notice_extraction.id=${mapping.extractionIdText}: source_site exceeds 191 characters`);
    }
    if ([...sourceNoticeId].length > 191) {
      throw new Error(
        `notice_extraction.id=${mapping.extractionIdText}: source_notice_id exceeds 191 characters`,
      );
    }
    if (sourceUrl !== null && [...sourceUrl].length > 1024) {
      throw new Error(`notice_extraction.id=${mapping.extractionIdText}: source_url exceeds 1024 characters`);
    }
    if (
      extraction.extractedFields === null ||
      typeof extraction.extractedFields !== "object" ||
      Array.isArray(extraction.extractedFields)
    ) {
      throw new Error(
        `notice_extraction.id=${mapping.extractionIdText}: extracted_fields must be a JSON object`,
      );
    }

    rows.push({
      extractionId: extraction.id,
      currentProjectNoticeId: extraction.projectNoticeId,
      projectId: projectIds[0],
      noticeType: extraction.noticeType,
      title,
      content: rawNotice.rawText,
      structuredData: extraction.extractedFields,
      publishDate: rawNotice.publishDate,
      sourceSite,
      sourceUrl,
      sourceNoticeId,
      crawlTime: rawNotice.crawlTime,
      key: noticeKey(sourceSite, sourceNoticeId),
    });
  }

  const seenKeys = new Map();
  for (const row of rows) {
    const previous = seenKeys.get(row.key);
    if (previous) {
      throw new Error(
        `Duplicate source_site/source_notice_id in import set: ${row.sourceSite}/${row.sourceNoticeId} (extractions ${previous.extractionId} and ${row.extractionId})`,
      );
    }
    seenKeys.set(row.key, row);
  }
  return rows;
}

async function dependentCounts(client) {
  return [
    ["project_notice_attachment", await client.projectNoticeAttachment.count()],
    [
      "project_company_relation.notice_id",
      await client.projectCompanyRelation.count({ where: { noticeId: { not: null } } }),
    ],
    [
      "project_requirement.notice_id",
      await client.projectRequirement.count({ where: { noticeId: { not: null } } }),
    ],
    ["contract.notice_id", await client.contract.count({ where: { noticeId: { not: null } } })],
  ];
}

function indexExistingProjectNotices(rows) {
  const byKey = new Map();
  for (const row of rows) {
    const sourceSite = nullableString(row.sourceSite);
    const sourceNoticeId = nullableString(row.sourceNoticeId);
    if (sourceSite === null || sourceNoticeId === null) continue;
    const key = noticeKey(sourceSite, sourceNoticeId);
    if (byKey.has(key)) {
      throw new Error(`Existing project_notice duplicate: ${sourceSite}/${sourceNoticeId}`);
    }
    byKey.set(key, row);
  }
  return byKey;
}

async function updateExtractionLinks(transaction, links, batchSize) {
  let updated = 0;
  for (const batch of chunks(links, batchSize)) {
    const cases = batch.map(() => "WHEN ? THEN ?").join(" ");
    const inList = batch.map(() => "?").join(", ");
    const sql = `
      UPDATE notice_extraction
      SET project_notice_id = CASE id ${cases} ELSE project_notice_id END
      WHERE id IN (${inList})
    `;
    const parameters = [
      ...batch.flatMap((link) => [link.extractionId, link.projectNoticeId]),
      ...batch.map((link) => link.extractionId),
    ];
    updated += await transaction.$executeRawUnsafe(sql, ...parameters);
  }
  return updated;
}

async function commitRows(prisma, rows, batchSize, replace) {
  await prisma.$transaction(
    async (transaction) => {
      let deleted = 0;
      if (replace) {
        const blockers = (await dependentCounts(transaction)).filter(([, count]) => count > 0);
        if (blockers.length > 0) {
          throw new Error(
            `--replace refused because dependent rows exist: ${blockers
              .map(([table, count]) => `${table}=${count}`)
              .join(", ")}`,
          );
        }
        await transaction.noticeExtraction.updateMany({
          where: { projectNoticeId: { not: null } },
          data: { projectNoticeId: null },
        });
        deleted = (await transaction.projectNotice.deleteMany()).count;
      }

      const existingRows = await transaction.projectNotice.findMany({
        select: {
          id: true, projectId: true, noticeType: true, title: true, content: true,
          structuredData: true, publishDate: true, sourceSite: true, sourceUrl: true,
          sourceNoticeId: true, crawlTime: true,
        },
      });
      const existingByKey = indexExistingProjectNotices(existingRows);
      const missingRows = [];
      let updated = 0;
      for (const row of rows) {
        const existing = existingByKey.get(row.key);
        if (!existing) {
          missingRows.push(row);
          continue;
        }
        if (!projectNoticeChanged(existing, row)) continue;
        await transaction.projectNotice.update({
          where: { id: existing.id },
          data: {
            projectId: row.projectId,
            noticeType: row.noticeType,
            title: row.title,
            content: row.content,
            structuredData: row.structuredData,
            publishDate: row.publishDate,
            sourceSite: row.sourceSite,
            sourceUrl: row.sourceUrl,
            sourceNoticeId: row.sourceNoticeId,
            crawlTime: row.crawlTime,
          },
        });
        updated += 1;
      }
      const toCreate = replace
        ? missingRows.map((row, index) => ({ ...row, insertId: index + 1 }))
        : missingRows;

      let inserted = 0;
      for (const batch of chunks(toCreate, batchSize)) {
        const result = await transaction.projectNotice.createMany({
          data: batch.map((row) => {
            const data = {
              projectId: row.projectId,
              noticeType: row.noticeType,
              title: row.title,
              content: row.content,
              structuredData: row.structuredData,
              publishDate: row.publishDate,
              sourceSite: row.sourceSite,
              sourceUrl: row.sourceUrl,
              sourceNoticeId: row.sourceNoticeId,
              crawlTime: row.crawlTime,
            };
            if (row.insertId !== undefined) data.id = row.insertId;
            return data;
          }),
        });
        inserted += result.count;
      }

      const allProjectNotices = await transaction.projectNotice.findMany({
        select: { id: true, projectId: true, sourceSite: true, sourceNoticeId: true },
      });
      const projectNoticeByKey = indexExistingProjectNotices(allProjectNotices);
      const links = rows.map((row) => {
        const projectNotice = projectNoticeByKey.get(row.key);
        if (!projectNotice) {
          throw new Error(`Inserted project_notice cannot be found by key: ${row.key}`);
        }
        if (projectNotice.projectId !== row.projectId) {
          throw new Error(`project_notice key ${row.key} resolved to the wrong project_id`);
        }
        return {
          extractionId: row.extractionId,
          projectNoticeId: projectNotice.id,
          currentProjectNoticeId: row.currentProjectNoticeId,
        };
      }).filter((link) => link.currentProjectNoticeId !== link.projectNoticeId);
      await updateExtractionLinks(transaction, links, batchSize);

      const linkedCount = await transaction.noticeExtraction.count({
        where: {
          id: { in: rows.map((row) => row.extractionId) },
          projectNoticeId: { not: null },
        },
      });
      if (linkedCount !== rows.length) {
        throw new Error(`Only ${linkedCount}/${rows.length} notice_extraction rows were linked`);
      }
      console.log(
        `Commit completed: deleted=${deleted}, inserted=${inserted}, existing_updated=${updated}, existing_unchanged=${rows.length - inserted - updated}, extraction_links_total=${linkedCount}, extraction_links_updated=${links.length}.`,
      );
    },
    { maxWait: 10_000, timeout: 300_000 },
  );
  if (replace) {
    const nextAutoIncrement = rows.length + 1;
    await prisma.$executeRawUnsafe(
      `ALTER TABLE project_notice AUTO_INCREMENT = ${nextAutoIncrement}`,
    );
    console.log(
      `project_notice.id reset: inserted ids=1..${rows.length}, next AUTO_INCREMENT=${nextAutoIncrement}.`,
    );
  }
}

async function clearProjectNotices(prisma) {
  await prisma.$transaction(
    async (transaction) => {
      const blockers = (await dependentCounts(transaction)).filter(([, count]) => count > 0);
      if (blockers.length > 0) {
        throw new Error(
          `--clear-only refused because dependent rows exist: ${blockers
            .map(([table, count]) => `${table}=${count}`)
            .join(", ")}`,
        );
      }
      const unlinked = (
        await transaction.noticeExtraction.updateMany({
          where: { projectNoticeId: { not: null } },
          data: { projectNoticeId: null },
        })
      ).count;
      const deleted = (await transaction.projectNotice.deleteMany()).count;
      console.log(
        `Clear completed: project_notice deleted=${deleted}, notice_extraction links cleared=${unlinked}.`,
      );
    },
    { maxWait: 10_000, timeout: 300_000 },
  );
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
    if (options.clearOnly) {
      const existingCount = await prisma.projectNotice.count();
      const linkedExtractionCount = await prisma.noticeExtraction.count({
        where: { projectNoticeId: { not: null } },
      });
      const blockers = (await dependentCounts(prisma)).filter(([, count]) => count > 0);
      console.log(
        `Mode: ${options.commit ? "COMMIT + CLEAR ONLY" : "DRY RUN + CLEAR ONLY (no database writes)"}`,
      );
      console.log(`Existing project_notice rows to clear: ${existingCount}`);
      console.log(`notice_extraction links to clear: ${linkedExtractionCount}`);
      console.log(
        `Clear blockers: ${blockers.length === 0 ? "none" : blockers.map(([name, count]) => `${name}=${count}`).join(", ")}`,
      );
      if (!options.commit) {
        console.log("Dry run complete. Add --commit to clear project_notice safely.");
        return;
      }
      await clearProjectNotices(prisma);
      return;
    }

    const mappings = readMatchedMappings(options.mappingRoot, options.site);
    const extractions = await loadExtractions(prisma, mappings, options.batchSize);
    const projectCodes = [
      ...new Set(mappings.map((mapping) => mapping.projectCode).filter((code) => code !== null)),
    ];
    const includeStandalone = mappings.some((mapping) => mapping.projectCode === null);
    const standaloneProjectNames = new Set(
      mappings
        .map((mapping) => mapping.standaloneProjectName)
        .filter((projectName) => projectName !== null),
    );
    const projects = await loadProjects(
      prisma,
      projectCodes,
      options.batchSize,
      includeStandalone,
    );
    const rows = buildImportRows(mappings, extractions, projects);
    const noticeTypeCounts = {};
    for (const row of rows) {
      noticeTypeCounts[row.noticeType] = (noticeTypeCounts[row.noticeType] || 0) + 1;
    }
    const existingCount = await prisma.projectNotice.count();
    const blockers = (await dependentCounts(prisma)).filter(([, count]) => count > 0);

    console.log(
      `Mode: ${options.commit ? (options.replace ? "COMMIT + REPLACE" : "COMMIT") : "DRY RUN (no database writes)"}`,
    );
    console.log(`Matched mapping rows: ${mappings.length}`);
    console.log(`Validated notice_extraction rows: ${extractions.length}`);
    console.log(`Referenced project codes: ${projectCodes.length}`);
    console.log(`Referenced standalone plan projects: ${standaloneProjectNames.size}`);
    console.log(`Validated project_notice rows: ${rows.length}`);
    console.log(
      `Notice type counts: ${Object.entries(noticeTypeCounts)
        .map(([type, count]) => `${type}=${count}`)
        .join(", ")}`,
    );
    console.log(`Existing project_notice rows: ${existingCount}`);
    console.log(
      `Replace blockers: ${blockers.length === 0 ? "none" : blockers.map(([name, count]) => `${name}=${count}`).join(", ")}`,
    );
    console.log("On commit, every imported notice_extraction.project_notice_id will be linked.");

    if (!options.commit) {
      console.log(options.site
        ? "Dry run complete. Add --commit with the same site scope to upsert project notices."
        : "Dry run complete. Add --commit --replace to replace and write project notices.");
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
