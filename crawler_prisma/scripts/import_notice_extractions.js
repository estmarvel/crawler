#!/usr/bin/env node

"use strict";

const fs = require("node:fs");
const path = require("node:path");

const PROJECT_ROOT = path.resolve(__dirname, "..");
const DEFAULT_OUTPUT_ROOT = path.resolve(PROJECT_ROOT, "../Crawler_Scrapy/output");

// Folder/platform code -> existing data_source.id. Database short_code is not checked.
const SITE_CONFIG = Object.freeze({
  huaxin: Object.freeze({ dataSourceId: 6 }),
  jiubang: Object.freeze({ dataSourceId: 14 }),
});

// Exact Chinese notice types and required fields from 项目爬取关键字段20260622.xlsx.
// The duplicated 招标代理机构 column in two worksheets is represented once because
// a JSON object cannot contain two independent values under the same key.
const EXTRACTION_FIELDS = Object.freeze({
  招标计划: Object.freeze([
    "项目性质",
    "招标方式",
    "项目名称",
    "项目类型",
    "项目总投资",
    "招标内容",
    "招标人名称",
    "行政监督部门",
    "建设地点",
    "建设内容及规模",
    "招标公告（资格预审公告）预计发布时间",
    "发布日期",
    "发布网站",
  ]),
  资格预审公告: Object.freeze([
    "项目性质",
    "项目名称",
    "所属行业",
    "组织形式",
    "开标时间",
    "项目编号/招标编号",
    "项目类型/行业分类",
    "项目总投资/估算金额",
    "招标金额",
    "资金来源",
    "项目地点",
    "招标人/采购人名称",
    "招标代理机构",
    "项目概况与招标范围",
    "申请人资格要求/投标人资格要求",
    "预审文件获取时间",
    "获取方式",
    "递交截止时间",
    "递交方法",
    "开启时间",
    "开启方式",
    "开启地点",
    "评审办法",
    "投标保证金方式",
    "招标人地址",
    "招标人联系人",
    "招标人联系方式",
    "招标代理机构地址",
    "招标代理机构联系人",
    "招标代理机构联系方式",
    "发布日期",
    "发布网站",
  ]),
  招标公告: Object.freeze([
    "项目性质",
    "项目名称",
    "所属行业",
    "组织形式",
    "开标时间",
    "项目编号/招标编号",
    "项目类型/行业分类",
    "项目总投资/估算金额",
    "招标金额",
    "资金来源",
    "项目地点",
    "招标人/采购人名称",
    "招标代理机构",
    "项目规模",
    "工期/服务期/供货日期",
    "质量要求",
    "招标内容与范围",
    "申请人资格要求/投标人资格要求",
    "预审文件获取时间",
    "获取方式",
    "递交截止时间",
    "递交方法",
    "开启时间",
    "开启方式",
    "开启地点",
    "评审办法",
    "投标保证金方式",
    "招标人地址",
    "招标人联系人",
    "招标人联系方式",
    "招标代理机构地址",
    "招标代理机构联系人",
    "招标代理机构联系方式",
    "发布日期",
    "发布网站",
  ]),
  中标候选人公示: Object.freeze([
    "项目性质",
    "项目名称",
    "所属行业",
    "组织形式",
    "开标时间",
    "公示时间",
    "招标编号/项目编号",
    "中标候选人名称",
    "中标候选人报价",
    "招标人/采购人",
    "招标人地址",
    "招标人联系人",
    "招标人联系方式",
    "招标代理机构",
    "招标代理机构地址",
    "招标代理机构联系人",
    "招标代理机构联系方式",
    "发布日期",
    "发布网站",
  ]),
  定标候选人公示: Object.freeze([
    "项目性质",
    "项目名称",
    "所属行业",
    "组织形式",
    "开标时间",
    "公示时间",
    "招标编号/项目编号",
    "定标候选人名称",
    "定标候选人报价",
    "定标候选人项目经理",
    "定标候选人项目经理相关证书及编号",
    "定标候选人项目副经理",
    "定标候选人项目副经理相关证书及编号",
    "定标候选人资信情况",
    "定标候选人业绩情况（名称、日期、金额）",
    "招标人/采购人",
    "招标人地址",
    "招标人联系人",
    "招标人联系方式",
    "招标代理机构",
    "招标代理机构地址",
    "招标代理机构联系人",
    "招标代理机构联系方式",
    "依据文件",
    "依据文号",
    "发布日期",
    "发布网站",
  ]),
  中标结果公示: Object.freeze([
    "项目性质",
    "项目名称",
    "所属行业",
    "组织形式",
    "招标方式",
    "中标人名称",
    "联合体成员",
    "中标价",
    "工期",
    "项目经理",
    "项目经理证书名称",
    "项目经理证书编号",
    "招标人/采购人",
    "招标人地址",
    "招标人联系人",
    "招标人联系方式",
    "招标代理机构",
    "招标代理机构地址",
    "招标代理机构联系人",
    "招标代理机构联系方式",
    "依据文件",
    "依据文号",
    "发布日期",
    "发布网站",
  ]),
  更正结果公示: Object.freeze([
    "公共类型",
    "项目名称",
    "所属行业",
    "组织形式",
    "开标时间",
    "标书发售时间",
    "公告内容",
    "招标人地址",
    "招标人联系人",
    "招标人联系方式",
    "招标代理机构",
    "招标代理机构地址",
    "招标代理机构联系人",
    "招标代理机构联系方式",
    "监督部门地址",
    "监督部门联系人",
    "监督部门联系方式",
    "依据文件",
    "依据文号",
    "发布日期",
    "发布网站",
  ]),
  合同与履约: Object.freeze([
    "项目名称",
    "项目编号",
    "合同名称",
    "招标人名称",
    "中标人名称",
    "合同金额",
    "合同期限",
    "合同签署时间",
    "合同主要内容",
    "发布日期",
    "发布网站",
  ]),
});

