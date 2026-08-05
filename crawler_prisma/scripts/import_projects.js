#!/usr/bin/env node

"use strict";

const fs = require("node:fs");
const path = require("node:path");

const PROJECT_ROOT = path.resolve(__dirname, "..");
const VARCHAR_LIMIT = 191;
const DEFAULT_UNMATCHED_OUTPUT = path.join(
  PROJECT_ROOT,
  "reports",
  "project_notices_without_project_code.json",
);
const DEFAULT_MAPPING_ROOT = path.resolve(
  PROJECT_ROOT,
  "../Crawler_Scrapy/output/project_identity_mapping",
);
const DEFAULT_LOCATION_ANOMALY_OUTPUT = path.join(
  PROJECT_ROOT,
  "reports",
  "project_location_anomalies.json",
);
const SITE_BY_DATA_SOURCE_ID = Object.freeze({
  6: "huaxin",
  14: "jiubang",
});

const STAGES = Object.freeze({
  "招标计划": Object.freeze({ status: "PLAN", rank: 10, sourcePriority: 70 }),
  "资格预审公告": Object.freeze({ status: "PREQUALIFICATION", rank: 20, sourcePriority: 90 }),
  "招标公告": Object.freeze({ status: "TENDER", rank: 30, sourcePriority: 100 }),
  "中标候选人公示": Object.freeze({ status: "CANDIDATE", rank: 40, sourcePriority: 60 }),
  "定标候选人公示": Object.freeze({ status: "FINAL_CANDIDATE", rank: 50, sourcePriority: 50 }),
  "中标结果公示": Object.freeze({ status: "AWARD", rank: 60, sourcePriority: 50 }),
  "合同与履约": Object.freeze({ status: "CONTRACT", rank: 70, sourcePriority: 40 }),
  // 更正公告不是项目阶段，不能单独决定 current_status。
  "更正结果公示": Object.freeze({ status: null, rank: 0, sourcePriority: 30 }),
});

const FIELD_ALIASES = Object.freeze({
  projectNature: ["项目性质"],
  industry: ["所属行业"],
  projectType: ["项目类型/行业分类", "项目类型"],
  tenderMethod: ["招标方式"],
  organizationForm: ["组织形式"],
  locationText: ["项目地点", "建设地点"],
  ownerCompanyName: ["招标人/采购人名称", "招标人/采购人", "招标人名称"],
  agencyCompanyName: ["招标代理机构"],
  estimatedAmount: ["项目总投资/估算金额", "项目总投资"],
  tenderAmount: ["招标金额"],
  fundSource: ["资金来源"],
  bidOpenTime: ["开标时间"],
  bidSubmissionDeadline: ["递交截止时间"],
  duration: ["工期/服务期/供货日期", "工期"],
  qualityRequirement: ["质量要求"],
  supervisorDepartment: ["行政监督部门"],
  publishDate: ["发布日期"],
  projectCode: ["项目编号", "项目编号/招标编号", "招标编号/项目编号"],
});

const SHANXI_PROVINCE = "山西省";
const SHANXI_PREFECTURE_CITIES = Object.freeze([
  "太原市",
  "大同市",
  "阳泉市",
  "长治市",
  "晋城市",
  "朔州市",
  "晋中市",
  "运城市",
  "忻州市",
  "临汾市",
  "吕梁市",
]);

function printHelp() {
  console.log(`Usage:
  npm run build:project-mappings
  npm run import:projects -- [options]

Options:
  --commit                    Write to MySQL. Without it, only read and validate.
  --replace                   Delete all existing project rows before inserting.
                              Requires --commit and fails if child tables have rows.
                              New rows receive explicit ids from 1, then the next
                              AUTO_INCREMENT value is reset to row_count + 1.
  --unmatched-output=<path>   JSON report for unresolved/review notices.
                              Default: ${DEFAULT_UNMATCHED_OUTPUT}
  --mapping-root=<path>       Relationship mapping output directory.
                              Default: ${DEFAULT_MAPPING_ROOT}
  --location-anomaly-output=<path>
                              Province/city conflicts and unresolved locations.
                              Default: ${DEFAULT_LOCATION_ANOMALY_OUTPUT}
  --raw-notice-ids=<ids>      Limit generated mappings and database writes to
                              projects affected by these comma-separated raw_notice ids.
                              Identity resolution still reads the complete notice chain.
  --batch-size=<n>            Rows per INSERT batch (default: 200, max: 500).
  --help                      Show this help.

The importer reads notice_extraction and raw_notice from MySQL. Plans first match an
existing project by normalized project name; unmatched plans create standalone
project rows with project_code=NULL and current_status=PLAN. Other notices resolve
by direct project code, unique tender-code lookup, or normalized project name.
Relationship files are always generated; original crawler JSON is never modified.

Examples:
  npm run build:project-mappings
  npm run import:projects -- --replace
  npm run import:projects -- --commit --replace
`);
}

