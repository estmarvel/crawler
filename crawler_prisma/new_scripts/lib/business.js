"use strict";

const { createHash } = require("node:crypto");
const {
  compactDatabaseString,
  iterateJsonNotices,
  nullableString,
  parseCrawlerDate,
  requiredString,
  stableDigest,
} = require("./runtime");

const NOTICE_TYPE_BY_CODE = Object.freeze({
  PLAN: "招标计划",
  PREQUALIFICATION: "资格预审公告",
  TENDER: "招标公告",
  CANDIDATE: "中标候选人公示",
  FINAL_CANDIDATE: "定标候选人公示",
  AWARD: "中标结果公示",
  CORRECTION: "更正结果公示",
  TERMINATION: "终止公告",
  CONTRACT: "合同与履约",
});

const NOTICE_TYPE_BY_SUBTYPE = Object.freeze({
  zbjh: "招标计划",
  zbys: "资格预审公告",
  zbgg: "招标公告",
  cggg: "采购公告",
  hxr: "中标候选人公示",
  cjhxr: "成交候选人公示",
  dbhxr: "定标候选人公示",
  zbjg: "中标结果公示",
  cjgg: "成交公告",
  gzjg: "更正结果公示",
  bg: "更正结果公示",
  zzgg: "终止公告",
  qt: "其他公告",
  htly: "合同与履约",
});

const NON_BUSINESS_FIELDS = new Set([
  "平台名称", "平台代码", "公告ID", "公告类型", "公告子类型", "公告标题",
  "发布时间", "缺失字段", "公告正文", "公告内容", "解析状态", "内容指纹",
  "抽取方式", "抽取版本", "是否已核验", "爬虫时间", "详情页链接",
  "HTML快照路径", "HTML快照SHA256", "附件", "_trace",
]);

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
});

const STAGE_PRIORITY = Object.freeze({
  招标公告: 100,
  采购公告: 100,
  资格预审公告: 90,
  招标计划: 70,
  中标候选人公示: 60,
  成交候选人公示: 60,
  定标候选人公示: 55,
  中标结果公示: 50,
  成交公告: 50,
  合同与履约: 40,
  更正结果公示: 30,
  终止公告: 20,
  其他公告: 10,
});

const SHANXI_CITIES = [
  "太原市", "大同市", "阳泉市", "长治市", "晋城市", "朔州市",
  "晋中市", "运城市", "忻州市", "临汾市", "吕梁市",
];

const PROJECT_FIELD_NAMES = new Set([
  "项目名称",
  ...Object.values(FIELD_ALIASES).flat(),
]);

function resolveNoticeType(source, context = "record") {
  const rawType = requiredString(source["公告类型"], "公告类型", context);
  const codeType = NOTICE_TYPE_BY_CODE[rawType.toUpperCase()];
  // 新框架顶层公告类型已经按数据库口径规范化。采购/成交等源站名称只在
  // 公告子类型和溯源包中保留，不能再次把 TENDER/CANDIDATE/AWARD 展开成
  // 数据库未统一使用的中文变体。
  if (codeType) return codeType;
  const suffix = nullableString(source["公告子类型"])?.toLowerCase().split(".").at(-1);
  return (suffix && NOTICE_TYPE_BY_SUBTYPE[suffix]) || rawType;
}

const EMPTY_IDENTIFIER_VALUES = new Set(["无", "暂无", "不适用", "未提供", "-", "--", "/"]);
const IDENTIFIER_PROSE_MARKERS = [
  "资金来源", "招标人", "采购人", "已由", "批准", "本项目", "经评标委员会",
  "经评审", "现将", "现对", "进行公开招标", "在本地区", "建设单位",
  "项目名称", "预算金额", "最高限价", "采购需求",
  "招标范围", "计划期限", "计划工期", "质量要求", "标段划分",
  "发布日期", "公告日期", "采购品目", "交易编号", "涉及包号", "项目内容",
  "招标内容", "工程名称", "项目地址", "建设地点", "工程概况", "建设规模",
  "采购内容", "采购计划编号", "标书编号", "采购单位", "竞价时间",
  "招标公告", "采购公告", "中标公告", "成交公告", "候选人公示", "结果公示",
  "网上投标", "建设项目房建", "建设项目施工",
];

