"use strict";

const fs = require("node:fs");
const path = require("node:path");
const { createHash } = require("node:crypto");

const NEW_SCRIPTS_ROOT = path.resolve(__dirname, "..");
const CRAWLER_PRISMA_ROOT = path.resolve(NEW_SCRIPTS_ROOT, "..");
const DEFAULT_OUTPUT_ROOT = path.resolve(CRAWLER_PRISMA_ROOT, "../Crawler_Scrapy/new_output");
const DEFAULT_ENV_FILE = path.resolve(CRAWLER_PRISMA_ROOT, ".env");

const SITE_CONFIG = Object.freeze({
  huaxin: Object.freeze({ preferredDataSourceId: 6, shortCodes: ["huaxin", "ygcgpt"] }),
  jiubang: Object.freeze({ preferredDataSourceId: 14, shortCodes: ["jiubang", "bjjbkj"] }),
  sxjm: Object.freeze({ preferredDataSourceId: 7, shortCodes: ["sxjm", "sxccdzzcpt"] }),
  sxzwfw: Object.freeze({ preferredDataSourceId: 21, shortCodes: ["sxzwfw"] }),
  bitbid: Object.freeze({ preferredDataSourceId: 12, shortCodes: ["bitbid"] }),
  sxjkzcpt: Object.freeze({ preferredDataSourceId: 9, shortCodes: ["sxjkzcpt"] }),
  trade365: Object.freeze({ preferredDataSourceId: 16, shortCodes: ["trade365", "shanxi_365trade"] }),
  sxxindian: Object.freeze({ preferredDataSourceId: 18, shortCodes: ["sxxindian"] }),
  sxbid: Object.freeze({ preferredDataSourceId: 22, shortCodes: ["sxbid"] }),
  qianji: Object.freeze({ preferredDataSourceId: 24, shortCodes: ["qianji", "qianjilink"] }),
  runshihua: Object.freeze({ preferredDataSourceId: null, shortCodes: ["runshihua"] }),
  gxebidding: Object.freeze({ preferredDataSourceId: 26, shortCodes: ["gxebidding", "guoxin_shanxi"] }),
  sxzfcg: Object.freeze({ preferredDataSourceId: 27, shortCodes: ["sxzfcg", "ccgp_shanxi"] }),
});

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

function compactDatabaseString(value, max) {
  const text = nullableString(value);
  if (text === null || [...text].length <= max) return text;
  const digest = createHash("sha256").update(text).digest("hex").slice(0, 8);
  return `${[...text].slice(0, max - digest.length - 1).join("")}-${digest}`;
}

function stableJsonValue(value) {
  if (value === undefined) return null;
  if (value === null || typeof value !== "object") {
    if (typeof value === "bigint") return value.toString();
    return value;
  }
  if (value instanceof Date) return value.toISOString();
  if (Array.isArray(value)) return value.map(stableJsonValue);
  return Object.fromEntries(
    Object.keys(value)
      .sort()
      .filter((key) => value[key] !== undefined)
      .map((key) => [key, stableJsonValue(value[key])]),
  );
}

function stableDigest(value) {
  return createHash("sha256")
    .update(JSON.stringify(stableJsonValue(value)))
    .digest("hex");
}

function booleanValue(value, field, context) {
  if (value === null || value === undefined || value === "") return false;
  if (typeof value === "boolean") return value;
  if (value === 1 || String(value).trim().toLowerCase() === "true") return true;
  if (value === 0 || String(value).trim().toLowerCase() === "false") return false;
  throw new Error(`${context}: invalid boolean ${field}: ${value}`);
}

function bigintValue(value, field, context) {
  if (value === null || value === undefined || value === "") return null;
  if (typeof value === "number" && !Number.isSafeInteger(value)) {
    throw new Error(`${context}: ${field} is not a safe integer; store it as a JSON string`);
  }
  const text = String(value).trim();
  if (!/^\d+$/.test(text)) throw new Error(`${context}: invalid ${field}: ${text}`);
  return BigInt(text);
}

function parseCrawlerDate(value, field, context, required = false) {
  const text = nullableString(value);
  if (text === null) {
    if (required) throw new Error(`${context}: missing required field ${field}`);
    return null;
  }
  const match = text.match(
    /^(\d{4})-(\d{2})-(\d{2})(?:[ T](\d{2}):(\d{2}):(\d{2})(?:\.(\d{1,6}))?)?$/,
  );
  if (!match) throw new Error(`${context}: invalid ${field}: ${text}`);
  const [, year, month, day, hour = "00", minute = "00", second = "00", fraction = ""] = match;
  const date = new Date(
    `${year}-${month}-${day}T${hour}:${minute}:${second}.${fraction.padEnd(3, "0").slice(0, 3)}+08:00`,
  );
  if (Number.isNaN(date.getTime())) throw new Error(`${context}: invalid ${field}: ${text}`);
  return date;
}

