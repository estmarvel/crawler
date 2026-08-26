#!/usr/bin/env node

"use strict";

const fs = require("node:fs");
const path = require("node:path");
const {
  DETAIL_DEFAULTS,
  REQUIREMENT_SUBTYPES,
  compareFromText,
  extractProjectRequirements,
  makeRecord,
  normalizeLevel,
  normalizeText,
  validateProjectRequirement,
} = require("./lib/project_requirements");

const CRAWLER_PRISMA_ROOT = path.resolve(__dirname, "..");
const PROJECT_ROOT = path.resolve(CRAWLER_PRISMA_ROOT, "..");
const QUALIFICATION_FIELD = "申请人资格要求/投标人资格要求";
const DEFAULT_QUALIFICATION_AI_BASE_URL = "https://api.siliconflow.cn/v1";
const DEFAULT_QUALIFICATION_AI_MODEL = "Qwen/Qwen3-8B";
const DEFAULT_QUALIFICATION_AI_TIMEOUT_MS = 60000;

function loadEnvironment() {
  for (const envPath of [
    path.join(CRAWLER_PRISMA_ROOT, ".env"),
    path.join(PROJECT_ROOT, ".env.production"),
  ]) {
    if (!fs.existsSync(envPath)) continue;
    for (const rawLine of fs.readFileSync(envPath, "utf8").split(/\r?\n/u)) {
      const match = rawLine.match(/^\s*([A-Z][A-Z0-9_]*)\s*=\s*(.*?)\s*$/u);
      if (!match || rawLine.trimStart().startsWith("#") || process.env[match[1]]) continue;
      let value = match[2];
      if ((value.startsWith('"') && value.endsWith('"')) || (value.startsWith("'") && value.endsWith("'"))) {
        value = value.slice(1, -1);
      }
      process.env[match[1]] = value;
    }
    if (process.env.DATABASE_URL) break;
  }
  if (!process.env.DATABASE_URL) {
    throw new Error("DATABASE_URL is required; inject it or configure crawler_prisma/.env or project .env.production");
  }
}

function positiveInteger(value, name, allowZero = false) {
  if (!/^\d+$/u.test(String(value || ""))) throw new Error(`${name} must be an integer`);
  const number = Number(value);
  if (!Number.isSafeInteger(number) || (allowZero ? number < 0 : number <= 0)) throw new Error(`${name} is out of range`);
  return number;
}

function parseArgs(argv) {
  const timestamp = new Date().toISOString().replace(/[-:.TZ]/gu, "").slice(0, 14);
  const options = {
    commit: false,
    sync: false,
    allowAll: false,
    ai: false,
    aiMaxCalls: 20,
    limit: null,
    afterId: 0,
    noticeIds: [],
    sourceSite: null,
    batchId: `qualification-${timestamp}`,
    output: null,
    help: false,
  };
  for (const arg of argv) {
    if (arg === "--commit") options.commit = true;
    else if (arg === "--sync") options.sync = true;
    else if (arg === "--allow-all") options.allowAll = true;
    else if (arg === "--ai") options.ai = true;
    else if (arg === "--help" || arg === "-h") options.help = true;
    else if (arg.startsWith("--limit=")) options.limit = positiveInteger(arg.slice(8), "--limit");
    else if (arg.startsWith("--after-id=")) options.afterId = positiveInteger(arg.slice(11), "--after-id", true);
    else if (arg.startsWith("--ai-max-calls=")) options.aiMaxCalls = positiveInteger(arg.slice(15), "--ai-max-calls", true);
    else if (arg.startsWith("--notice-ids=")) {
      options.noticeIds = arg.slice(13).split(",").filter(Boolean).map((value) => positiveInteger(value, "--notice-ids"));
    } else if (arg.startsWith("--source-site=")) options.sourceSite = normalizeText(arg.slice(14)) || null;
    else if (arg.startsWith("--batch-id=")) options.batchId = normalizeText(arg.slice(11));
    else if (arg.startsWith("--output=")) options.output = path.resolve(arg.slice(9));
    else throw new Error(`Unknown argument: ${arg}`);
  }
  if (!options.batchId) throw new Error("--batch-id cannot be empty");
  if (options.sync && !options.commit) throw new Error("--sync requires --commit");
  if (options.commit && !options.allowAll && options.limit === null && options.noticeIds.length === 0) {
    throw new Error("Full commit is protected; use --limit, --notice-ids, or explicitly add --allow-all");
  }
  options.output ||= path.join(
    PROJECT_ROOT,
    "runtime",
    "qualification-extraction",
    `${options.batchId}-${options.commit ? "commit" : "dry-run"}.json`,
  );
  return options;
}