function normalizeIdentifier(value, field = "identifier", context = "record") {
  const source = nullableString(value);
  if (!source) return null;
  const normalized = source.normalize("NFKC").replace(/\s+/gu, "").toUpperCase();
  if (EMPTY_IDENTIFIER_VALUES.has(normalized)) return null;
  if ([...normalized].length < 4 || [...normalized].length > 191) {
    throw new Error(`${context}: ${field} has invalid length: ${source}`);
  }
  if (/[|；;]/u.test(normalized)) {
    throw new Error(`${context}: ${field} contains multiple identifiers: ${source}`);
  }
  if (/[：:&]/u.test(normalized)) {
    throw new Error(`${context}: ${field} contains field-label or HTML residue: ${source}`);
  }
  if (IDENTIFIER_PROSE_MARKERS.some((marker) => normalized.includes(marker))) {
    throw new Error(`${context}: ${field} contains prose: ${source}`);
  }
  for (const [opening, closing] of [["(", ")"], ["[", "]"], ["【", "】"]]) {
    if (normalized.split(opening).length !== normalized.split(closing).length) {
      throw new Error(`${context}: ${field} contains unbalanced brackets: ${source}`);
    }
  }
  if (!/[0-9]/u.test(normalized)) {
    throw new Error(`${context}: ${field} does not look like a business identifier: ${source}`);
  }
  return normalized;
}

function normalizeProjectName(value) {
  const text = nullableString(value);
  if (!text) return null;
  return text
    .normalize("NFKC")
    .replace(/[（]/gu, "(")
    .replace(/[）]/gu, ")")
    .replace(/\s+/gu, "")
    .replace(/(?:招标计划|资格预审公告|招标公告|采购公告|中标候选人公示|成交候选人公示|定标候选人公示|中标结果公示|中标公告|成交公告|结果公告|终止公告|合同公告)$/u, "")
    .toLowerCase();
}

function inferredProjectName(source) {
  const explicit = nullableString(source["项目名称"]);
  if (explicit) return explicit;
  const title = requiredString(source["公告标题"], "公告标题", "record");
  return title.replace(
    /(?:招标计划|资格预审公告|招标公告|采购公告|中标候选人公示|成交候选人公示|定标候选人公示|中标结果公示|中标公告|成交公告|结果公告|终止公告|合同公告)$/u,
    "",
  ).trim() || title;
}

function businessFields(source) {
  return Object.fromEntries(
    Object.entries(source).filter(([key]) => !NON_BUSINESS_FIELDS.has(key)),
  );
}

function mapBusinessRecord(record, options = {}) {
  const { source, context } = record;
  const parseStatus = (nullableString(source["解析状态"]) || "PENDING").toUpperCase();
  if (parseStatus !== "PARSED") {
    throw new Error(`${context}: project import requires 解析状态=PARSED, got ${parseStatus}`);
  }
  const includeContent = options.includeContent !== false;
  const fieldMode = options.fieldMode || "all";
  const combinedIdentifier = nullableString(
    source["项目编号/招标编号"] ?? source["招标编号/项目编号"],
  );
  const projectCode = normalizeIdentifier(source["项目编号"], "项目编号", context);
  const explicitTenderCode = normalizeIdentifier(source["招标编号"], "招标编号", context);
  // 组合字段仅兼容旧数据中的“单个编号”。它一旦含多个编号便没有可靠语义，
  // 不能猜测其中哪一个是招标编号，更不能把整个组合串写成数据库关联键。
  const legacyCombinedCode = !projectCode && !explicitTenderCode && !/[|；;]/u.test(combinedIdentifier || "")
    ? normalizeIdentifier(combinedIdentifier, "项目编号/招标编号", context)
    : null;
  const tenderCode = explicitTenderCode || legacyCombinedCode;
  const projectName = inferredProjectName(source);
  return {
    site: record.site,
    fileName: record.fileName,
    filePath: record.filePath,
    index: record.index,
    context,
    sourceNoticeId: requiredString(source["公告ID"], "公告ID", context),
    platformName: requiredString(source["平台名称"], "平台名称", context),
    title: requiredString(source["公告标题"], "公告标题", context),
    noticeType: resolveNoticeType(source, context),
    noticeSubtype: nullableString(source["公告子类型"]),
    projectCode,
    tenderCode,
    projectName,
    normalizedProjectName: normalizeProjectName(projectName),
    fields: fieldMode === "project"
      ? Object.fromEntries(Object.entries(source).filter(([key]) => (
        PROJECT_FIELD_NAMES.has(key)
      )))
      : businessFields(source),
    content: includeContent
      ? nullableString(source["公告正文"] ?? source["公告内容"])
      : null,
    publishDate: parseCrawlerDate(
      source["发布时间"] ?? source["发布日期"],
      "发布时间/发布日期",
      context,
    ),
    crawlTime: parseCrawlerDate(source["爬虫时间"], "爬虫时间", context, true),
    sourceUrl: requiredString(source["详情页链接"], "详情页链接", context),
    extractionModel: compactDatabaseString(
      requiredString(source["抽取方式"], "抽取方式", context),
      64,
    ),
    extractionVersion: compactDatabaseString(
      requiredString(source["抽取版本"], "抽取版本", context),
      32,
    ),
  };
}