const SUBTYPE_TO_NOTICE_TYPE = Object.freeze({
  zbjh: "招标计划",
  zbys: "资格预审公告",
  zbgg: "招标公告",
  hxr: "中标候选人公示",
  dbhxr: "定标候选人公示",
  zbjg: "中标结果公示",
  gzjg: "更正结果公示",
  htly: "合同与履约",
});

const FALLBACK_NOTICE_TYPES = Object.freeze({
  PLAN: "招标计划",
  PREQUALIFICATION: "资格预审公告",
  TENDER: "招标公告",
  CANDIDATE: "中标候选人公示",
  FINAL_CANDIDATE: "定标候选人公示",
  AWARD: "中标结果公示",
  CORRECTION: "更正结果公示",
  CONTRACT: "合同与履约",
});

function printHelp() {
  console.log(`Usage:
  npm run import:notice-extractions -- [options]

Options:
  --commit              Write to MySQL. Without it, only validate and summarize.
  --site=<name>         all, huaxin, or jiubang (default: all).
  --output-root=<path>  Crawler output directory (default: ${DEFAULT_OUTPUT_ROOT}).
  --batch-size=<n>      Rows per createMany batch (default: 200, max: 500).
  --help                Show this help.

Examples:
  npm run import:notice-extractions
  npm run import:notice-extractions -- --site=huaxin
  npm run import:notice-extractions -- --commit
`);
}

