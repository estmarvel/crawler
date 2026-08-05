"use strict";

const fs = require("node:fs");
const path = require("node:path");

const SELECTED_MAPPING_STATUSES = new Set(["MATCHED", "STANDALONE_PROJECT"]);
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
});

const STAGE_PRIORITY = Object.freeze({
  招标计划: 70,
  资格预审公告: 90,
  招标公告: 100,
  采购公告: 100,
  中标候选人公示: 60,
  成交候选人公示: 60,
  定标候选人公示: 50,
  中标结果公示: 50,
  成交公告: 50,
  合同与履约: 40,
  更正结果公示: 30,
  终止公告: 20,
});

const SHANXI_CITIES = [
  "太原市", "大同市", "阳泉市", "长治市", "晋城市", "朔州市",
  "晋中市", "运城市", "忻州市", "临汾市", "吕梁市",
];

function nullableString(value) {
  if (value === null || value === undefined) return null;
  const text = String(value).trim();
  return text === "" ? null : text;
}

function chunks(values, size = 500) {
  const result = [];
  for (let index = 0; index < values.length; index += size) result.push(values.slice(index, index + size));
  return result;
}

function fieldValue(fields, aliases) {
  for (const alias of aliases) {
    const value = nullableString(fields?.[alias]);
    if (value !== null) return value;
  }
  return null;
}

function readMappings(mappingRoot, sites) {
  const all = [];
  for (const site of sites) {
    const filePath = path.join(mappingRoot, `${site}_project_mapping.json`);
    if (!fs.existsSync(filePath)) throw new Error(`Mapping file does not exist: ${filePath}`);
    const document = JSON.parse(fs.readFileSync(filePath, "utf8"));
    if (!Array.isArray(document.records)) throw new Error(`${filePath}: records must be an array`);
    for (const source of document.records) {
      const sourceNoticeId = nullableString(source["公告ID"]);
      if (!sourceNoticeId) throw new Error(`${filePath}: mapping row has no 公告ID`);
      all.push({
        site,
        sourceNoticeId,
        noticeType: nullableString(source["公告类型"]),
        projectCode: nullableString(source["项目编号"]),
        standaloneProjectName: nullableString(source["独立项目名称"]),
        status: nullableString(source["匹配状态"]),
        source,
      });
    }
  }
  const seen = new Set();
  for (const row of all) {
    const key = `${row.site}\u0000${row.sourceNoticeId}`;
    if (seen.has(key)) throw new Error(`Duplicate mapping identity: ${row.site}/${row.sourceNoticeId}`);
    seen.add(key);
  }
  return {
    all,
    selected: all.filter((row) => SELECTED_MAPPING_STATUSES.has(row.status)),
    review: all.filter((row) => !SELECTED_MAPPING_STATUSES.has(row.status)),
  };
}

function chooseExtraction(rows) {
  return [...rows].sort((left, right) => {
    if (left.isVerified !== right.isVerified) return Number(right.isVerified) - Number(left.isVerified);
    const time = right.updatedAt.getTime() - left.updatedAt.getTime();
    if (time !== 0) return time;
    return left.id < right.id ? 1 : -1;
  })[0] || null;
}

function validObjectId(ObjectId, value) {
  return typeof value === "string" && ObjectId.isValid(value);
}

async function hydrateMappings(stores, mappings, dataSources) {
  const ids = [...dataSources.values()].map((source) => source.id);
  const rawRows = await stores.prisma.rawNotice.findMany({
    where: { dataSourceId: { in: ids } },
    include: { dataSource: true, extractionResults: true },
  });
  const siteByDataSourceId = new Map([...dataSources].map(([site, source]) => [source.id, site]));
  const rawByKey = new Map(
    rawRows.map((raw) => [`${siteByDataSourceId.get(raw.dataSourceId)}\u0000${raw.sourceNoticeId}`, raw]),
  );
  const selected = [];
  const stale = [];
  for (const mapping of mappings) {
    const raw = rawByKey.get(`${mapping.site}\u0000${mapping.sourceNoticeId}`);
    if (!raw) {
      stale.push(mapping);
      continue;
    }
    const extraction = chooseExtraction(raw.extractionResults);
    if (!extraction) throw new Error(`${mapping.site}/${mapping.sourceNoticeId}: no notice_extraction row`);
    selected.push({ mapping, raw, extraction });
  }

  const rawIds = selected
    .filter((row) => validObjectId(stores.ObjectId, row.raw.mongoDocumentId))
    .map((row) => new stores.ObjectId(row.raw.mongoDocumentId));
  const extractionIds = selected
    .filter((row) => validObjectId(stores.ObjectId, row.extraction.mongoDocumentId))
    .map((row) => new stores.ObjectId(row.extraction.mongoDocumentId));
  const [rawDocuments, extractionDocuments] = await Promise.all([
    stores.mongo.collection("raw_notices").find({ _id: { $in: rawIds } }).toArray(),
    stores.mongo.collection("notice_extractions").find({ _id: { $in: extractionIds } }).toArray(),
  ]);
  const rawDocumentById = new Map(rawDocuments.map((document) => [document._id.toHexString(), document]));
  const extractionDocumentById = new Map(
    extractionDocuments.map((document) => [document._id.toHexString(), document]),
  );

  const hydrated = selected.map(({ mapping, raw, extraction }) => {
    const rawDocument = rawDocumentById.get(raw.mongoDocumentId);
    const extractionDocument = extractionDocumentById.get(extraction.mongoDocumentId);
    if (!rawDocument) throw new Error(`${mapping.site}/${mapping.sourceNoticeId}: MongoDB raw document not found`);
    if (!extractionDocument) throw new Error(`${mapping.site}/${mapping.sourceNoticeId}: MongoDB extraction document not found`);
    if (mapping.noticeType && mapping.noticeType !== extraction.noticeType) {
      throw new Error(`${mapping.site}/${mapping.sourceNoticeId}: mapping notice type ${mapping.noticeType} differs from ${extraction.noticeType}`);
    }
    return {
      mapping,
      id: extraction.id,
      rawNoticeId: raw.id,
      noticeType: extraction.noticeType,
      extractedFields: extractionDocument.extractedFields,
      extraction,
      rawNotice: raw,
      rawDocument,
    };
  });
  return { hydrated, stale, rawRows };
}

