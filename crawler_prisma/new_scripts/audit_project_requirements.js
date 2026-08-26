#!/usr/bin/env node

"use strict";

const fs = require("node:fs");
const path = require("node:path");
const { validateProjectRequirement } = require("./lib/project_requirements");

function parseArgs(argv) {
  const options = { input: null, output: null, maxOtherRate: 0.05 };
  for (const arg of argv) {
    if (arg.startsWith("--input=")) options.input = path.resolve(arg.slice(8));
    else if (arg.startsWith("--output=")) options.output = path.resolve(arg.slice(9));
    else if (arg.startsWith("--max-other-rate=")) options.maxOtherRate = Number(arg.slice(17));
    else if (arg === "--help" || arg === "-h") {
      console.log("Usage: node new_scripts/audit_project_requirements.js --input=<dry-run.json> [--output=<audit.json>] [--max-other-rate=0.05]");
      process.exit(0);
    } else throw new Error(`Unknown argument: ${arg}`);
  }
  if (!options.input) throw new Error("--input is required");
  if (!Number.isFinite(options.maxOtherRate) || options.maxOtherRate < 0 || options.maxOtherRate > 1) {
    throw new Error("--max-other-rate must be between 0 and 1");
  }
  options.output ||= options.input.replace(/\.json$/u, "-audit.json");
  return options;
}

function unbalancedParentheses(value) {
  const text = String(value || "");
  return (text.match(/[（(]/gu) || []).length !== (text.match(/[）)]/gu) || []).length;
}

function typedFieldIssue(record) {
  const subtype = record.requirementSubtype;
  const data = record.structuredData || {};
  if (subtype === "COMPANY_QUALIFICATION") {
    const name = String(data.name || "");
    if (!name || name.length > 48 || unbalancedParentheses(name)) return "invalid qualification name";
    if (/^(?:综合|综合类|施工|设计|承包|施工总承包|专业类|相关专业)$/u.test(name)) return "generic qualification name";
    if (/[A-Z]\d|\s|https?:|www\.|业绩|合同|发票|项目名称|供应商须|投标人须|标段|资质要求|颁发的|颁布的|核发的|许可证/iu.test(name)) {
      return "contaminated qualification name";
    }
  }
  if (subtype === "COMPANY_LICENSE") {
    const name = String(data.name || "");
    if (!name || name.length > 40 || !/(?:许可证|核准证|许可相关证件)$/u.test(name)) return "invalid license name";
    if (/合同|发票|项目|投标人|供应商|履行日期/u.test(name)) return "contaminated license name";
  }
  if (subtype === "COMPANY_CERTIFICATION" && !data.name) return "missing certification name";
  if (subtype === "PERSONNEL_CERTIFICATE") {
    const name = String(data.certificateName || "");
    if (!name || name.length > 40) return "invalid personnel certificate name";
    if (/^(?:拟派|提供|须提供|需提供|持有|投标人)/u.test(name)) return "contaminated personnel certificate name";
    if (/^(?:人员资格证书|注册执业证书|相关专业资格证书)$/u.test(name)) return "generic personnel certificate name";
  }
  if (subtype === "BASIC_CONDITION" && (!data.conditionCode || data.conditionCode === "OTHER")) {
    return "non-concrete basic condition";
  }
  if (subtype === "PERFORMANCE" && /^业绩要求[：:]?\s*(?:无|不要求|无要求|[/,，])?$/u.test(record.requirementText)) {
    return "empty performance requirement";
  }
  return null;
}

function increment(object, key) {
  object[key] = (object[key] || 0) + 1;
}