function isBusinessReady(record) {
  return (nullableString(record?.source?.["解析状态"]) || "PENDING").toUpperCase() === "PARSED";
}

function syntheticTenderCode(site, tenderCode) {
  const value = `TENDER:${site}:${tenderCode}`;
  if ([...value].length <= 191) return value;
  return `TENDER:${site}:SHA256:${createHash("sha256").update(tenderCode).digest("hex")}`;
}

function syntheticNameCode(site, normalizedProjectName) {
  return `NAME:${site}:SHA256:${createHash("sha256").update(normalizedProjectName).digest("hex")}`;
}

function addToMapSet(map, key, value) {
  if (!key) return;
  if (!map.has(key)) map.set(key, new Set());
  map.get(key).add(value);
}

function groupBusinessRecords(records) {
  const groups = new Map();
  const recordGroupKeys = new Map();
  const tenderProjectCodes = new Map();

  for (const row of records) {
    if (row.projectCode && row.tenderCode) {
      addToMapSet(
        tenderProjectCodes,
        `${row.site}\u0000${row.tenderCode}`,
        row.projectCode,
      );
    }
  }

  function append(groupKey, row, identitySource) {
    if (!groups.has(groupKey)) {
      groups.set(groupKey, { groupKey, identitySource, records: [] });
    }
    groups.get(groupKey).records.push(row);
    recordGroupKeys.set(`${row.site}\u0000${row.sourceNoticeId}`, groupKey);
  }

  const pendingNameRows = [];
  for (const row of records) {
    if (row.projectCode) {
      append(`PROJECT\u0000${row.projectCode}`, row, "PROJECT_CODE");
      continue;
    }
    if (row.tenderCode) {
      const candidates = tenderProjectCodes.get(
        `${row.site}\u0000${row.tenderCode}`,
      ) || new Set();
      if (candidates.size === 1) {
        append(`PROJECT\u0000${[...candidates][0]}`, row, "PROJECT_CODE");
      } else if (candidates.size > 1) {
        // 同一招标编号已明确对应多个不同项目编号时，该编号不再具备唯一性；
        // 无项目编号公告必须回退到项目名称，不能合并成一个伪项目。
        pendingNameRows.push(row);
      } else {
        append(`TENDER\u0000${row.site}\u0000${row.tenderCode}`, row, "TENDER_CODE");
      }
      continue;
    }
    pendingNameRows.push(row);
  }

  const nameGroups = new Map();
  for (const group of groups.values()) {
    for (const row of group.records) {
      addToMapSet(
        nameGroups,
        `${row.site}\u0000${row.normalizedProjectName}`,
        group.groupKey,
      );
    }
  }
  for (const row of pendingNameRows) {
    const candidates = nameGroups.get(
      `${row.site}\u0000${row.normalizedProjectName}`,
    ) || new Set();
    if (candidates.size === 1) {
      append([...candidates][0], row, groups.get([...candidates][0]).identitySource);
    } else {
      append(`NAME\u0000${row.site}\u0000${row.normalizedProjectName}`, row, "PROJECT_NAME");
    }
  }
  return { groups: [...groups.values()], recordGroupKeys };
}

function businessRecordDigest(row) {
  return stableDigest({
    noticeType: row.noticeType,
    noticeSubtype: row.noticeSubtype,
    projectCode: row.projectCode,
    tenderCode: row.tenderCode,
    projectName: row.projectName,
    title: row.title,
    fields: row.fields,
    content: row.content,
    publishDate: row.publishDate,
    sourceUrl: row.sourceUrl,
    extractionModel: row.extractionModel,
    extractionVersion: row.extractionVersion,
  });
}

function deduplicateBusinessRecords(records) {
  const unique = new Map();
  let duplicateCount = 0;
  for (const row of records) {
    const key = `${row.site}\u0000${row.sourceNoticeId}`;
    const previous = unique.get(key);
    if (!previous) {
      unique.set(key, row);
      continue;
    }
    duplicateCount += 1;
    if (businessRecordDigest(previous) !== businessRecordDigest(row)) {
      throw new Error(
        `${row.context}: conflicting duplicate 公告ID=${row.sourceNoticeId}; `
        + `first seen at ${previous.context}`,
      );
    }
  }
  return {
    records: [...unique.values()],
    duplicateCount,
  };
}