function safeObjectName(value) {
  return String(value).replace(/[\\/:*?"<>|\u0000-\u001f]/g, "_");
}

function traceEnvelope(source, context = "record") {
  const value = source?._trace;
  if (value === null || value === undefined) return null;
  if (typeof value !== "object" || Array.isArray(value)) {
    throw new Error(`${context}: _trace must be an object`);
  }
  const schemaVersion = nullableString(value.schemaVersion);
  if (!["1.0", "2.0"].includes(schemaVersion)) {
    throw new Error(`${context}: unsupported _trace.schemaVersion ${value.schemaVersion}`);
  }
  if (
    schemaVersion === "1.0" &&
    (!value.payload || typeof value.payload !== "object" || Array.isArray(value.payload))
  ) {
    throw new Error(`${context}: _trace.payload must be an object`);
  }
  for (const field of ["rawHtml", "rawText", "crawlerVersion", "extractionVersion"]) {
    if (value[field] !== null && value[field] !== undefined && typeof value[field] !== "string") {
      throw new Error(`${context}: _trace.${field} must be a string or null`);
    }
  }
  if (schemaVersion === "2.0") {
    for (const field of ["payload", "rawHtml", "rawText", "exportMetadata"]) {
      if (value[field] !== null && value[field] !== undefined) {
        throw new Error(`${context}: _trace.${field} is not allowed in schema 2.0`);
      }
    }
  }
  if (
    value.responseMetadata !== null && value.responseMetadata !== undefined &&
    (typeof value.responseMetadata !== "object" || Array.isArray(value.responseMetadata))
  ) {
    throw new Error(`${context}: _trace.responseMetadata must be an object`);
  }
  if (
    value.fieldMeta !== null && value.fieldMeta !== undefined &&
    (typeof value.fieldMeta !== "object" || Array.isArray(value.fieldMeta))
  ) {
    throw new Error(`${context}: _trace.fieldMeta must be an object`);
  }
  if (
    value.exportMetadata !== null && value.exportMetadata !== undefined &&
    (typeof value.exportMetadata !== "object" || Array.isArray(value.exportMetadata))
  ) {
    throw new Error(`${context}: _trace.exportMetadata must be an object`);
  }
  const payloadSnapshot = value.payloadSnapshot;
  if (payloadSnapshot !== null && payloadSnapshot !== undefined) {
    if (typeof payloadSnapshot !== "object" || Array.isArray(payloadSnapshot)) {
      throw new Error(`${context}: _trace.payloadSnapshot must be an object`);
    }
    requiredString(payloadSnapshot.path, "_trace.payloadSnapshot.path", context);
    const sha256 = requiredString(
      payloadSnapshot.sha256,
      "_trace.payloadSnapshot.sha256",
      context,
    );
    if (!/^[a-f0-9]{64}$/iu.test(sha256)) {
      throw new Error(`${context}: _trace.payloadSnapshot.sha256 must be SHA256`);
    }
  }
  const fieldConfidences = value.fieldMeta?.fieldConfidences;
  if (
    fieldConfidences !== null && fieldConfidences !== undefined &&
    (typeof fieldConfidences !== "object" || Array.isArray(fieldConfidences))
  ) {
    throw new Error(`${context}: _trace.fieldMeta.fieldConfidences must be an object`);
  }
  const evidence = value.fieldMeta?.evidence;
  if (evidence !== null && evidence !== undefined && !Array.isArray(evidence)) {
    throw new Error(`${context}: _trace.fieldMeta.evidence must be an array`);
  }
  return value;
}

function snapshotBytes(record, relativePath, expectedSha256, label) {
  const snapshotPath = nullableString(relativePath);
  if (!snapshotPath) return null;
  const outputRoot = path.resolve(record.outputRoot);
  const absolutePath = path.resolve(outputRoot, snapshotPath);
  const outputPrefix = `${outputRoot}${path.sep}`;
  if (absolutePath !== outputRoot && !absolutePath.startsWith(outputPrefix)) {
    throw new Error(`${record.context}: ${label} escapes output root: ${snapshotPath}`);
  }
  if (!fs.existsSync(absolutePath) || !fs.statSync(absolutePath).isFile()) {
    throw new Error(`${record.context}: ${label} does not exist: ${absolutePath}`);
  }
  const bytes = fs.readFileSync(absolutePath);
  const actualSha256 = createHash("sha256").update(bytes).digest("hex");
  const expected = nullableString(expectedSha256)?.toLowerCase() || null;
  if (expected && actualSha256 !== expected) {
    throw new Error(
      `${record.context}: ${label} SHA256 mismatch: expected=${expected} actual=${actualSha256}`,
    );
  }
  return bytes;
}

function decodeSnapshotText(bytes) {
  if (!bytes) return null;
  try {
    return new TextDecoder("utf-8", { fatal: true }).decode(bytes);
  } catch {
    return new TextDecoder("gb18030").decode(bytes);
  }
}

function loadTraceSources(record, trace) {
  let payload = null;
  let rawHtml = null;
  if (trace?.schemaVersion === "1.0") {
    payload = trace.payload || null;
    rawHtml = nullableString(trace.rawHtml);
  } else if (trace?.payloadSnapshot) {
    const bytes = snapshotBytes(
      record,
      trace.payloadSnapshot.path,
      trace.payloadSnapshot.sha256,
      "payload snapshot",
    );
    try {
      payload = JSON.parse(bytes.toString("utf8"));
    } catch (error) {
      throw new Error(`${record.context}: cannot parse payload snapshot: ${error.message}`);
    }
  }
  if (!rawHtml) {
    rawHtml = decodeSnapshotText(snapshotBytes(
      record,
      record.source["HTML快照路径"],
      record.source["HTML快照SHA256"],
      "HTML snapshot",
    ));
  }
  return { payload, rawHtml };
}

function readJsonNotices(outputRoot, sites) {
  const records = [];
  let jsonFileCount = 0;
  for (const site of sites) {
    const jsonDirectory = path.join(outputRoot, site, "json");
    if (!fs.existsSync(jsonDirectory)) {
      throw new Error(`JSON directory does not exist: ${jsonDirectory}`);
    }
    const files = fs.readdirSync(jsonDirectory, { withFileTypes: true })
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
      values.forEach((source, index) => {
        const context = `${site}/${fileName} item ${index + 1}`;
        if (source === null || typeof source !== "object" || Array.isArray(source)) {
          throw new Error(`${context}: expected an object`);
        }
        const platformCode = requiredString(source["平台代码"], "平台代码", context).toLowerCase();
        if (platformCode !== site) {
          throw new Error(`${context}: 平台代码 is ${platformCode}, expected ${site}`);
        }
        records.push({ site, fileName, filePath, outputRoot: path.resolve(outputRoot), index, context, source });
      });
    }
  }
  return { records, jsonFileCount };
}

function jsonNoticeFiles(outputRoot, sites) {
  const files = [];
  for (const site of sites) {
    const jsonDirectory = path.join(outputRoot, site, "json");
    if (!fs.existsSync(jsonDirectory)) {
      throw new Error(`JSON directory does not exist: ${jsonDirectory}`);
    }
    const names = fs.readdirSync(jsonDirectory, { withFileTypes: true })
      .filter((entry) => entry.isFile() && entry.name.toLowerCase().endsWith(".json"))
      .map((entry) => entry.name)
      .sort((left, right) => left.localeCompare(right, "zh-CN"));
    if (names.length === 0) throw new Error(`No JSON files found in: ${jsonDirectory}`);
    for (const fileName of names) files.push({ site, fileName, filePath: path.join(jsonDirectory, fileName) });
  }
  return files;
}

async function* streamJsonArray(filePath) {
  const stream = fs.createReadStream(filePath, { encoding: "utf8", highWaterMark: 1024 * 1024 });
  let opened = false;
  let closed = false;
  let collecting = false;
  let depth = 0;
  let inString = false;
  let escaped = false;
  let current = "";

  for await (const chunk of stream) {
    for (const character of chunk) {
      if (!opened) {
        if (/\s/u.test(character)) continue;
        if (character !== "[") throw new Error(`${filePath}: top-level JSON must be an array`);
        opened = true;
        continue;
      }
      if (!collecting) {
        if (/\s/u.test(character) || character === ",") continue;
        if (character === "]") {
          closed = true;
          continue;
        }
        if (character !== "{") throw new Error(`${filePath}: array entries must be objects`);
        collecting = true;
        depth = 1;
        inString = false;
        escaped = false;
        current = "{";
        continue;
      }

      current += character;
      if (inString) {
        if (escaped) escaped = false;
        else if (character === "\\") escaped = true;
        else if (character === '"') inString = false;
        continue;
      }
      if (character === '"') {
        inString = true;
        continue;
      }
      if (character === "{" || character === "[") depth += 1;
      else if (character === "}" || character === "]") depth -= 1;
      if (depth === 0) {
        let value;
        try {
          value = JSON.parse(current);
        } catch (error) {
          throw new Error(`${filePath}: cannot parse array entry: ${error.message}`);
        }
        yield value;
        collecting = false;
        current = "";
      }
    }
  }
  if (!opened || collecting || !closed) throw new Error(`${filePath}: incomplete JSON array`);
}

async function* iterateJsonNotices(outputRoot, sites) {
  for (const file of jsonNoticeFiles(outputRoot, sites)) {
    let index = 0;
    for await (const source of streamJsonArray(file.filePath)) {
      const context = `${file.site}/${file.fileName} item ${index + 1}`;
      if (source === null || typeof source !== "object" || Array.isArray(source)) {
        throw new Error(`${context}: expected an object`);
      }
      const platformCode = requiredString(source["平台代码"], "平台代码", context).toLowerCase();
      if (platformCode !== file.site) {
        throw new Error(`${context}: 平台代码 is ${platformCode}, expected ${file.site}`);
      }
      yield { ...file, outputRoot: path.resolve(outputRoot), index, context, source };
      index += 1;
    }
  }
}

function parseCommonArgs(argv, extra = {}) {
  const options = {
    commit: false,
    site: "all",
    outputRoot: DEFAULT_OUTPUT_ROOT,
    envFile: process.env.CRAWLER_PRISMA_ENV || DEFAULT_ENV_FILE,
    ...extra,
  };
  for (const arg of argv) {
    if (arg === "--commit") options.commit = true;
    else if (arg === "--help" || arg === "-h") options.help = true;
    else if (arg.startsWith("--site=")) options.site = arg.slice("--site=".length).trim().toLowerCase();
    else if (arg.startsWith("--output-root=")) options.outputRoot = path.resolve(arg.slice("--output-root=".length));
    else if (arg.startsWith("--env-file=")) options.envFile = path.resolve(arg.slice("--env-file=".length));
    // 兼容旧命令：原 api-root 的环境文件位于 API 同级 .env.production。
    else if (arg.startsWith("--api-root=")) {
      const apiRoot = path.resolve(arg.slice("--api-root=".length));
      options.envFile = path.resolve(apiRoot, "../.env.production");
    }
    else if (arg.startsWith("--crawl-task-id=") && Object.hasOwn(options, "crawlTaskId")) {
      options.crawlTaskId = arg.slice("--crawl-task-id=".length).trim();
    } else if (arg === "--allow-missing-files" && Object.hasOwn(options, "allowMissingFiles")) {
      options.allowMissingFiles = true;
    } else {
      throw new Error(`Unknown argument: ${arg}`);
    }
  }
  if (!["all", ...Object.keys(SITE_CONFIG)].includes(options.site)) {
    throw new Error(`Invalid --site value: ${options.site}`);
  }
  if (options.crawlTaskId !== undefined && options.crawlTaskId !== null) {
    if (!/^[1-9]\d*$/.test(options.crawlTaskId)) {
      throw new Error("--crawl-task-id must be a positive integer");
    }
    if (options.site === "all") throw new Error("--crawl-task-id requires one explicit --site");
  }
  options.sites = options.site === "all"
    ? Object.keys(SITE_CONFIG).filter((site) => fs.existsSync(path.join(options.outputRoot, site, "json")))
    : [options.site];
  if (options.sites.length === 0) throw new Error(`No supported site JSON directory found under ${options.outputRoot}`);
  return options;
}

function loadStorageEnvironment(environmentSource = DEFAULT_ENV_FILE, requirements = {}) {
  let envPath = path.resolve(environmentSource || DEFAULT_ENV_FILE);
  if (fs.existsSync(envPath) && fs.statSync(envPath).isDirectory()) {
    const candidates = [path.join(envPath, ".env"), path.resolve(envPath, "../.env.production")];
    envPath = candidates.find((candidate) => fs.existsSync(candidate)) || candidates[0];
  }
  if (!fs.existsSync(envPath)) throw new Error(`Environment file does not exist: ${envPath}`);
  if (typeof process.loadEnvFile !== "function") {
    throw new Error("Node.js 20.12+ is required because process.loadEnvFile() is unavailable");
  }
  // 本地 .env 保存经核验的实际 MySQL；--env-file 可补充 MongoDB/MinIO。
  // process.loadEnvFile 保留已存在变量，因此按“本地配置 -> 补充配置”加载；
  // 进程显式环境仍具有最高优先级。外部配置中的旧 DATABASE_URL 不会覆盖
  // 当前数据库，同时无需复制或提交任何密钥。
  const explicitEnvironment = { ...process.env };
  const sources = envPath === DEFAULT_ENV_FILE
    ? [DEFAULT_ENV_FILE]
    : [DEFAULT_ENV_FILE, envPath];
  for (const source of sources) {
    if (fs.existsSync(source)) process.loadEnvFile(source);
  }
  Object.assign(process.env, explicitEnvironment);
  const required = ["DATABASE_URL"];
  if (requirements.mongo) required.push("MONGODB_URL", "MONGODB_DATABASE");
  if (requirements.minio) required.push("MINIO_ENDPOINT");
  for (const name of required) {
    if (!process.env[name]) {
      throw new Error(
        `${name} is required in process environment, ${DEFAULT_ENV_FILE}, or ${envPath}`,
      );
    }
  }
  return envPath;
}

function requireLocal(packageName) {
  try {
    return require(require.resolve(packageName, { paths: [CRAWLER_PRISMA_ROOT] }));
  } catch (error) {
    throw new Error(`Cannot load local ${packageName}; run npm ci in ${CRAWLER_PRISMA_ROOT} (${error.message})`);
  }
}

async function openStores(environmentSource = DEFAULT_ENV_FILE, requirements = {}) {
  loadStorageEnvironment(environmentSource, requirements);
  const { PrismaClient } = requireLocal("@prisma/client");
  const prisma = new PrismaClient();
  await prisma.$connect();

  let mongoClient = null;
  let mongo = null;
  let ObjectId = null;
  if (requirements.mongo) {
    const mongodb = requireLocal("mongodb");
    ObjectId = mongodb.ObjectId;
    mongoClient = new mongodb.MongoClient(process.env.MONGODB_URL);
    await mongoClient.connect();
    mongo = mongoClient.db(process.env.MONGODB_DATABASE || "project_recommendation_documents");
  }

  let minio = null;
  if (requirements.minio) {
    const { Client } = requireLocal("minio");
    minio = new Client({
      endPoint: process.env.MINIO_ENDPOINT,
      port: Number(process.env.MINIO_PORT || 9000),
      useSSL: process.env.MINIO_USE_SSL === "true",
      accessKey: process.env.MINIO_ACCESS_KEY || "",
      secretKey: process.env.MINIO_SECRET_KEY || "",
    });
  }

  return {
    prisma,
    mongoClient,
    mongo,
    ObjectId,
    minio,
    bucketName: process.env.MINIO_BUCKET_ATTACHMENTS || "notice-attachments",
    async close() {
      if (mongoClient) await mongoClient.close();
      await prisma.$disconnect();
    },
  };
}

async function resolveDataSources(prisma, sites) {
  const result = new Map();
  for (const site of sites) {
    const config = SITE_CONFIG[site];
    let row = null;
    if (config.preferredDataSourceId !== null) {
      row = await prisma.dataSource.findUnique({ where: { id: config.preferredDataSourceId } });
      if (row && !config.shortCodes.includes(String(row.shortCode).toLowerCase())) {
        throw new Error(
          `data_source.id=${config.preferredDataSourceId} has short_code=${row.shortCode}, expected one of ${config.shortCodes.join(",")}`,
        );
      }
    }
    if (!row) {
      row = await prisma.dataSource.findFirst({ where: { shortCode: { in: config.shortCodes } } });
    }
    if (!row) {
      throw new Error(
        `No data_source found for ${site}; expected id=${config.preferredDataSourceId ?? "(none)"} or short_code in ${config.shortCodes.join(",")}`,
      );
    }
    result.set(site, row);
  }
  return result;
}

function deduplicate(records, keyBuilder) {
  const result = new Map();
  let duplicateCount = 0;
  for (const record of records) {
    const key = keyBuilder(record);
    if (result.has(key)) duplicateCount += 1;
    result.set(key, record);
  }
  return { records: [...result.values()], duplicateCount };
}

module.exports = {
  DEFAULT_ENV_FILE,
  DEFAULT_OUTPUT_ROOT,
  SITE_CONFIG,
  assertMaxLength,
  bigintValue,
  booleanValue,
  compactDatabaseString,
  deduplicate,
  iterateJsonNotices,
  jsonNoticeFiles,
  loadStorageEnvironment,
  loadTraceSources,
  nullableString,
  openStores,
  parseCommonArgs,
  parseCrawlerDate,
  readJsonNotices,
  requiredString,
  resolveDataSources,
  safeObjectName,
  stableDigest,
  streamJsonArray,
  traceEnvelope,
};