function main() {
  const options = parseArgs(process.argv.slice(2));
  const report = JSON.parse(fs.readFileSync(options.input, "utf8"));
  const blockers = [];
  const warnings = [];
  const countsBySubtype = {};
  const countsBySite = {};
  const matrix = {};
  const sampleBuckets = new Map();
  let total = 0;
  let other = 0;

  if (!report.summary || !Array.isArray(report.notices) || !Array.isArray(report.failures)) {
    throw new Error("Input is not a project requirement extraction report");
  }
  if (report.summary.validationFailureNotices !== 0 || report.failures.length !== 0) {
    blockers.push({ code: "EXTRACTION_VALIDATION_FAILURES", count: report.failures.length });
  }
  if (report.notices.length !== report.summary.sourceNotices) {
    blockers.push({ code: "SOURCE_NOTICE_COUNT_MISMATCH", expected: report.summary.sourceNotices, actual: report.notices.length });
  }

  for (const notice of report.notices) {
    const requirements = Array.isArray(notice.requirements) ? notice.requirements : [];
    if (requirements.length === 0) blockers.push({ code: "EMPTY_NOTICE_RESULT", projectNoticeId: notice.projectNoticeId });
    const hashes = new Set();
    for (const record of requirements) {
      total += 1;
      increment(countsBySubtype, record.requirementSubtype);
      increment(countsBySite, notice.sourceSite || "<EMPTY>");
      matrix[notice.sourceSite || "<EMPTY>"] ||= {};
      increment(matrix[notice.sourceSite || "<EMPTY>"], record.requirementSubtype);
      if (record.requirementSubtype === "OTHER") other += 1;
      const validationErrors = validateProjectRequirement(record, notice.qualificationText);
      if (validationErrors.length) blockers.push({
        code: "REVALIDATION_FAILED",
        projectNoticeId: notice.projectNoticeId,
        contentHash: record.contentHash,
        errors: validationErrors,
      });
      if (hashes.has(record.contentHash)) blockers.push({
        code: "DUPLICATE_HASH_IN_NOTICE",
        projectNoticeId: notice.projectNoticeId,
        contentHash: record.contentHash,
      });
      hashes.add(record.contentHash);
      if (record.verificationStatus !== "UNVERIFIED") blockers.push({
        code: "UNAPPROVED_VERIFICATION_STATUS",
        projectNoticeId: notice.projectNoticeId,
        status: record.verificationStatus,
      });
      const typedIssue = typedFieldIssue(record);
      if (typedIssue) blockers.push({
        code: "TYPED_FIELD_QUALITY",
        projectNoticeId: notice.projectNoticeId,
        subtype: record.requirementSubtype,
        issue: typedIssue,
        value: record.structuredData?.name || record.structuredData?.certificateName || null,
      });
      const bucketKey = `${notice.sourceSite}\u0000${record.requirementSubtype}`;
      if (!sampleBuckets.has(bucketKey)) sampleBuckets.set(bucketKey, []);
      const bucket = sampleBuckets.get(bucketKey);
      if (bucket.length < 2) bucket.push({
        projectNoticeId: notice.projectNoticeId,
        title: notice.title,
        requirementText: record.requirementText,
        structuredData: record.structuredData,
      });
    }
  }
  if (total !== report.summary.extractedRequirements) blockers.push({
    code: "REQUIREMENT_COUNT_MISMATCH",
    expected: report.summary.extractedRequirements,
    actual: total,
  });
  const otherRate = total ? other / total : 0;
  if (otherRate > options.maxOtherRate) blockers.push({
    code: "OTHER_RATE_EXCEEDED",
    rate: otherRate,
    maximum: options.maxOtherRate,
  });
  if (other > 0) warnings.push({
    code: "MANUAL_REVIEW_REMAINS",
    count: other,
    note: "OTHER records remain UNVERIFIED and must not be used as hard recommendation gates.",
  });
  const audit = {
    passed: blockers.length === 0,
    input: options.input,
    auditedAt: new Date().toISOString(),
    totals: {
      sourceNotices: report.notices.length,
      requirements: total,
      other,
      otherRate: Number(otherRate.toFixed(6)),
      maxOtherRate: options.maxOtherRate,
    },
    countsBySubtype,
    countsBySite,
    siteSubtypeMatrix: matrix,
    blockers,
    warnings,
    samples: Object.fromEntries([...sampleBuckets.entries()].map(([key, value]) => [key.replace("\u0000", " / "), value])),
  };
  fs.mkdirSync(path.dirname(options.output), { recursive: true });
  fs.writeFileSync(options.output, `${JSON.stringify(audit, null, 2)}\n`);
  console.log(JSON.stringify({
    passed: audit.passed,
    totals: audit.totals,
    blockerCount: blockers.length,
    warningCount: warnings.length,
    output: options.output,
  }, null, 2));
  if (!audit.passed) process.exitCode = 2;
}

try {
  main();
} catch (error) {
  console.error(error instanceof Error ? error.message : error);
  process.exitCode = 1;
}