function parseArgs(argv) {
  const options = {
    commit: false,
    site: "all",
    outputRoot: DEFAULT_OUTPUT_ROOT,
    batchSize: 200,
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

function booleanValue(value, field, context) {
  if (value === null || value === undefined || value === "") return false;
  if (typeof value === "boolean") return value;
  if (value === 1 || String(value).trim().toLowerCase() === "true") return true;
  if (value === 0 || String(value).trim().toLowerCase() === "false") return false;
  throw new Error(`${context}: invalid boolean ${field}: ${value}`);
}

function resolveNoticeType(source, context) {
  const subtype = nullableString(source["公告子类型"]);
  if (subtype) {
    const mapped = SUBTYPE_TO_NOTICE_TYPE[subtype.toLowerCase()];
    if (mapped) return mapped;
  }

  const rawType = requiredString(source["公告类型"], "公告类型", context);
  if (Object.hasOwn(EXTRACTION_FIELDS, rawType)) return rawType;
  const fallback = FALLBACK_NOTICE_TYPES[rawType.toUpperCase()];
  if (fallback) return fallback;
  throw new Error(
    `${context}: unsupported 公告子类型=${subtype || "(empty)"}, 公告类型=${rawType}`,
  );
}

function buildExtractedFields(source, noticeType) {
  const result = {};
  for (const field of EXTRACTION_FIELDS[noticeType]) {
    result[field] = source[field] === undefined ? null : source[field];
  }
  return result;
}

function mapExtraction(source, site, fileName, index) {
  const context = `${site}/${fileName} item ${index + 1}`;
  const platformCode = requiredString(source["平台代码"], "平台代码", context).toLowerCase();
  if (platformCode !== site) {
    throw new Error(`${context}: 平台代码 is ${platformCode}, expected ${site}`);
  }

  const noticeType = resolveNoticeType(source, context);
  const extractionModel = requiredString(source["抽取方式"], "抽取方式", context);
  const extractionVersion = requiredString(source["抽取版本"], "抽取版本", context);
  if ([...noticeType].length > 64) throw new Error(`${context}: notice_type exceeds 64 characters`);
  if ([...extractionModel].length > 64) {
    throw new Error(`${context}: extraction_model exceeds 64 characters`);
  }
  if ([...extractionVersion].length > 32) {
    throw new Error(`${context}: extraction_version exceeds 32 characters`);
  }

  return {
    site,
    dataSourceId: SITE_CONFIG[site].dataSourceId,
    sourceNoticeId: requiredString(source["公告ID"], "公告ID", context),
    noticeType,
    extractedFields: buildExtractedFields(source, noticeType),
    extractionModel,
    extractionVersion,
    isVerified: booleanValue(source["是否已核验"], "是否已核验", context),
  };
}

function readExtractions(outputRoot, sites) {
  const extractions = [];
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
      let values;
      try {
        values = JSON.parse(fs.readFileSync(filePath, "utf8"));
      } catch (error) {
        throw new Error(`Cannot parse ${filePath}: ${error.message}`);
      }
      if (!Array.isArray(values)) throw new Error(`${filePath}: top-level JSON must be an array`);
      values.forEach((value, index) => {
        if (value === null || typeof value !== "object" || Array.isArray(value)) {
          throw new Error(`${site}/${fileName} item ${index + 1}: expected an object`);
        }
        extractions.push(mapExtraction(value, site, fileName, index));
      });
    }
  }

  const uniqueByKey = new Map();
  let duplicateCount = 0;
  for (const extraction of extractions) {
    const key = [
      extraction.dataSourceId,
      extraction.sourceNoticeId,
      extraction.extractionModel,
      extraction.extractionVersion,
    ].join("\u0000");
    if (uniqueByKey.has(key)) duplicateCount += 1;
    uniqueByKey.set(key, extraction);
  }
  return { extractions: [...uniqueByKey.values()], jsonFileCount, duplicateCount };
}

function printSummary(extractions, jsonFileCount, duplicateCount, options) {
  console.log(`Mode: ${options.commit ? "COMMIT" : "DRY RUN (no database writes)"}`);
  console.log(`Output root: ${options.outputRoot}`);
  console.log(`Validated JSON files: ${jsonFileCount}`);
  console.log(`Validated extractions: ${extractions.length}`);
  console.log(`Duplicate JSON extractions skipped: ${duplicateCount}`);

  const counts = new Map(Object.keys(EXTRACTION_FIELDS).map((noticeType) => [noticeType, 0]));
  for (const extraction of extractions) {
    counts.set(extraction.noticeType, counts.get(extraction.noticeType) + 1);
  }
  for (const [noticeType, count] of counts) {
    console.log(`  ${noticeType}: ${count}`);
  }
  console.log("project_notice_id, confidence fields and verification audit fields are NULL on insert.");
}

async function resolveParents(prisma, extractions, sites) {
  const conditions = sites.map((site) => ({
    dataSourceId: SITE_CONFIG[site].dataSourceId,
    sourceNoticeId: {
      in: [
        ...new Set(
          extractions
            .filter((extraction) => extraction.site === site)
            .map((extraction) => extraction.sourceNoticeId),
        ),
      ],
    },
  }));
  const parents = await prisma.rawNotice.findMany({
    where: { OR: conditions },
    select: { id: true, dataSourceId: true, sourceNoticeId: true },
  });
  const parentByKey = new Map(
    parents.map((parent) => [`${parent.dataSourceId}\u0000${parent.sourceNoticeId}`, parent.id]),
  );

  const missing = [];
  for (const extraction of extractions) {
    const key = `${extraction.dataSourceId}\u0000${extraction.sourceNoticeId}`;
    const rawNoticeId = parentByKey.get(key);
    if (rawNoticeId === undefined) {
      missing.push(`data_source_id=${extraction.dataSourceId}, source_notice_id=${extraction.sourceNoticeId}`);
    } else {
      extraction.rawNoticeId = rawNoticeId;
    }
  }
  if (missing.length > 0) {
    throw new Error(
      `${missing.length} parent raw_notice rows were not found (${missing.slice(0, 10).join("; ")}). Import raw_notice first.`,
    );
  }
}