function printHelp() {
  console.log(`Usage:
  node new_scripts/import_project_requirements.js [options]

Default mode is read-only dry-run. The source is project_notice.structured_data
field "${QUALIFICATION_FIELD}".

Options:
  --limit=<n>             Process at most n non-empty qualification notices.
  --after-id=<id>         Process project_notice rows after this id.
  --notice-ids=<ids>      Process comma-separated project_notice ids.
  --source-site=<name>    Restrict to one source_site value.
  --batch-id=<id>         Audit id stored in structured_data.extraction.batchId.
  --output=<path>         JSON report path.
  --ai                    Review rule-unclassified clauses with an OpenAI-compatible Qwen endpoint.
  --ai-max-calls=<n>      Maximum AI calls for this run; default 20.
  --commit                Write validated rows to project_requirement.
  --sync                  Mark old ACTIVE rows from processed notices SUPERSEDED.
  --allow-all             Explicitly allow a full commit without limit.
  --help                  Show this help.

AI environment:
  QUALIFICATION_AI_API_KEY             Required when --ai is enabled.
  QUALIFICATION_AI_BASE_URL            Optional; default https://api.siliconflow.cn/v1
  QUALIFICATION_AI_MODEL               Optional; default Qwen/Qwen3-8B
  QUALIFICATION_AI_TIMEOUT_MS          Optional; default 60000
`);
}

function stripCodeFence(value) {
  const text = normalizeText(value);
  return text.replace(/^```(?:json)?\s*/iu, "").replace(/\s*```$/u, "").trim();
}

class QualificationAiReviewer {
  constructor(options) {
    this.baseUrl = (normalizeText(process.env.QUALIFICATION_AI_BASE_URL)
      || DEFAULT_QUALIFICATION_AI_BASE_URL).replace(/\/+$/u, "");
    this.apiKey = normalizeText(process.env.QUALIFICATION_AI_API_KEY);
    this.model = normalizeText(process.env.QUALIFICATION_AI_MODEL) || DEFAULT_QUALIFICATION_AI_MODEL;
    this.timeoutMs = process.env.QUALIFICATION_AI_TIMEOUT_MS
      ? positiveInteger(process.env.QUALIFICATION_AI_TIMEOUT_MS, "QUALIFICATION_AI_TIMEOUT_MS")
      : DEFAULT_QUALIFICATION_AI_TIMEOUT_MS;
    this.maxCalls = options.aiMaxCalls;
    this.callCount = 0;
    this.successCount = 0;
    this.failureCount = 0;
    this.skippedCount = 0;
    this.rejectedResultCount = 0;
    if (!this.apiKey) {
      throw new Error("--ai requires QUALIFICATION_AI_API_KEY");
    }
  }