function sourcePriority(record) {
  const amendment = /延期|变更|更正|补充|终止|暂停|控制价|重新招标/u.test(
    `${fieldValue(record.extractedFields, ["项目名称"]) || ""} ${record.rawNotice.title || ""}`,
  );
  return (STAGE_PRIORITY[record.noticeType] || 0) - (amendment ? 40 : 0);
}

function publicationDate(record) {
  const value = fieldValue(record.extractedFields, FIELD_ALIASES.publishDate);
  const parsed = value ? new Date(value.replace(" ", "T") + "+08:00") : record.rawNotice.publishDate;
  return parsed && !Number.isNaN(parsed.getTime()) ? parsed : null;
}

function compareRecords(left, right) {
  const priority = sourcePriority(right) - sourcePriority(left);
  if (priority !== 0) return priority;
  return (publicationDate(left)?.getTime() || 0) - (publicationDate(right)?.getTime() || 0);
}

function pickValue(records, aliases) {
  for (const record of records) {
    const value = fieldValue(record.extractedFields, aliases);
    if (value !== null) return value;
  }
  return null;
}

function truncate(value, max = 191) {
  if (value === null) return null;
  return [...value].slice(0, max).join("");
}

function decimalValue(value, field, context) {
  const text = nullableString(value)?.replace(/,/g, "") || null;
  if (text === null) return null;
  if (!/^-?\d+(?:\.\d+)?$/.test(text)) throw new Error(`${context}: ${field} is not numeric: ${value}`);
  const number = Number(text);
  if (!Number.isFinite(number)) throw new Error(`${context}: ${field} is outside numeric range`);
  return number.toFixed(2);
}

function parseLocation(value) {
  const raw = nullableString(value);
  if (!raw) return { province: null, city: null };
  const normalized = raw.normalize("NFKC").replace(/\s+/g, "");
  const segments = normalized.split("|");
  let cities = [];
  for (const segment of segments) {
    cities = SHANXI_CITIES.filter((city) => segment.includes(city));
    if (cities.length) break;
  }
  return {
    province: /山西(?:省)?/u.test(normalized) || cities.length ? "山西省" : null,
    city: cities.length ? [...new Set(cities)].join("、") : null,
  };
}

function abnormalStatus(record) {
  const text = `${record.rawNotice.title || ""} ${fieldValue(record.extractedFields, ["项目名称"]) || ""}`;
  if (/撤销/u.test(text)) return "CANCELLED";
  if (/暂停/u.test(text)) return "SUSPENDED";
  if (/终止/u.test(text)) return "TERMINATED";
  return null;
}