function extractionIdentity(extraction) {
  return [
    extraction.rawNoticeId.toString(),
    extraction.extractionModel,
    extraction.extractionVersion,
  ].join("\u0000");
}

function createData(extraction) {
  return {
    rawNoticeId: extraction.rawNoticeId,
    noticeType: extraction.noticeType,
    extractedFields: extraction.extractedFields,
    extractionModel: extraction.extractionModel,
    extractionVersion: extraction.extractionVersion,
    isVerified: extraction.isVerified,
  };
}

function chunks(values, size) {
  const result = [];
  for (let index = 0; index < values.length; index += size) {
    result.push(values.slice(index, index + size));
  }
  return result;
}

function canonicalJson(value) {
  if (Array.isArray(value)) return value.map(canonicalJson);
  if (value !== null && typeof value === "object") {
    return Object.fromEntries(
      Object.keys(value)
        .sort()
        .map((key) => [key, canonicalJson(value[key])]),
    );
  }
  return value;
}

function jsonEquals(left, right) {
  return JSON.stringify(canonicalJson(left)) === JSON.stringify(canonicalJson(right));
}

async function commitExtractions(prisma, extractions, batchSize) {
  let inserted = 0;
  let updated = 0;
  let unchanged = 0;

  await prisma.$transaction(
    async (transaction) => {
      const rawNoticeIds = [...new Set(extractions.map((extraction) => extraction.rawNoticeId))];
      const existingRows = await transaction.noticeExtraction.findMany({
        where: { rawNoticeId: { in: rawNoticeIds } },
        select: {
          id: true,
          rawNoticeId: true,
          noticeType: true,
          extractedFields: true,
          extractionModel: true,
          extractionVersion: true,
        },
      });

      const existingByKey = new Map();
      for (const existing of existingRows) {
        const key = extractionIdentity(existing);
        if (existingByKey.has(key)) {
          throw new Error(
            `Database has duplicate notice_extraction rows for raw_notice_id=${existing.rawNoticeId}, model=${existing.extractionModel}, version=${existing.extractionVersion}`,
          );
        }
        existingByKey.set(key, existing);
      }

      const toCreate = [];
      for (const extraction of extractions) {
        const existing = existingByKey.get(extractionIdentity(extraction));
        if (!existing) {
          toCreate.push(createData(extraction));
          continue;
        }

        const fieldsChanged =
          existing.noticeType !== extraction.noticeType ||
          !jsonEquals(existing.extractedFields, extraction.extractedFields);
        if (!fieldsChanged) {
          unchanged += 1;
          continue;
        }
        await transaction.noticeExtraction.update({
          where: { id: existing.id },
          data: {
            noticeType: extraction.noticeType,
            extractedFields: extraction.extractedFields,
          },
        });
        updated += 1;
      }

      const batches = chunks(toCreate, batchSize);
      for (let index = 0; index < batches.length; index += 1) {
        const result = await transaction.noticeExtraction.createMany({ data: batches[index] });
        inserted += result.count;
        console.log(`  Created batch ${index + 1}/${batches.length} (${result.count} rows)`);
      }
    },
    { maxWait: 10_000, timeout: 300_000 },
  );

  console.log(`Commit completed: inserted=${inserted}, updated=${updated}, unchanged=${unchanged}.`);
  console.log("Existing project_notice_id and human verification fields were preserved.");
}

async function main() {
  const options = parseArgs(process.argv.slice(2));
  if (options.help) {
    printHelp();
    return;
  }

  const sites = options.site === "all" ? Object.keys(SITE_CONFIG) : [options.site];
  const { extractions, jsonFileCount, duplicateCount } = readExtractions(options.outputRoot, sites);
  printSummary(extractions, jsonFileCount, duplicateCount, options);

  if (!options.commit) {
    console.log("Dry run complete. Add --commit to write these extractions to MySQL.");
    return;
  }

  loadDatabaseUrlFromDotEnv();
  if (!process.env.DATABASE_URL) {
    throw new Error("DATABASE_URL is not set and was not found in crawler_prisma/.env");
  }
  const { PrismaClient } = require("@prisma/client");
  const prisma = new PrismaClient();
  try {
    await resolveParents(prisma, extractions, sites);
    await commitExtractions(prisma, extractions, options.batchSize);
  } finally {
    await prisma.$disconnect();
  }
}

main().catch((error) => {
  console.error(`Import failed: ${error.message}`);
  process.exitCode = 1;
});