  async review(clause, commonOptions) {
    if (this.callCount >= this.maxCalls) {
      this.skippedCount += 1;
      return { records: [], error: "AI_CALL_LIMIT_REACHED" };
    }
    this.callCount += 1;
    const allowedDetails = Object.fromEntries(
      REQUIREMENT_SUBTYPES.filter((item) => item !== "OTHER").map(
        (subtype) => [subtype, DETAIL_DEFAULTS[subtype]],
      ),
    );
    const prompt = [
      "你是招标公告资格要求结构化工具。只允许依据给出的原文，不得补充、猜测或改写原文没有的事实。",
      "把原文拆成可独立判断的资格条件。无法确定时返回空 requirements，由规则结果进入人工核验。",
      `允许类型及details完整模板：${JSON.stringify(allowedDetails)}`,
      "输出严格JSON：{\"requirements\":[{\"subtype\":\"...\",\"quote\":\"原文中的连续证据\",\"keywords\":[],\"details\":{},\"confidence\":0.0}]}。",
      "每种类型的details必须包含模板中的全部字段；未知值沿用模板默认值或填null/空数组，枚举字段禁止填null，禁止增加字段。",
      "confidence低于0.6表示无法可靠分类，此时应返回空requirements，不要勉强分类。",
      `原文：${clause}`,
    ].join("\n");
    let response;
    try {
      response = await fetch(`${this.baseUrl}/chat/completions`, {
        method: "POST",
        headers: { Authorization: `Bearer ${this.apiKey}`, "Content-Type": "application/json" },
        body: JSON.stringify({
          model: this.model,
          messages: [{ role: "user", content: prompt }],
          temperature: 0,
          top_p: 0.8,
          top_k: 20,
          min_p: 0,
          enable_thinking: false,
          max_tokens: 1200,
          response_format: { type: "json_object" },
        }),
        signal: AbortSignal.timeout(this.timeoutMs),
      });
      if (!response.ok) throw new Error(`HTTP ${response.status}: ${(await response.text()).slice(0, 300)}`);
      const payload = await response.json();
      const content = payload?.choices?.[0]?.message?.content;
      const parsed = JSON.parse(stripCodeFence(content));
      const values = Array.isArray(parsed.requirements) ? parsed.requirements : [];
      const records = [];
      const rejected = [];
      for (const value of values) {
        const subtype = normalizeText(value?.subtype);
        if (!REQUIREMENT_SUBTYPES.includes(subtype) || subtype === "OTHER") {
          rejected.push("unsupported subtype");
          continue;
        }
        const quote = normalizeText(value?.quote);
        if (!quote || !normalizeText(clause).includes(quote)) {
          rejected.push("quote is not grounded in source clause");
          continue;
        }
        const expectedKeys = Object.keys(DETAIL_DEFAULTS[subtype]).sort();
        const details = value?.details && typeof value.details === "object" && !Array.isArray(value.details)
          ? { ...value.details }
          : {};
        if (JSON.stringify(Object.keys(details).sort()) !== JSON.stringify(expectedKeys)) {
          rejected.push(`${subtype} details fields do not match V1 contract`);
          continue;
        }
        const confidence = typeof value.confidence === "number" ? value.confidence : Number.NaN;
        if (!Number.isFinite(confidence) || confidence < 0.6 || confidence > 1) {
          rejected.push("confidence must be between 0.6 and 1");
          continue;
        }
        if (subtype === "BASIC_CONDITION" && (!details.conditionCode || details.conditionCode === "OTHER")) {
          rejected.push("BASIC_CONDITION requires a concrete conditionCode");
          continue;
        }
        if (Object.hasOwn(details, "level") && typeof details.level === "string" && details.level) {
          details.level = normalizeLevel(details.level) || normalizeText(details.level);
          if (Object.hasOwn(details, "compare")) {
            details.compare = compareFromText(quote, details.compare || "UNSPECIFIED");
          }
        }
        const candidate = makeRecord(subtype, quote, details, {
          ...commonOptions,
          method: "RULE_QWEN",
          model: this.model,
          confidence,
          keywords: Array.isArray(value.keywords) ? value.keywords : [],
        });
        const candidateErrors = validateProjectRequirement(candidate, clause);
        if (candidateErrors.length) {
          rejected.push(...candidateErrors);
          continue;
        }
        records.push(candidate);
      }
      this.successCount += 1;
      this.rejectedResultCount += rejected.length;
      return {
        records,
        error: rejected.length ? `AI_OUTPUT_REJECTED: ${[...new Set(rejected)].join("; ")}` : null,
      };
    } catch (error) {
      this.failureCount += 1;
      return { records: [], error: error.message };
    }
  }
}

function increment(map, key, value = 1) {
  map[key] = (map[key] || 0) + value;
}

function publicNotice(notice, sourceText, extraction, validationErrors, aiErrors) {
  return {
    projectNoticeId: notice.id,
    projectId: notice.projectId,
    noticeType: notice.noticeType,
    sourceSite: notice.sourceSite,
    sourceNoticeId: notice.sourceNoticeId,
    title: notice.title,
    qualificationText: sourceText,
    clauses: extraction.clauses,
    requirements: extraction.records,
    validationErrors,
    aiErrors,
  };
}

async function writeNoticeRequirements(prisma, notice, records, options) {
  return prisma.$transaction(async (transaction) => {
    let inserted = 0;
    let updated = 0;
    const activeHashes = [];
    for (const record of records) {
      activeHashes.push(record.contentHash);
      const where = {
        projectId_noticeId_contentHash: {
          projectId: notice.projectId,
          noticeId: notice.id,
          contentHash: record.contentHash,
        },
      };
      const existing = await transaction.projectRequirement.findUnique({ where, select: { id: true } });
      const data = {
        requirementType: record.requirementType,
        requirementSubtype: record.requirementSubtype,
        requirementText: record.requirementText,
        keywords: record.keywords,
        structuredData: record.structuredData,
        isMandatory: record.isMandatory,
        verificationStatus: record.verificationStatus,
        effectiveStatus: "ACTIVE",
      };
      await transaction.projectRequirement.upsert({
        where,
        create: {
          projectId: notice.projectId,
          noticeId: notice.id,
          contentHash: record.contentHash,
          ...data,
        },
        update: data,
      });
      if (existing) updated += 1;
      else inserted += 1;
    }
    let superseded = 0;
    if (options.sync) {
      const result = await transaction.projectRequirement.updateMany({
        where: {
          projectId: notice.projectId,
          noticeId: notice.id,
          effectiveStatus: "ACTIVE",
          ...(activeHashes.length ? { contentHash: { notIn: activeHashes } } : {}),
        },
        data: { effectiveStatus: "SUPERSEDED" },
      });
      superseded = result.count;
    }
    return { inserted, updated, superseded };
  });
}