function determineStatus(records) {
  const chronological = [...records].sort(
    (left, right) => (publicationDate(right)?.getTime() || 0) - (publicationDate(left)?.getTime() || 0),
  );
  const latestAbnormal = chronological.find((record) => abnormalStatus(record));
  const latestNormal = chronological.find((record) => !abnormalStatus(record));
  if (latestAbnormal && (!latestNormal || (publicationDate(latestAbnormal)?.getTime() || 0) >= (publicationDate(latestNormal)?.getTime() || 0))) {
    return abnormalStatus(latestAbnormal);
  }
  if (records.some((row) => row.noticeType === "合同与履约")) return "CONTRACT";
  if (records.some((row) => ["中标结果公示", "成交公告"].includes(row.noticeType))) return "AWARD";
  if (records.some((row) => row.noticeType === "定标候选人公示")) return "FINAL_CANDIDATE";
  if (records.some((row) => ["中标候选人公示", "成交候选人公示"].includes(row.noticeType))) return "CANDIDATE";
  const deadlines = records.flatMap((record) => [
    fieldValue(record.extractedFields, FIELD_ALIASES.bidSubmissionDeadline),
    fieldValue(record.extractedFields, FIELD_ALIASES.bidOpenTime),
  ]).filter(Boolean).map((value) => new Date(value.replace(" ", "T") + "+08:00")).filter((date) => !Number.isNaN(date.getTime()));
  if (deadlines.length && Math.max(...deadlines.map((date) => date.getTime())) <= Date.now()) return "EVALUATING";
  if (records.some((row) => ["招标公告", "采购公告"].includes(row.noticeType))) return "TENDER";
  if (records.some((row) => row.noticeType === "资格预审公告")) return "PREQUALIFICATION";
  return "PLAN";
}

function buildProjects(records) {
  const groups = new Map();
  for (const record of records) {
    const mapping = record.mapping;
    const key = mapping.projectCode
      ? `CODE\u0000${mapping.projectCode}`
      : `STANDALONE\u0000${mapping.site}\u0000${mapping.standaloneProjectName}`;
    if (!groups.has(key)) groups.set(key, { key, projectCode: mapping.projectCode, records: [] });
    groups.get(key).records.push(record);
  }

  const projects = [];
  for (const group of groups.values()) {
    const recordsByAuthority = [...group.records].sort(compareRecords);
    const projectName = pickValue(recordsByAuthority, ["项目名称"])
      || group.records[0].mapping.standaloneProjectName;
    if (!projectName) throw new Error(`${group.key}: project name is missing`);
    const locationText = pickValue(recordsByAuthority, FIELD_ALIASES.locationText);
    const location = parseLocation(locationText);
    const publishDates = recordsByAuthority.map(publicationDate).filter(Boolean).sort((a, b) => a - b);
    const bidOpenDates = recordsByAuthority
      .map((row) => fieldValue(row.extractedFields, FIELD_ALIASES.bidOpenTime))
      .filter(Boolean)
      .map((value) => new Date(value.replace(" ", "T") + "+08:00"))
      .filter((date) => !Number.isNaN(date.getTime()))
      .sort((a, b) => b - a);
    const context = group.projectCode || group.key;
    projects.push({
      groupKey: group.key,
      projectCode: truncate(group.projectCode),
      projectName: truncate(projectName),
      projectNature: truncate(pickValue(recordsByAuthority, FIELD_ALIASES.projectNature)),
      industry: truncate(pickValue(recordsByAuthority, FIELD_ALIASES.industry)),
      projectType: truncate(pickValue(recordsByAuthority, FIELD_ALIASES.projectType)),
      tenderMethod: truncate(pickValue(recordsByAuthority, FIELD_ALIASES.tenderMethod)),
      organizationForm: truncate(pickValue(recordsByAuthority, FIELD_ALIASES.organizationForm)),
      province: location.province,
      city: location.city,
      locationText,
      ownerCompanyId: null,
      ownerCompanyName: truncate(pickValue(recordsByAuthority, FIELD_ALIASES.ownerCompanyName)),
      agencyCompanyName: truncate(pickValue(recordsByAuthority, FIELD_ALIASES.agencyCompanyName)),
      estimatedAmount: decimalValue(pickValue(recordsByAuthority, FIELD_ALIASES.estimatedAmount), "estimated amount", context),
      tenderAmount: decimalValue(pickValue(recordsByAuthority, FIELD_ALIASES.tenderAmount), "tender amount", context),
      fundSource: truncate(pickValue(recordsByAuthority, FIELD_ALIASES.fundSource)),
      bidOpenTime: bidOpenDates[0] || null,
      duration: truncate(pickValue(recordsByAuthority, FIELD_ALIASES.duration)),
      qualityRequirement: pickValue(recordsByAuthority, FIELD_ALIASES.qualityRequirement),
      supervisorDepartment: truncate(pickValue(recordsByAuthority, FIELD_ALIASES.supervisorDepartment)),
      currentStatus: determineStatus(recordsByAuthority),
      firstPublishDate: publishDates[0] || null,
      records: group.records,
    });
  }
  projects.sort((left, right) => (left.projectCode || `~${left.projectName}`).localeCompare(right.projectCode || `~${right.projectName}`, "zh-CN"));
  return projects;
}

function projectLookupKey(projectCode, site, standaloneProjectName) {
  return projectCode
    ? `CODE\u0000${projectCode}`
    : `STANDALONE\u0000${site}\u0000${standaloneProjectName}`;
}

module.exports = {
  buildProjects,
  chunks,
  hydrateMappings,
  projectLookupKey,
  readMappings,
};