function fieldValue(fields, aliases) {
  for (const alias of aliases) {
    const value = fields?.[alias];
    if (value !== null && value !== undefined && String(value).trim() !== "") return value;
  }
  return null;
}

function truncate(value, max = 191) {
  const text = nullableString(value);
  return text === null ? null : [...text].slice(0, max).join("");
}

function decimalValue(value, field, context) {
  if (value === null || value === undefined || value === "") return null;
  const text = String(value).replace(/[,，]/gu, "").trim();
  const match = text.match(/^(-?\d+(?:\.\d+)?)\s*(亿元|万元|元)?$/u);
  // 百分比、折扣、收费标准和中文大写金额不能安全映射到项目表 DECIMAL。
  // 原文仍完整保存在 project_notice.structured_data/Mongo 中，此处留空。
  if (!match) return null;
  const multipliers = { 亿元: 100_000_000, 万元: 10_000, 元: 1 };
  const number = Number(match[1]) * (multipliers[match[2]] || 1);
  if (!Number.isFinite(number)) throw new Error(`${context}: ${field} is outside numeric range`);
  return number.toFixed(2);
}

function parseOptionalDate(value) {
  const text = nullableString(value);
  if (!text) return null;
  const normalized = text.replace(/\//gu, "-");
  const withZone = /^\d{4}-\d{2}-\d{2}(?:[ T]\d{2}:\d{2}(?::\d{2})?)?/u.test(normalized)
    ? `${normalized.replace(" ", "T")}+08:00`
    : normalized;
  const date = new Date(withZone);
  return Number.isNaN(date.getTime()) ? null : date;
}

function parseLocation(value) {
  const text = nullableString(value);
  if (!text) return { province: null, city: null };
  const normalized = text.normalize("NFKC").replace(/\s+/gu, "");
  const cities = SHANXI_CITIES.filter((city) => normalized.includes(city));
  return {
    province: /山西(?:省)?/u.test(normalized) || cities.length ? "山西省" : null,
    city: cities.length ? [...new Set(cities)].join("、") : null,
  };
}

function compareAuthority(left, right) {
  const stage = (STAGE_PRIORITY[right.noticeType] || 0) - (STAGE_PRIORITY[left.noticeType] || 0);
  if (stage !== 0) return stage;
  return (right.publishDate?.getTime() || 0) - (left.publishDate?.getTime() || 0);
}

function pickValue(records, aliases) {
  for (const row of records) {
    const value = fieldValue(row.fields, aliases);
    if (value !== null) return value;
  }
  return null;
}

function determineStatus(records) {
  const newest = [...records].sort(
    (left, right) => (right.publishDate?.getTime() || 0) - (left.publishDate?.getTime() || 0),
  );
  const abnormal = newest.find((row) => /撤销|终止|暂停/u.test(`${row.title} ${row.projectName}`));
  if (abnormal) {
    if (/撤销/u.test(abnormal.title)) return "CANCELLED";
    if (/暂停/u.test(abnormal.title)) return "SUSPENDED";
    return "TERMINATED";
  }
  const types = new Set(records.map((row) => row.noticeType));
  if (types.has("合同与履约")) return "CONTRACT";
  if (types.has("中标结果公示") || types.has("成交公告")) return "AWARD";
  if (types.has("定标候选人公示")) return "FINAL_CANDIDATE";
  if (types.has("中标候选人公示") || types.has("成交候选人公示")) return "CANDIDATE";
  const deadlines = records.flatMap((row) => [
    fieldValue(row.fields, FIELD_ALIASES.bidSubmissionDeadline),
    fieldValue(row.fields, FIELD_ALIASES.bidOpenTime),
  ]).map(parseOptionalDate).filter(Boolean);
  if (deadlines.length && Math.max(...deadlines.map((date) => date.getTime())) <= Date.now()) {
    return "EVALUATING";
  }
  if (types.has("招标公告") || types.has("采购公告")) return "TENDER";
  if (types.has("资格预审公告")) return "PREQUALIFICATION";
  return "PLAN";
}

function buildProjectRows(groups) {
  return groups.map((group) => {
    const records = [...group.records].sort(compareAuthority);
    const projectCodes = [...new Set(records.map((row) => row.projectCode).filter(Boolean))];
    if (projectCodes.length > 1) {
      throw new Error(`${group.groupKey}: multiple project codes: ${projectCodes.join(", ")}`);
    }
    const tenderAliases = [...new Set(
      records.filter((row) => row.tenderCode).map((row) => syntheticTenderCode(row.site, row.tenderCode)),
    )];
    const projectCode = projectCodes[0]
      || (group.identitySource === "TENDER_CODE" ? tenderAliases[0] : null)
      || (group.identitySource === "PROJECT_NAME"
        ? syntheticNameCode(records[0].site, records[0].normalizedProjectName)
        : null);
    const projectName = pickValue(records, ["项目名称"]) || records[0].projectName;
    const locationText = pickValue(records, FIELD_ALIASES.locationText);
    const location = parseLocation(locationText);
    const bidOpenDates = records
      .map((row) => parseOptionalDate(fieldValue(row.fields, FIELD_ALIASES.bidOpenTime)))
      .filter(Boolean)
      .sort((left, right) => right - left);
    return {
      groupKey: group.groupKey,
      identitySource: group.identitySource,
      aliases: tenderAliases,
      records: group.records,
      data: {
        projectCode: truncate(projectCode),
        projectName: truncate(projectName),
        projectNature: truncate(pickValue(records, FIELD_ALIASES.projectNature)),
        industry: truncate(pickValue(records, FIELD_ALIASES.industry)),
        projectType: truncate(pickValue(records, FIELD_ALIASES.projectType)),
        tenderMethod: truncate(pickValue(records, FIELD_ALIASES.tenderMethod)),
        organizationForm: truncate(pickValue(records, FIELD_ALIASES.organizationForm)),
        province: location.province,
        city: location.city,
        locationText: nullableString(locationText),
        ownerCompanyId: null,
        ownerCompanyName: truncate(pickValue(records, FIELD_ALIASES.ownerCompanyName)),
        agencyCompanyName: truncate(pickValue(records, FIELD_ALIASES.agencyCompanyName)),
        estimatedAmount: decimalValue(
          pickValue(records, FIELD_ALIASES.estimatedAmount),
          "estimated amount",
          group.groupKey,
        ),
        tenderAmount: decimalValue(
          pickValue(records, FIELD_ALIASES.tenderAmount),
          "tender amount",
          group.groupKey,
        ),
        fundSource: truncate(pickValue(records, FIELD_ALIASES.fundSource)),
        bidOpenTime: bidOpenDates[0] || null,
        duration: truncate(pickValue(records, FIELD_ALIASES.duration)),
        qualityRequirement: nullableString(pickValue(records, FIELD_ALIASES.qualityRequirement)),
        supervisorDepartment: truncate(pickValue(records, FIELD_ALIASES.supervisorDepartment)),
        currentStatus: determineStatus(records),
        firstPublishDate: records.map((row) => row.publishDate).filter(Boolean).sort((a, b) => a - b)[0] || null,
      },
    };
  });
}

function buildMappedBusinessDataset(mapped) {
  const unique = deduplicateBusinessRecords(mapped);
  const grouped = groupBusinessRecords(unique.records);
  return {
    records: unique.records,
    duplicateCount: unique.duplicateCount,
    groups: grouped.groups,
    recordGroupKeys: grouped.recordGroupKeys,
    projects: buildProjectRows(grouped.groups),
  };
}

function buildBusinessDataset(loadedRecords, options = {}) {
  return buildMappedBusinessDataset(
    loadedRecords.map((record) => mapBusinessRecord(record, options)),
  );
}

async function loadBusinessDataset(outputRoot, sites, options = {}) {
  const mapped = [];
  let skippedNonParsedCount = 0;
  for await (const record of iterateJsonNotices(outputRoot, sites)) {
    if (!isBusinessReady(record)) {
      skippedNonParsedCount += 1;
      continue;
    }
    mapped.push(mapBusinessRecord(record, options));
  }
  return {
    ...buildMappedBusinessDataset(mapped),
    skippedNonParsedCount,
  };
}

module.exports = {
  NON_BUSINESS_FIELDS,
  NOTICE_TYPE_BY_CODE,
  NOTICE_TYPE_BY_SUBTYPE,
  buildBusinessDataset,
  buildMappedBusinessDataset,
  businessRecordDigest,
  buildProjectRows,
  businessFields,
  groupBusinessRecords,
  isBusinessReady,
  deduplicateBusinessRecords,
  loadBusinessDataset,
  mapBusinessRecord,
  normalizeIdentifier,
  normalizeProjectName,
  resolveNoticeType,
  syntheticTenderCode,
  syntheticNameCode,
};