async function main() {
  const options = parseArgs(process.argv.slice(2));
  if (options.help) return printHelp();
  loadEnvironment();
  const { PrismaClient } = require("@prisma/client");
  const prisma = new PrismaClient();
  const ai = options.ai ? new QualificationAiReviewer(options) : null;
  const subtypeCounts = {};
  const siteCounts = {};
  const failures = [];
  const reports = [];
  const writes = { inserted: 0, updated: 0, superseded: 0 };
  try {
    await prisma.$connect();
    const rows = await prisma.projectNotice.findMany({
      where: {
        id: { gt: options.afterId, ...(options.noticeIds.length ? { in: options.noticeIds } : {}) },
        ...(options.sourceSite ? { sourceSite: options.sourceSite } : {}),
      },
      select: {
        id: true,
        projectId: true,
        noticeType: true,
        sourceSite: true,
        sourceNoticeId: true,
        title: true,
        structuredData: true,
      },
      orderBy: { id: "asc" },
    });
    const candidates = [];
    for (const row of rows) {
      const value = row.structuredData && typeof row.structuredData === "object" && !Array.isArray(row.structuredData)
        ? row.structuredData[QUALIFICATION_FIELD]
        : null;
      const sourceText = normalizeText(value);
      if (!sourceText) continue;
      candidates.push({ row, sourceText });
      if (options.limit !== null && candidates.length >= options.limit) break;
    }

    for (const { row, sourceText } of candidates) {
      const commonOptions = { batchId: options.batchId, version: "qualification-v1" };
      const extraction = extractProjectRequirements(sourceText, commonOptions);
      const aiErrors = [];
      if (ai) {
        const refined = [];
        for (const record of extraction.records) {
          if (record.requirementSubtype !== "OTHER") {
            refined.push(record);
            continue;
          }
          const result = await ai.review(record.requirementText, commonOptions);
          if (result.error) aiErrors.push({ clause: record.requirementText, error: result.error });
          if (result.records.length) refined.push(...result.records);
          else refined.push(record);
        }
        extraction.records = [...new Map(refined.map((record) => [record.contentHash, record])).values()];
      }
      const validationErrors = [];
      for (const record of extraction.records) {
        const errors = validateProjectRequirement(record, sourceText);
        if (errors.length) validationErrors.push({ contentHash: record.contentHash, errors });
        increment(subtypeCounts, record.requirementSubtype);
      }
      increment(siteCounts, row.sourceSite || "(NULL)");
      if (validationErrors.length || extraction.records.length === 0) {
        failures.push({ projectNoticeId: row.id, validationErrors, emptyResult: extraction.records.length === 0 });
      } else if (options.commit) {
        const result = await writeNoticeRequirements(prisma, row, extraction.records, options);
        writes.inserted += result.inserted;
        writes.updated += result.updated;
        writes.superseded += result.superseded;
      }
      reports.push(publicNotice(row, sourceText, extraction, validationErrors, aiErrors));
    }

    const summary = {
      mode: options.commit ? "COMMIT" : "DRY_RUN",
      batchId: options.batchId,
      qualificationField: QUALIFICATION_FIELD,
      scannedRows: rows.length,
      sourceNotices: candidates.length,
      extractedRequirements: reports.reduce((sum, item) => sum + item.requirements.length, 0),
      clauses: reports.reduce((sum, item) => sum + item.clauses.length, 0),
      validationFailureNotices: failures.length,
      subtypeCounts,
      siteCounts,
      aiEnabled: Boolean(ai),
      aiCalls: ai?.callCount || 0,
      aiSuccesses: ai?.successCount || 0,
      aiFailures: ai?.failureCount || 0,
      aiSkippedDueToLimit: ai?.skippedCount || 0,
      aiRejectedResults: ai?.rejectedResultCount || 0,
      aiExtractedRequirements: reports.reduce(
        (sum, item) => sum + item.requirements.filter(
          (record) => record.structuredData?.extraction?.method === "RULE_QWEN",
        ).length,
        0,
      ),
      writes,
    };
    fs.mkdirSync(path.dirname(options.output), { recursive: true });
    fs.writeFileSync(options.output, `${JSON.stringify({ summary, failures, notices: reports }, null, 2)}\n`, "utf8");
    console.log(JSON.stringify(summary, null, 2));
    console.log(`Report: ${options.output}`);
    if (failures.length) process.exitCode = 2;
  } finally {
    await prisma.$disconnect();
  }
}

main().catch((error) => {
  console.error(error.stack || error.message);
  process.exitCode = 1;
});