function parseArgs(argv) {
  const options = {
    commit: false,
    replace: false,
    unmatchedOutput: DEFAULT_UNMATCHED_OUTPUT,
    mappingRoot: DEFAULT_MAPPING_ROOT,
    locationAnomalyOutput: DEFAULT_LOCATION_ANOMALY_OUTPUT,
    batchSize: 200,
    rawNoticeIds: null,
  };
  for (const arg of argv) {
    if (arg === "--commit") {
      options.commit = true;
    } else if (arg === "--replace") {
      options.replace = true;
    } else if (arg === "--help" || arg === "-h") {
      options.help = true;
    } else if (arg.startsWith("--unmatched-output=")) {
      options.unmatchedOutput = path.resolve(arg.slice("--unmatched-output=".length));
    } else if (arg.startsWith("--mapping-root=")) {
      options.mappingRoot = path.resolve(arg.slice("--mapping-root=".length));
    } else if (arg.startsWith("--location-anomaly-output=")) {
      options.locationAnomalyOutput = path.resolve(
        arg.slice("--location-anomaly-output=".length),
      );
    } else if (arg.startsWith("--batch-size=")) {
      options.batchSize = Number(arg.slice("--batch-size=".length));
    } else if (arg.startsWith("--raw-notice-ids=")) {
      const values = arg.slice("--raw-notice-ids=".length).split(",").filter(Boolean);
      if (values.length === 0 || values.some((value) => !/^\d+$/u.test(value))) {
        throw new Error("--raw-notice-ids must be a comma-separated list of positive integers");
      }
      options.rawNoticeIds = new Set(values);
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

function normalizeProjectCode(value) {
  const text = nullableString(value);
  if (text === null) return null;
  return text
    .normalize("NFKC")
    .toUpperCase()
    .replace(/\s+/gu, "")
    .replace(/^[（(\[【"'“”]+|[）)\]】"'“”。，,；;：:]+$/gu, "");
}

function fieldValue(fields, aliases) {
  for (const alias of aliases) {
    const value = nullableString(fields[alias]);
    if (value !== null) return value;
  }
  return null;
}

function normalizeTenderCode(value) {
  return normalizeProjectCode(value);
}

function splitNumberParts(value) {
  const text = nullableString(value);
  if (text === null) return [];
  return text
    .replace(/\|/gu, "；")
    .replace(/;/gu, "；")
    .split("；")
    .map((part) => part.trim())
    .filter(Boolean);
}

function normalizeComparableText(value) {
  const text = nullableString(value);
  if (text === null) return null;
  return text
    .normalize("NFKC")
    .toLowerCase()
    .replace(/\s+/gu, "")
    .replace(/[，、]/gu, ",")
    .replace(/[—–－]/gu, "-")
    .replace(/[“”]/gu, '"')
    .replace(/[‘’]/gu, "'");
}

function normalizeBaseProjectName(value) {
  let text = nullableString(value)?.normalize("NFKC") || "";
  const noticeSuffix =
    /(?:招标公告|资格预审公告|中标候选人公示|定标候选人公示|中标结果公示|中标结果公告|结果公示|更正公告|招标计划)$/u;
  const amendmentSuffix =
    /(?:(?:招标)?(?:一|二|三|四|五|六|七|八|九|十|十一|十二|十三|十四|十五|\d+)?次?(?:延期|变更|更正|补充)|招标控制价|重新招标|终止|暂停)$/u;
  const lotParentheses =
    /[（(]\s*\d{1,3}(?:\s*[,，、]\s*\d{1,3})*(?:\s*[-—至~～]\s*\d{1,3})?\s*(?:标段|包)\s*[）)]/gu;
  const lotSuffix = /(?:\d{1,3}(?:第[一二三四五六七八九十]+)?(?:标段|包))$/u;

  let previous;
  do {
    previous = text;
    text = text
      .replace(noticeSuffix, "")
      .replace(amendmentSuffix, "")
      .replace(lotParentheses, "")
      .replace(lotSuffix, "")
      .trim();
  } while (text !== previous);
  return normalizeComparableText(text) || "";
}

function extractLabelledCode(text, labels) {
  if (!text) return null;
  const labelPattern = labels.map((label) => label.replace(/[.*+?^${}()|[\]\\]/gu, "\\$&")).join("|");
  const match = text.match(
    new RegExp(`(?:${labelPattern})\\s*[：:]\\s*([A-Z0-9][A-Z0-9._\\/-]{3,190})`, "iu"),
  );
  return match?.[1] || null;
}

function extractNoticeIdentity(extraction) {
  const fields = extraction.extractedFields;
  const explicitProjectField = fieldValue(fields, ["项目编号"]);
  const explicitTenderField = fieldValue(fields, ["招标编号"]);
  const combined = fieldValue(fields, ["项目编号/招标编号", "招标编号/项目编号"]);
  const parts = splitNumberParts(combined);
  const rawText = nullableString(extraction.rawNotice.rawText);
  const otherCodes = [];
  let projectCode = normalizeProjectCode(explicitProjectField);
  let tenderCode = normalizeTenderCode(explicitTenderField);
  let projectCodeSource = projectCode ? "STRUCTURED_PROJECT_CODE" : null;
  let tenderCodeSource = tenderCode ? "STRUCTURED_TENDER_CODE" : null;

  if (parts.length > 0) {
    const eCodeIndex = parts.findIndex((part) => /^E\d{16,22}$/iu.test(normalizeProjectCode(part)));
    if (projectCode === null && eCodeIndex >= 0) {
      projectCode = normalizeProjectCode(parts[eCodeIndex]);
      projectCodeSource = "STRUCTURED_E_CODE";
    } else if (projectCode === null && parts.length >= 2) {
      projectCode = normalizeProjectCode(parts[0]);
      projectCodeSource = "STRUCTURED_FIRST_OF_PAIR";
    }

    const projectIndex = projectCode
      ? parts.findIndex((part) => normalizeProjectCode(part) === projectCode)
      : -1;
    const remaining = parts.filter((_, index) => index !== projectIndex);
    if (tenderCode === null && remaining.length > 0) {
      tenderCode = normalizeTenderCode(remaining[0]);
      tenderCodeSource = "STRUCTURED_NUMBER_FIELD";
      otherCodes.push(...remaining.slice(1).map(normalizeTenderCode).filter(Boolean));
    } else if (tenderCode === null && projectCode === null && parts.length === 1) {
      tenderCode = normalizeTenderCode(parts[0]);
      tenderCodeSource = "STRUCTURED_SINGLE_AMBIGUOUS_NUMBER";
    }
  }

  if (rawText !== null) {
    if (projectCode === null) {
      const labelledProjectCode = extractLabelledCode(rawText, ["招标项目编号", "项目编号"]);
      const rawECode = rawText.match(/\bE\d{16,22}\b/iu)?.[0];
      const value = labelledProjectCode || rawECode;
      if (value) {
        projectCode = normalizeProjectCode(value);
        projectCodeSource = labelledProjectCode ? "RAW_TEXT_PROJECT_LABEL" : "RAW_TEXT_E_CODE";
      }
    }
    if (tenderCode === null) {
      const labelledTenderCode = extractLabelledCode(rawText, ["招标编号", "采购编号"]);
      if (labelledTenderCode && normalizeTenderCode(labelledTenderCode) !== projectCode) {
        tenderCode = normalizeTenderCode(labelledTenderCode);
        tenderCodeSource = "RAW_TEXT_TENDER_LABEL";
      }
    }
  }

  return {
    projectCode,
    tenderCode,
    otherCodes: [...new Set(otherCodes)],
    projectCodeSource,
    tenderCodeSource,
    combined,
  };
}

function mysqlDateTime(value, field, context) {
  const text = nullableString(value);
  if (text === null) return null;
  const match = text.match(
    /^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2}):(\d{2})(?:\.(\d{1,6}))?$/,
  );
  if (!match) throw new Error(`${context}: invalid ${field}: ${text}`);
  const [, year, month, day, hour, minute, second, fraction = ""] = match;
  const [monthNumber, dayNumber, hourNumber, minuteNumber, secondNumber] = [
    month,
    day,
    hour,
    minute,
    second,
  ].map(Number);
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
  return `${year}-${month}-${day} ${hour}:${minute}:${second}.${fraction.padEnd(3, "0").slice(0, 3)}`;
}

function dateTimeEpoch(value, field, context) {
  const normalized = mysqlDateTime(value, field, context);
  if (normalized === null) return null;
  // Source times are local wall-clock values for the crawled Chinese platforms.
  return Date.parse(`${normalized.replace(" ", "T").replace(/\.\d{3}$/u, "")}+08:00`);
}

function latestDateTime(records, aliases, field, context) {
  const values = records
    .map((record) => fieldValue(record.extractedFields, aliases))
    .filter((value) => value !== null)
    .map((value) => ({ value, epoch: dateTimeEpoch(value, field, context) }))
    .filter((item) => Number.isFinite(item.epoch))
    .sort((left, right) => right.epoch - left.epoch);
  return values.length > 0 ? mysqlDateTime(values[0].value, field, context) : null;
}

function publicationEpoch(extraction) {
  const value = publicationValue(extraction);
  if (!value) return Number.NEGATIVE_INFINITY;
  const epoch = dateTimeEpoch(value, "发布日期", `notice_extraction.id=${extraction.id}`);
  return Number.isFinite(epoch) ? epoch : Number.NEGATIVE_INFINITY;
}

function abnormalStatus(extraction) {
  const text = `${extraction.rawNotice.title || ""} ${fieldValue(extraction.extractedFields, ["项目名称"]) || ""}`;
  if (/撤销/u.test(text)) return "CANCELLED";
  if (/暂停/u.test(text)) return "SUSPENDED";
  if (/终止/u.test(text)) return "TERMINATED";
  return null;
}

function isLotScopedNotice(extraction) {
  const text = `${extraction.rawNotice.title || ""} ${fieldValue(extraction.extractedFields, ["项目名称"]) || ""}`.normalize("NFKC");
  return /\(\s*\d{1,3}(?:\s*[,，、\-—至~～]\s*\d{1,3})*\s*(?:标段|包)\s*\)|(?:^|[^\d])\d{1,3}(?:第[一二三四五六七八九十]+)?(?:标段|包)/u.test(text);
}

function determineCurrentStatus(records, nowEpoch = Date.now()) {
  const abnormalEvents = records
    .map((record) => ({ record, status: abnormalStatus(record) }))
    .filter((event) => event.status !== null && !isLotScopedNotice(event.record))
    .sort((left, right) => publicationEpoch(right.record) - publicationEpoch(left.record));
  const latestNormalEventEpoch = Math.max(
    ...records
      .filter((record) => abnormalStatus(record) === null)
      .map(publicationEpoch),
    Number.NEGATIVE_INFINITY,
  );
  if (
    abnormalEvents.length > 0 &&
    publicationEpoch(abnormalEvents[0].record) >= latestNormalEventEpoch
  ) {
    return abnormalEvents[0].status;
  }

  if (records.some((record) => record.noticeType === "合同与履约")) return "CONTRACT";
  if (records.some((record) => record.noticeType === "中标结果公示")) return "AWARD";
  if (records.some((record) => record.noticeType === "定标候选人公示")) {
    return "FINAL_CANDIDATE";
  }
  if (records.some((record) => record.noticeType === "中标候选人公示")) return "CANDIDATE";

  const context = `project_code=${records[0]?.projectCode || "unknown"}`;
  const deadlineEpochs = records
    .flatMap((record) => [
      fieldValue(record.extractedFields, FIELD_ALIASES.bidSubmissionDeadline),
      fieldValue(record.extractedFields, FIELD_ALIASES.bidOpenTime),
    ])
    .filter((value) => value !== null)
    .map((value) => dateTimeEpoch(value, "递交截止时间/开标时间", context))
    .filter(Number.isFinite);
  if (deadlineEpochs.length > 0 && Math.max(...deadlineEpochs) <= nowEpoch) {
    return "EVALUATING";
  }
  if (records.some((record) => record.noticeType === "招标公告")) return "TENDER";
  if (records.some((record) => record.noticeType === "资格预审公告")) {
    return "PREQUALIFICATION";
  }
  return "PLAN";
}

function decimalValue(value, field, context) {
  const text = nullableString(value);
  if (text === null) return null;
  const normalized = text.replace(/,/gu, "");
  if (!/^-?\d+(?:\.\d+)?$/.test(normalized)) {
    throw new Error(`${context}: ${field} is not a plain numeric value: ${text}`);
  }
  const [integerPart, fractionPart = ""] = normalized.replace(/^-/, "").split(".");
  if (integerPart.length > 16 || fractionPart.length > 2) {
    throw new Error(`${context}: ${field} exceeds DECIMAL(18,2): ${text}`);
  }
  return normalized;
}

function truncateSummary(value, field, context, truncations) {
  if (value === null) return null;
  const characters = [...value];
  if (characters.length <= VARCHAR_LIMIT) return value;
  truncations.set(field, (truncations.get(field) || 0) + 1);
  return characters.slice(0, VARCHAR_LIMIT).join("");
}

function sourcePriority(noticeType) {
  return STAGES[noticeType]?.sourcePriority || 0;
}

function isAmendmentRecord(extraction) {
  const text = [
    fieldValue(extraction.extractedFields, ["项目名称"]),
    extraction.rawNotice.title,
  ]
    .filter(Boolean)
    .join(" ");
  return /(?:延期|变更|更正|补充|终止|暂停|控制价|重新招标)/u.test(text);
}

function recordAuthority(extraction) {
  return sourcePriority(extraction.noticeType) - (isAmendmentRecord(extraction) ? 40 : 0);
}

function publicationValue(extraction) {
  return fieldValue(extraction.extractedFields, FIELD_ALIASES.publishDate) || "";
}

function compareSourceRecords(left, right) {
  const priorityDifference = recordAuthority(right) - recordAuthority(left);
  if (priorityDifference !== 0) return priorityDifference;
  // The earliest original notice is preferred over later same-type title variants.
  const dateDifference = publicationValue(left).localeCompare(publicationValue(right));
  if (dateDifference !== 0) return dateDifference;
  return left.id < right.id ? -1 : left.id > right.id ? 1 : 0;
}

function selectCurrentExtractions(rows) {
  const selected = new Map();
  for (const row of rows) {
    const key = row.rawNoticeId.toString();
    const current = selected.get(key);
    if (!current) {
      selected.set(key, row);
      continue;
    }
    const rowPreferred =
      Number(row.isVerified) > Number(current.isVerified) ||
      (row.isVerified === current.isVerified && row.updatedAt > current.updatedAt) ||
      (row.isVerified === current.isVerified &&
        row.updatedAt.getTime() === current.updatedAt.getTime() &&
        row.id > current.id);
    if (rowPreferred) selected.set(key, row);
  }
  return [...selected.values()];
}

function pickValue(records, aliases) {
  for (const record of records) {
    const value = fieldValue(record.extractedFields, aliases);
    if (value !== null) return value;
  }
  return null;
}

function parseShanxiLocation(value) {
  const raw = nullableString(value);
  if (raw === null) {
    return {
      raw: null,
      province: null,
      city: null,
      cityCandidates: [],
      status: "EMPTY",
      reason: "项目地点和建设地点均为空",
    };
  }

  const normalized = raw.normalize("NFKC").replace(/\s+/gu, "");
  const segments = normalized.split("|");
  let selectedSegmentIndex = -1;
  let cityCandidates = [];
  for (let index = 0; index < segments.length; index += 1) {
    const segment = segments[index];
    const citiesInSegment = SHANXI_PREFECTURE_CITIES
      .map((city) => ({ city, index: segment.indexOf(city) }))
      .filter((candidate) => candidate.index >= 0)
      .sort((left, right) => left.index - right.index)
      .map((candidate) => candidate.city);
    if (citiesInSegment.length > 0) {
      selectedSegmentIndex = index;
      cityCandidates = [...new Set(citiesInSegment)];
      break;
    }
  }
  const hasExplicitProvince = /山西(?:省)?/u.test(normalized);
  const city = cityCandidates.length > 0 ? cityCandidates.join("、") : null;
  // 识别到山西省 11 个地级市之一时，省份可无歧义地归一为“山西省”。
  const province = hasExplicitProvince || city !== null ? SHANXI_PROVINCE : null;
  if (province !== null && city !== null) {
    return {
      raw,
      province,
      city,
      cityCandidates,
      selectedSegmentIndex,
      status: "MATCHED",
      reason:
        cityCandidates.length > 1
          ? `第${selectedSegmentIndex + 1}个“|”分段包含并列城市，全部保留`
          : `使用从左到右第一个包含城市的“|”分段（第${selectedSegmentIndex + 1}段）`,
    };
  }
  if (province !== null) {
    return {
      raw,
      province,
      city: null,
      cityCandidates,
      status: "PROVINCE_ONLY",
      reason: "识别到山西省，但未识别到明确地级市",
    };
  }
  return {
    raw,
    province: null,
    city: null,
    cityCandidates,
    status: "UNMATCHED",
    reason: "未识别到山西省或山西省地级市，不从县区、道路或描述性地址猜测",
  };
}

function resolveProjectLocation(records, selectedLocationText) {
  const sourceValues = [
    ...new Set(
      records
        .map((record) => fieldValue(record.extractedFields, FIELD_ALIASES.locationText))
        .filter((value) => value !== null),
    ),
  ];
  const sourceNotices = records.map((record) => ({
    rawNoticeId: record.rawNoticeId.toString(),
    sourceNoticeId: record.rawNotice.sourceNoticeId,
    noticeType: record.noticeType,
    title: record.rawNotice.title,
    locationText: fieldValue(record.extractedFields, FIELD_ALIASES.locationText),
    detailPageUrl: record.rawNotice.sourceUrl,
  }));
  const selected = parseShanxiLocation(selectedLocationText);
  return {
    province: selected.province,
    city: selected.city,
    status: selected.status,
    reason: selected.reason,
    cityCandidates: selected.cityCandidates,
    selectedSegmentIndex: selected.selectedSegmentIndex ?? null,
    sourceValues,
    sourceNotices,
  };
}

function buildProject(group, truncations) {
  const records = [...group.records].sort(compareSourceRecords);
  const projectName = pickValue(records, ["项目名称"]);
  const context = group.projectCode
    ? `project_code=${group.projectCode}`
    : `standalone_project=${group.groupKey}`;
  if (projectName === null) {
    throw new Error(`${context}: no 项目名称 in the grouped extractions`);
  }
  if ([...projectName].length > VARCHAR_LIMIT) {
    throw new Error(`${context}: 项目名称 exceeds ${VARCHAR_LIMIT} characters`);
  }

  const publishDates = records
    .map((record) => fieldValue(record.extractedFields, FIELD_ALIASES.publishDate))
    .filter((value) => value !== null)
    .map((value) => mysqlDateTime(value, "发布日期", context))
    .sort();

  const locationText = pickValue(records, FIELD_ALIASES.locationText);
  const locationResolution = resolveProjectLocation(records, locationText);
  const project = {
    projectCode: group.projectCode,
    projectName,
    projectNature: pickValue(records, FIELD_ALIASES.projectNature),
    industry: pickValue(records, FIELD_ALIASES.industry),
    projectType: pickValue(records, FIELD_ALIASES.projectType),
    tenderMethod: pickValue(records, FIELD_ALIASES.tenderMethod),
    organizationForm: pickValue(records, FIELD_ALIASES.organizationForm),
    province: locationResolution.province,
    city: locationResolution.city,
    locationText,
    locationResolution,
    ownerCompanyId: null,
    ownerCompanyName: pickValue(records, FIELD_ALIASES.ownerCompanyName),
    agencyCompanyName: pickValue(records, FIELD_ALIASES.agencyCompanyName),
    estimatedAmount: decimalValue(
      pickValue(records, FIELD_ALIASES.estimatedAmount),
      "估算金额/总投资",
      context,
    ),
    tenderAmount: decimalValue(pickValue(records, FIELD_ALIASES.tenderAmount), "招标金额", context),
    fundSource: pickValue(records, FIELD_ALIASES.fundSource),
    bidOpenTime: latestDateTime(records, FIELD_ALIASES.bidOpenTime, "开标时间", context),
    duration: pickValue(records, FIELD_ALIASES.duration),
    qualityRequirement: pickValue(records, FIELD_ALIASES.qualityRequirement),
    supervisorDepartment: pickValue(records, FIELD_ALIASES.supervisorDepartment),
    currentStatus: determineCurrentStatus(records),
    firstPublishDate: publishDates[0] || null,
    noticeCount: records.length,
    rawNoticeIds: records.map((record) => record.rawNoticeId.toString()),
    codeSources: [...new Set(records.map((record) => record.projectCodeSource))],
  };

  for (const field of [
    "projectCode",
    "projectNature",
    "industry",
    "projectType",
    "tenderMethod",
    "organizationForm",
    "ownerCompanyName",
    "agencyCompanyName",
    "fundSource",
    "duration",
    "supervisorDepartment",
  ]) {
    project[field] = truncateSummary(project[field], field, context, truncations);
  }
  return project;
}

function siteCode(extraction) {
  return SITE_BY_DATA_SOURCE_ID[extraction.rawNotice.dataSourceId] ||
    `data_source_${extraction.rawNotice.dataSourceId}`;
}

function addReference(index, key, reference) {
  if (!key) return;
  if (!index.has(key)) index.set(key, []);
  index.get(key).push(reference);
}

function distinctProjectCodes(references) {
  return [...new Set(references.map((reference) => reference.projectCode))].sort();
}

function referenceForProjectCode(references, projectCode) {
  return references
    .filter((reference) => reference.projectCode === projectCode)
    .sort((left, right) => compareSourceRecords(left.extraction, right.extraction))[0];
}

function buildIdentityIndexes(extractions) {
  const tenderCodeIndex = new Map();
  const projectCodeIndex = new Map();
  const baseNameIndex = new Map();

  for (const extraction of extractions) {
    const identity = extractNoticeIdentity(extraction);
    extraction.identityEvidence = identity;
    if (!["招标公告", "资格预审公告"].includes(extraction.noticeType)) continue;
    if (identity.projectCode === null) continue;

    const site = siteCode(extraction);
    const projectName = fieldValue(extraction.extractedFields, ["项目名称"]);
    const reference = {
      extraction,
      site,
      projectCode: identity.projectCode,
      tenderCode: identity.tenderCode,
      projectName,
      baseProjectName: normalizeBaseProjectName(projectName),
      owner: normalizeComparableText(
        fieldValue(extraction.extractedFields, FIELD_ALIASES.ownerCompanyName),
      ),
      agency: normalizeComparableText(
        fieldValue(extraction.extractedFields, FIELD_ALIASES.agencyCompanyName),
      ),
    };
    addReference(projectCodeIndex, `${site}\u0000${reference.projectCode}`, reference);
    addReference(baseNameIndex, `${site}\u0000${reference.baseProjectName}`, reference);
    if (reference.tenderCode) {
      addReference(tenderCodeIndex, `${site}\u0000${reference.tenderCode}`, reference);
    }
  }
  return { tenderCodeIndex, projectCodeIndex, baseNameIndex };
}

function resolveByName(extraction, indexes, site, baseProjectName) {
  if (!baseProjectName) return null;
  const references = indexes.baseNameIndex.get(`${site}\u0000${baseProjectName}`) || [];
  if (references.length === 0) return null;

  const owner = normalizeComparableText(
    fieldValue(extraction.extractedFields, FIELD_ALIASES.ownerCompanyName),
  );
  const agency = normalizeComparableText(
    fieldValue(extraction.extractedFields, FIELD_ALIASES.agencyCompanyName),
  );
  const ownerMatches = owner
    ? references.filter((reference) => reference.owner && reference.owner === owner)
    : [];
  const ownerCodes = distinctProjectCodes(ownerMatches);
  if (ownerCodes.length === 1) {
    return {
      projectCode: ownerCodes[0],
      matchMethod: "PROJECT_NAME_AND_OWNER",
      confidence: 0.9,
      reference: referenceForProjectCode(ownerMatches, ownerCodes[0]),
      evidence: ["去标段后的项目名称一致", "招标人/采购人一致"],
    };
  }

  const agencyMatches = agency
    ? references.filter((reference) => reference.agency && reference.agency === agency)
    : [];
  const agencyCodes = distinctProjectCodes(agencyMatches);
  if (agencyCodes.length === 1) {
    return {
      projectCode: agencyCodes[0],
      matchMethod: "PROJECT_NAME_AND_AGENCY",
      confidence: 0.85,
      reference: referenceForProjectCode(agencyMatches, agencyCodes[0]),
      evidence: ["去标段后的项目名称一致", "招标代理机构一致"],
    };
  }

  const candidateProjectCodes = distinctProjectCodes(references);
  return {
    reviewOnly: true,
    candidateProjectCodes,
    reason:
      candidateProjectCodes.length === 1
        ? "项目基础名称唯一，但缺少招标人或代理机构一致性证据"
        : "项目基础名称对应多个项目编号",
  };
}

function resolvePlanByName(extraction, indexes, site, baseProjectName) {
  if (!baseProjectName) return null;
  const references = indexes.baseNameIndex.get(`${site}\u0000${baseProjectName}`) || [];
  if (references.length === 0) return null;

  const projectCodes = distinctProjectCodes(references);
  if (projectCodes.length === 1) {
    return {
      projectCode: projectCodes[0],
      matchMethod: "PLAN_PROJECT_NAME_UNIQUE",
      confidence: 0.9,
      reference: referenceForProjectCode(references, projectCodes[0]),
      evidence: ["招标计划与招标公告的标准化项目名称一致", "项目名称唯一对应一个项目编号"],
    };
  }

  const owner = normalizeComparableText(
    fieldValue(extraction.extractedFields, FIELD_ALIASES.ownerCompanyName),
  );
  const ownerMatches = owner
    ? references.filter((reference) => reference.owner && reference.owner === owner)
    : [];
  const ownerCodes = distinctProjectCodes(ownerMatches);
  if (ownerCodes.length === 1) {
    return {
      projectCode: ownerCodes[0],
      matchMethod: "PLAN_PROJECT_NAME_AND_OWNER",
      confidence: 0.95,
      reference: referenceForProjectCode(ownerMatches, ownerCodes[0]),
      evidence: ["招标计划与招标公告的标准化项目名称一致", "招标人名称一致"],
    };
  }
  return null;
}

function resolveNoticeIdentities(extractions) {
  const indexes = buildIdentityIndexes(extractions);
  const resolutions = [];

  for (const extraction of extractions) {
    const identity = extraction.identityEvidence || extractNoticeIdentity(extraction);
    const site = siteCode(extraction);
    const projectName = fieldValue(extraction.extractedFields, ["项目名称"]);
    const baseProjectName = normalizeBaseProjectName(projectName);
    const base = {
      extraction,
      site,
      projectName,
      baseProjectName,
      tenderCode: identity.tenderCode,
      otherCodes: identity.otherCodes,
      combined: identity.combined,
      candidateProjectCodes: [],
      evidence: [],
    };

    if (extraction.noticeType === "招标计划") {
      const nameMatch = resolvePlanByName(extraction, indexes, site, baseProjectName);
      if (nameMatch) {
        resolutions.push({
          ...base,
          projectCode: nameMatch.projectCode,
          tenderCode: nameMatch.reference?.tenderCode || null,
          matchStatus: "MATCHED",
          matchMethod: nameMatch.matchMethod,
          confidence: nameMatch.confidence,
          matchedTenderNoticeId:
            nameMatch.reference?.extraction.rawNotice.sourceNoticeId || null,
          evidence: nameMatch.evidence,
          reason: null,
        });
        continue;
      }
      const standaloneGroupKey = `PLAN\u0000${site}\u0000${
        baseProjectName || extraction.rawNotice.sourceNoticeId || extraction.id.toString()
      }`;
      resolutions.push({
        ...base,
        projectCode: null,
        matchStatus: "STANDALONE_PROJECT",
        matchMethod: "PLAN_PROJECT_NAME_NO_MATCH",
        confidence: 1,
        matchedTenderNoticeId: null,
        standaloneGroupKey,
        reason: "没有同名招标项目，建立project_code为空的独立计划项目",
      });
      continue;
    }

    if (identity.projectCode !== null) {
      const references =
        indexes.projectCodeIndex.get(`${site}\u0000${identity.projectCode}`) || [];
      const tenderReferences =
        references.length === 0
          ? indexes.tenderCodeIndex.get(`${site}\u0000${identity.projectCode}`) || []
          : [];
      const tenderProjectCodes = distinctProjectCodes(tenderReferences);
      if (tenderProjectCodes.length === 1) {
        const projectCode = tenderProjectCodes[0];
        const reference = referenceForProjectCode(tenderReferences, projectCode);
        resolutions.push({
          ...base,
          projectCode,
          tenderCode: identity.projectCode,
          otherCodes: [
            ...new Set(
              [identity.tenderCode, ...identity.otherCodes].filter(
                (code) => code !== null && code !== identity.projectCode,
              ),
            ),
          ],
          matchStatus: "MATCHED",
          matchMethod: "PROJECT_LABEL_REINTERPRETED_AS_TENDER_CODE",
          confidence: 0.98,
          matchedTenderNoticeId: reference?.extraction.rawNotice.sourceNoticeId || null,
          evidence: [
            "该编号未对应任何招标公告项目编号",
            "该编号作为招标编号时唯一对应一个项目编号",
          ],
          reason: null,
        });
        continue;
      }
      if (tenderProjectCodes.length > 1) {
        resolutions.push({
          ...base,
          projectCode: null,
          tenderCode: identity.projectCode,
          matchStatus: "REVIEW_REQUIRED",
          matchMethod: "PROJECT_LABEL_TENDER_CODE_CONFLICT",
          confidence: null,
          matchedTenderNoticeId: null,
          candidateProjectCodes: tenderProjectCodes,
          reason: "公告中的所谓项目编号作为招标编号时对应多个项目编号",
        });
        continue;
      }
      const reference = referenceForProjectCode(references, identity.projectCode);
      resolutions.push({
        ...base,
        projectCode: identity.projectCode,
        tenderCode: identity.tenderCode || reference?.tenderCode || null,
        matchStatus: "MATCHED",
        matchMethod: identity.projectCodeSource,
        confidence: identity.projectCodeSource?.startsWith("STRUCTURED") ? 1 : 0.99,
        matchedTenderNoticeId: reference?.extraction.rawNotice.sourceNoticeId || null,
        evidence: [
          identity.projectCodeSource?.startsWith("STRUCTURED")
            ? "结构化字段包含明确项目编号"
            : "公告正文包含明确项目编号",
        ],
        reason: null,
      });
      continue;
    }

    if (identity.tenderCode !== null) {
      const references =
        indexes.tenderCodeIndex.get(`${site}\u0000${identity.tenderCode}`) || [];
      const projectCodes = distinctProjectCodes(references);
      if (projectCodes.length === 1) {
        const reference = referenceForProjectCode(references, projectCodes[0]);
        resolutions.push({
          ...base,
          projectCode: projectCodes[0],
          matchStatus: "MATCHED",
          matchMethod: "TENDER_CODE_UNIQUE_LOOKUP",
          confidence: 0.98,
          matchedTenderNoticeId: reference?.extraction.rawNotice.sourceNoticeId || null,
          evidence: ["公告包含招标编号", "该招标编号唯一对应一个项目编号"],
          reason: null,
        });
        continue;
      }
      if (projectCodes.length > 1) {
        resolutions.push({
          ...base,
          projectCode: null,
          matchStatus: "REVIEW_REQUIRED",
          matchMethod: "TENDER_CODE_CONFLICT",
          confidence: null,
          matchedTenderNoticeId: null,
          candidateProjectCodes: projectCodes,
          reason: "同一招标编号对应多个项目编号",
        });
        continue;
      }
    }

    const nameMatch = resolveByName(extraction, indexes, site, baseProjectName);
    if (nameMatch && !nameMatch.reviewOnly) {
      resolutions.push({
        ...base,
        projectCode: nameMatch.projectCode,
        tenderCode: identity.tenderCode || nameMatch.reference?.tenderCode || null,
        matchStatus: "MATCHED",
        matchMethod: nameMatch.matchMethod,
        confidence: nameMatch.confidence,
        matchedTenderNoticeId:
          nameMatch.reference?.extraction.rawNotice.sourceNoticeId || null,
        evidence: nameMatch.evidence,
        reason: null,
      });
    } else {
      resolutions.push({
        ...base,
        projectCode: null,
        matchStatus: nameMatch?.reviewOnly ? "REVIEW_REQUIRED" : "UNMATCHED",
        matchMethod: nameMatch?.reviewOnly ? "PROJECT_NAME_REVIEW" : null,
        confidence: null,
        matchedTenderNoticeId: null,
        candidateProjectCodes: nameMatch?.candidateProjectCodes || [],
        reason: nameMatch?.reason || "没有项目编号、招标编号，也没有可验证的项目名称匹配",
      });
    }
  }
  return resolutions;
}

function buildProjects(extractions) {
  for (const extraction of extractions) {
    const fields = extraction.extractedFields;
    if (fields === null || typeof fields !== "object" || Array.isArray(fields)) {
      throw new Error(`notice_extraction.id=${extraction.id}: extracted_fields must be a JSON object`);
    }
  }

  const resolutions = resolveNoticeIdentities(extractions);
  const groups = new Map();
  for (const resolution of resolutions) {
    if (!["MATCHED", "STANDALONE_PROJECT"].includes(resolution.matchStatus)) continue;
    if (
      resolution.projectCode !== null &&
      [...resolution.projectCode].length > VARCHAR_LIMIT
    ) {
      throw new Error(
        `notice_extraction.id=${resolution.extraction.id}: project code exceeds ${VARCHAR_LIMIT} characters`,
      );
    }
    const extraction = resolution.extraction;
    extraction.projectCode = resolution.projectCode;
    extraction.projectCodeSource = resolution.matchMethod;
    const groupKey =
      resolution.projectCode !== null
        ? `PROJECT_CODE\u0000${resolution.projectCode}`
        : resolution.standaloneGroupKey;
    let group = groups.get(groupKey);
    if (!group) {
      group = { groupKey, projectCode: resolution.projectCode, records: [] };
      groups.set(groupKey, group);
    }
    group.records.push(extraction);
  }

  const truncations = new Map();
  const projects = [...groups.values()].map((group) => {
    const project = buildProject(group, truncations);
    group.projectName = project.projectName;
    return project;
  });
  for (const resolution of resolutions) {
    if (resolution.matchStatus !== "STANDALONE_PROJECT") continue;
    resolution.standaloneProjectName = groups.get(resolution.standaloneGroupKey)?.projectName || null;
  }
  projects.sort((left, right) => {
    if (left.projectCode === null && right.projectCode !== null) return 1;
    if (left.projectCode !== null && right.projectCode === null) return -1;
    if (left.projectCode !== null && right.projectCode !== null) {
      return left.projectCode.localeCompare(right.projectCode);
    }
    return left.projectName.localeCompare(right.projectName, "zh-CN");
  });
  return { projects, resolutions, truncations };
}

function mappingRecord(resolution) {
  return {
    平台代码: resolution.site,
    公告ID: resolution.extraction.rawNotice.sourceNoticeId,
    公告类型: resolution.extraction.noticeType,
    原项目名称: resolution.projectName,
    标准化项目名称: resolution.baseProjectName,
    项目编号: resolution.projectCode,
    独立项目名称: resolution.standaloneProjectName || null,
    招标编号: resolution.tenderCode,
    其他编号: resolution.otherCodes,
    匹配状态: resolution.matchStatus,
    匹配方式: resolution.matchMethod,
    匹配可信度: resolution.confidence,
    匹配到的招标公告ID: resolution.matchedTenderNoticeId,
    候选项目编号: resolution.candidateProjectCodes,
    匹配依据: resolution.evidence,
    未匹配原因: resolution.reason,
    原复合编号字段: resolution.combined,
    notice_extraction_id: resolution.extraction.id.toString(),
    raw_notice_id: resolution.extraction.rawNoticeId.toString(),
  };
}

function writeIdentityReports(mappingRoot, unmatchedOutput, resolutions, projects) {
  fs.mkdirSync(mappingRoot, { recursive: true });
  const records = resolutions.map(mappingRecord);
  const generatedAt = new Date().toISOString();
  const countsByStatus = {};
  const countsByMethod = {};
  for (const record of records) {
    countsByStatus[record.匹配状态] = (countsByStatus[record.匹配状态] || 0) + 1;
    const method = record.匹配方式 || "NONE";
    countsByMethod[method] = (countsByMethod[method] || 0) + 1;
  }

  for (const site of [...new Set(records.map((record) => record.平台代码))].sort()) {
    const siteRecords = records.filter((record) => record.平台代码 === site);
    const outputPath = path.join(mappingRoot, `${site}_project_mapping.json`);
    fs.writeFileSync(
      outputPath,
      `${JSON.stringify({ generatedAt, site, records: siteRecords }, null, 2)}\n`,
      "utf8",
    );
  }

  const unresolved = records.filter(
    (record) => !["MATCHED", "STANDALONE_PROJECT"].includes(record.匹配状态),
  );
  const report = {
    generatedAt,
    rule: "招标计划先按标准化项目名称匹配；未匹配计划建立项目编号为空的PLAN项目；其他公告按项目编号、招标编号、项目名称匹配。",
    projectCount: projects.length,
    countsByStatus,
    countsByMethod,
    notices: unresolved,
  };
  fs.mkdirSync(path.dirname(unmatchedOutput), { recursive: true });
  fs.writeFileSync(unmatchedOutput, `${JSON.stringify(report, null, 2)}\n`, "utf8");
  return { countsByStatus, countsByMethod, unresolvedCount: unresolved.length };
}

function writeLocationAnomalyReport(outputPath, projects) {
  const countsByStatus = {};
  for (const project of projects) {
    const status = project.locationResolution.status;
    countsByStatus[status] = (countsByStatus[status] || 0) + 1;
  }
  const anomalies = projects
    .filter((project) => project.locationResolution.status !== "MATCHED")
    .map((project) => ({
      projectCode: project.projectCode,
      projectName: project.projectName,
      locationText: project.locationText,
      provinceToWrite: project.province,
      cityToWrite: project.city,
      status: project.locationResolution.status,
      reason: project.locationResolution.reason,
      cityCandidates: project.locationResolution.cityCandidates,
      sourceLocationValues: project.locationResolution.sourceValues,
      detailPageLinks: [
        ...new Set(
          project.locationResolution.sourceNotices
            .map((notice) => notice.detailPageUrl)
            .filter((url) => url !== null),
        ),
      ],
      relatedNotices: project.locationResolution.sourceNotices,
      noticeCount: project.noticeCount,
    }));
  const report = {
    generatedAt: new Date().toISOString(),
    rule: "只识别山西省及其11个地级市；按从左到右第一个包含城市的“|”分段取值；该分段内以“、”并列的城市全部保存。",
    projectCount: projects.length,
    countsByStatus,
    anomalyCount: anomalies.length,
    projects: anomalies,
  };
  fs.mkdirSync(path.dirname(outputPath), { recursive: true });
  fs.writeFileSync(outputPath, `${JSON.stringify(report, null, 2)}\n`, "utf8");
  return { countsByStatus, anomalyCount: anomalies.length };
}

function chunks(values, size) {
  const result = [];
  for (let index = 0; index < values.length; index += size) result.push(values.slice(index, index + size));
  return result;
}

function prismaDateTime(value) {
  if (value === null || value === undefined || value instanceof Date) return value;
  return new Date(`${String(value).replace(" ", "T")}Z`);
}

async function insertBatch(transaction, rows) {
  const columnsPerRow = 23;
  const placeholders = rows
    .map(() => `(${new Array(columnsPerRow).fill("?").join(", ")}, CURRENT_TIMESTAMP(3), CURRENT_TIMESTAMP(3))`)
    .join(",\n");
  const sql = `
    INSERT INTO project (
      id, project_code, project_name, project_nature, industry, project_type,
      tender_method, organization_form, province, city, location_text, owner_company_id,
      owner_company_name,
      agency_company_name, estimated_amount, tender_amount, fund_source,
      bid_open_time, duration, quality_requirement, supervisor_department,
      current_status, first_publish_date, created_at, updated_at
    ) VALUES
    ${placeholders}
  `;
  const parameters = rows.flatMap((row) => [
    row.insertId ?? null,
    row.projectCode,
    row.projectName,
    row.projectNature,
    row.industry,
    row.projectType,
    row.tenderMethod,
    row.organizationForm,
    row.province,
    row.city,
    row.locationText,
    row.ownerCompanyId,
    row.ownerCompanyName,
    row.agencyCompanyName,
    row.estimatedAmount,
    row.tenderAmount,
    row.fundSource,
    row.bidOpenTime,
    row.duration,
    row.qualityRequirement,
    row.supervisorDepartment,
    row.currentStatus,
    row.firstPublishDate,
  ]);
  return transaction.$executeRawUnsafe(sql, ...parameters);
}

async function projectChildCounts(client) {
  const childModels = [
    ["project_notice", client.projectNotice],
    ["project_requirement", client.projectRequirement],
    ["project_company_relation", client.projectCompanyRelation],
    ["user_favorite", client.userFavorite],
    ["recommendation_result", client.recommendationResult],
    ["user_feedback", client.userFeedback],
    ["competition_analysis", client.competitionAnalysis],
    ["win_probability_analysis", client.winProbabilityAnalysis],
    ["contract", client.contract],
  ];
  const counts = [];
  for (const [tableName, model] of childModels) counts.push([tableName, await model.count()]);
  return counts;
}

async function assertNoProjectChildren(transaction) {
  const blockers = [];
  for (const [tableName, count] of await projectChildCounts(transaction)) {
    if (count > 0) blockers.push(`${tableName}=${count}`);
  }
  if (blockers.length > 0) {
    throw new Error(
      `--replace refused because project child tables are not empty: ${blockers.join(", ")}`,
    );
  }
}

async function commitProjects(prisma, projects, batchSize, replace) {
  await prisma.$transaction(
    async (transaction) => {
      let deleted = 0;
      let toCreate;
      let updated = 0;

      if (replace) {
        await assertNoProjectChildren(transaction);
        deleted = (await transaction.project.deleteMany()).count;
        toCreate = projects.map((project, index) => ({ ...project, insertId: index + 1 }));
      } else {
        const existingRows = await transaction.project.findMany({
          select: { id: true, projectCode: true, projectName: true },
        });
        const existingByIdentity = new Map();
        for (const row of existingRows) {
          const identity = row.projectCode !== null
            ? `CODE\u0000${normalizeProjectCode(row.projectCode)}`
            : `NAME\u0000${normalizeComparableText(row.projectName)}`;
          if (existingByIdentity.has(identity)) {
            throw new Error(`Existing project identity is duplicated: ${identity}`);
          }
          existingByIdentity.set(identity, row);
        }
        toCreate = [];
        for (const project of projects) {
          const identity = project.projectCode !== null
            ? `CODE\u0000${project.projectCode}`
            : `NAME\u0000${normalizeComparableText(project.projectName)}`;
          const existing = existingByIdentity.get(identity);
          if (!existing) {
            toCreate.push(project);
            continue;
          }
          await transaction.project.update({
            where: { id: existing.id },
            data: {
              projectCode: project.projectCode,
              projectName: project.projectName,
              projectNature: project.projectNature,
              industry: project.industry,
              projectType: project.projectType,
              tenderMethod: project.tenderMethod,
              organizationForm: project.organizationForm,
              province: project.province,
              city: project.city,
              locationText: project.locationText,
              ownerCompanyId: project.ownerCompanyId,
              ownerCompanyName: project.ownerCompanyName,
              agencyCompanyName: project.agencyCompanyName,
              estimatedAmount: project.estimatedAmount,
              tenderAmount: project.tenderAmount,
              fundSource: project.fundSource,
              bidOpenTime: prismaDateTime(project.bidOpenTime),
              duration: project.duration,
              qualityRequirement: project.qualityRequirement,
              supervisorDepartment: project.supervisorDepartment,
              currentStatus: project.currentStatus,
              firstPublishDate: prismaDateTime(project.firstPublishDate),
            },
          });
          updated += 1;
        }
      }

      const batches = chunks(toCreate, batchSize);
      let inserted = 0;
      for (let index = 0; index < batches.length; index += 1) {
        await insertBatch(transaction, batches[index]);
        inserted += batches[index].length;
        console.log(`  Inserted batch ${index + 1}/${batches.length} (${batches[index].length} rows)`);
      }
      console.log(
        `Commit completed: deleted=${deleted}, inserted=${inserted}, existing_updated=${updated}.`,
      );
      if (!replace) console.log("Existing projects were refreshed from the complete notice chain.");
    },
    { maxWait: 10_000, timeout: 300_000 },
  );
  if (replace) {
    const nextAutoIncrement = projects.length + 1;
    await prisma.$executeRawUnsafe(
      `ALTER TABLE project AUTO_INCREMENT = ${nextAutoIncrement}`,
    );
    console.log(
      `project.id reset: inserted ids=1..${projects.length}, next AUTO_INCREMENT=${nextAutoIncrement}.`,
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
    const allExtractions = await prisma.noticeExtraction.findMany({
      select: {
        id: true,
        rawNoticeId: true,
        noticeType: true,
        extractedFields: true,
        isVerified: true,
        updatedAt: true,
        rawNotice: {
          select: {
            dataSourceId: true,
            sourceNoticeId: true,
            sourceUrl: true,
            title: true,
            rawText: true,
          },
        },
      },
    });
    if (allExtractions.length === 0) throw new Error("notice_extraction is empty");

    const selectedExtractions = selectCurrentExtractions(allExtractions);
    const { projects, resolutions, truncations } = buildProjects(selectedExtractions);
    const scopedResolutions = options.rawNoticeIds
      ? resolutions.filter((resolution) => options.rawNoticeIds.has(resolution.extraction.rawNoticeId.toString()))
      : resolutions;
    const scopedProjects = options.rawNoticeIds
      ? projects.filter((project) => project.rawNoticeIds.some((id) => options.rawNoticeIds.has(id)))
      : projects;
    if (options.rawNoticeIds && scopedResolutions.length === 0) {
      throw new Error("No notice_extraction rows matched --raw-notice-ids");
    }
    const reportSummary = writeIdentityReports(
      options.mappingRoot,
      options.unmatchedOutput,
      scopedResolutions,
      scopedProjects,
    );
    const locationReportSummary = writeLocationAnomalyReport(
      options.locationAnomalyOutput,
      scopedProjects,
    );
    const stageCounts = new Map();
    for (const project of projects) {
      stageCounts.set(project.currentStatus, (stageCounts.get(project.currentStatus) || 0) + 1);
    }
    const existingProjectCount = await prisma.project.count();
    const childCounts = await projectChildCounts(prisma);
    const nonemptyChildren = childCounts.filter(([, count]) => count > 0);

    console.log(
      `Mode: ${options.commit ? (options.replace ? "COMMIT + REPLACE" : "COMMIT") : "DRY RUN (no database writes)"}`,
    );
    console.log(`notice_extraction rows read: ${allExtractions.length}`);
    console.log(`Current extraction rows selected: ${selectedExtractions.length}`);
    if (options.rawNoticeIds) {
      console.log(`Scoped raw_notice ids: ${[...options.rawNoticeIds].join(",")}`);
      console.log(`Scoped extraction mappings: ${scopedResolutions.length}`);
      console.log(`Affected projects to write: ${scopedProjects.length}`);
    }
    console.log(`Existing project rows to delete in replace mode: ${existingProjectCount}`);
    console.log(
      `Project child-table blockers: ${nonemptyChildren.length === 0 ? "none" : nonemptyChildren.map(([table, count]) => `${table}=${count}`).join(", ")}`,
    );
    console.log(`Projects after identity resolution and project-code merge: ${projects.length}`);
    console.log(`Projects merging more than one notice: ${projects.filter((project) => project.noticeCount > 1).length}`);
    console.log(
      `Identity status counts: ${Object.entries(reportSummary.countsByStatus)
        .map(([status, count]) => `${status}=${count}`)
        .join(", ") || "none"}`,
    );
    console.log(
      `Match method counts: ${Object.entries(reportSummary.countsByMethod)
        .map(([method, count]) => `${method}=${count}`)
        .join(", ") || "none"}`,
    );
    console.log(`Relationship mapping directory: ${options.mappingRoot}`);
    console.log(`Unmatched report: ${options.unmatchedOutput}`);
    console.log(
      `Location extraction counts: ${Object.entries(locationReportSummary.countsByStatus)
        .map(([status, count]) => `${status}=${count}`)
        .join(", ") || "none"}`,
    );
    console.log(
      `Projects retaining parallel cities: ${projects.filter((project) => project.city?.includes("、")).length}`,
    );
    console.log(`Location anomaly report: ${options.locationAnomalyOutput}`);
    console.log(`Status counts: ${[...stageCounts].map(([key, value]) => `${key}=${value}`).join(", ")}`);
    if (truncations.size > 0) {
      console.log(
        `VARCHAR(191) values truncated in project summary: ${[...truncations]
          .map(([field, count]) => `${field}=${count}`)
          .join(", ")}`,
      );
      console.log("Complete source values remain in notice_extraction.extracted_fields.");
    }

    if (!options.commit) {
      console.log(options.rawNoticeIds
        ? "Dry run complete. Add --commit with the same scope to update affected projects."
        : "Dry run complete. Add --commit --replace to delete old projects and insert the rebuilt set.");
      return;
    }
    await commitProjects(prisma, scopedProjects, options.batchSize, options.replace);
  } finally {
    await prisma.$disconnect();
  }
}

main().catch((error) => {
  console.error(`Import failed: ${error.message}`);
  process.exitCode = 1;
});
