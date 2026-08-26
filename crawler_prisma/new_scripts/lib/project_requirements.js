"use strict";

const { createHash } = require("node:crypto");

const SCHEMA_VERSION = "1.0";

const REQUIREMENT_SUBTYPES = Object.freeze([
  "COMPANY_QUALIFICATION",
  "COMPANY_LICENSE",
  "COMPANY_CERTIFICATION",
  "PERSONNEL_CERTIFICATE",
  "PERFORMANCE",
  "CREDIT",
  "BASIC_CONDITION",
  "CONSORTIUM",
  "OTHER",
]);

const COMPARE_VALUES = new Set([
  "EXACT", "AT_LEAST", "AT_MOST", "ONE_OF", "EXISTS", "NOT_ALLOWED", "UNSPECIFIED",
]);
const VALID_AT_VALUES = new Set([
  "CURRENT", "PUBLISH_DATE", "BID_DEADLINE", "OPENING_DATE", "CONTRACT_DATE", "UNSPECIFIED",
]);
const VERIFICATION_STATUS_VALUES = new Set(["UNVERIFIED", "SYSTEM_VERIFIED", "MANUAL_VERIFIED", "REJECTED"]);
const EFFECTIVE_STATUS_VALUES = new Set(["ACTIVE", "SUPERSEDED", "CANCELLED"]);
const EXTRACTION_METHOD_VALUES = new Set(["RULE", "RULE_QWEN", "QWEN", "MANUAL"]);
const BASIC_CONDITION_CODE_VALUES = new Set([
  "LEGAL_ENTITY", "BUSINESS_LICENSE", "BANK_ACCOUNT", "GENERAL_TAXPAYER", "FINANCIAL_STATUS",
  "TAX_PAYMENT", "SOCIAL_SECURITY", "EQUIPMENT", "MANUFACTURER_AUTHORIZATION", "SERVICE_AUTHORIZATION",
  "INDEPENDENT_RESPONSIBILITY", "RELATED_PARTY_RESTRICTION", "BID_LOT_RESTRICTION", "MANUFACTURER_STATUS",
  "PRODUCT_CERTIFICATION", "PRODUCT_TEST_REPORT", "PROFESSIONAL_ROSTER", "REGISTERED_CAPITAL",
  "INVOICE_CAPABILITY", "SUPPLY_CAPABILITY", "PERSONNEL_CAPACITY", "PERSONNEL_EMPLOYMENT",
  "VEHICLE_DOCUMENT", "STORAGE_CAPABILITY", "PRIOR_SERVICE_CONFLICT", "NO_SPECIAL_QUALIFICATION", "OTHER",
]);

const DETAIL_DEFAULTS = Object.freeze({
  COMPANY_QUALIFICATION: Object.freeze({
    name: null,
    profession: null,
    level: null,
    compare: "UNSPECIFIED",
    issuingAuthority: null,
    requiredScope: null,
  }),
  COMPANY_LICENSE: Object.freeze({
    name: null,
    licenseCategory: null,
    permittedScope: null,
    level: null,
    compare: "EXISTS",
    issuingAuthority: null,
  }),
  COMPANY_CERTIFICATION: Object.freeze({
    name: null,
    standardCode: null,
    certificationScope: null,
    level: null,
    compare: "EXISTS",
    issuingAuthority: null,
  }),
  PERSONNEL_CERTIFICATE: Object.freeze({
    role: null,
    certificateName: null,
    major: null,
    level: null,
    compare: "UNSPECIFIED",
    minimumCount: 1,
    registrationUnitRequired: null,
    socialSecurityMonths: null,
    noConcurrentProject: null,
    titleName: null,
    titleLevel: null,
  }),
  PERFORMANCE: Object.freeze({
    performanceOwner: "COMPANY",
    projectCategory: null,
    projectKeywords: [],
    withinYears: null,
    dateFrom: null,
    dateTo: null,
    minimumCount: 1,
    minimumSingleAmount: null,
    minimumTotalAmount: null,
    currency: "CNY",
    completionRequired: null,
    contractRequired: null,
    region: null,
  }),
  CREDIT: Object.freeze({
    creditSubject: "COMPANY",
    platform: null,
    restrictedList: null,
    condition: "NOT_LISTED",
    withinYears: null,
    dateFrom: null,
    dateTo: null,
  }),
  BASIC_CONDITION: Object.freeze({
    conditionCode: "OTHER",
    name: null,
    value: null,
    unit: null,
    compare: "EXISTS",
    minimumYears: null,
    minimumAmount: null,
    documentType: null,
  }),
  CONSORTIUM: Object.freeze({
    allowed: null,
    maximumMembers: null,
    leaderRequired: null,
    leaderRole: null,
    memberRoles: [],
    agreementRequired: null,
    jointLiabilityRequired: null,
    memberRequirementTexts: [],
  }),
  OTHER: Object.freeze({
    category: "UNCLASSIFIED",
    description: null,
    manualReviewRequired: true,
    reviewReason: null,
  }),
});

const SUBJECT_BY_SUBTYPE = Object.freeze({
  COMPANY_QUALIFICATION: "COMPANY",
  COMPANY_LICENSE: "COMPANY",
  COMPANY_CERTIFICATION: "COMPANY",
  PERSONNEL_CERTIFICATE: "PERSONNEL",
  PERFORMANCE: "COMPANY",
  CREDIT: "COMPANY",
  BASIC_CONDITION: "COMPANY",
  CONSORTIUM: "CONSORTIUM",
  OTHER: "UNSPECIFIED",
});

const RULE_BY_SUBTYPE = Object.freeze({
  COMPANY_QUALIFICATION: "HAS_QUALIFICATION",
  COMPANY_LICENSE: "HAS_LICENSE",
  COMPANY_CERTIFICATION: "HAS_CERTIFICATION",
  PERSONNEL_CERTIFICATE: "HAS_PERSONNEL_CERTIFICATE",
  PERFORMANCE: "HAS_PERFORMANCE",
  CREDIT: "SATISFIES_CREDIT_CONDITION",
  BASIC_CONDITION: "SATISFIES_BASIC_CONDITION",
  CONSORTIUM: "CONSORTIUM_POLICY",
  OTHER: "MANUAL_REVIEW",
});

const LEVEL_MAP = Object.freeze({
  壹级: "一级", 贰级: "二级", 叁级: "三级", 肆级: "四级",
  壹: "一级", 贰: "二级", 叁: "三级", 肆: "四级",
  一: "一级", 二: "二级", 三: "三级", 四: "四级",
  甲: "甲级", 乙: "乙级", 丙: "丙级", 特: "特级",
});

const BASIC_PATTERNS = Object.freeze([
  ["LEGAL_ENTITY", "独立法人资格", /(?:独立的?法人资格|独立(?:企业|事业单位)?法人)/u, "营业执照"],
  ["BUSINESS_LICENSE", "有效营业执照", /(?:有效的?)?营业执照/u, "营业执照"],
  ["BANK_ACCOUNT", "基本账户信息", /(?:基本账户开户许可证|基本存款账户信息|基本账户信息)/u, "基本账户证明"],
  ["GENERAL_TAXPAYER", "一般纳税人资格", /一般纳税人(?:资格)?/u, "纳税人证明"],
  ["INDEPENDENT_RESPONSIBILITY", "独立承担民事责任能力", /独.?承担民事责任(?:的能力)?/u, null],
  ["FINANCIAL_STATUS", "良好财务状况", /(?:良好的?)?(?:财务状况|财务(?:会计)?制度|财务要求|企业资信)/u, "财务证明"],
  ["TAX_PAYMENT", "依法缴纳税收", /依法缴纳税收|纳税证明/u, "纳税证明"],
  ["SOCIAL_SECURITY", "依法缴纳社会保障资金", /依法缴纳社会保障资金|社保缴纳证明/u, "社保证明"],
  ["EQUIPMENT", "项目所需设备能力", /(?:(?:车辆|机械|设备)要求|(?:自有|租赁|配备|具备|具有)[^；。]{0,80}(?:设备|机械|钻机|车辆|吊车|货车|挖掘机|铲车|泵车|仪器))/u, "设备证明"],
  ["MANUFACTURER_AUTHORIZATION", "制造商授权", /(?:制造商|生产厂家)[^；。]{0,20}授权/u, "授权书"],
  ["SERVICE_AUTHORIZATION", "服务、代理或合作授权", /(?:(?:服务|代理|媒体投放)[^；。]{0,30}授权|(?:法定|第三方)[^；。]{0,30}(?:授权文件|合作协议))/u, "授权证明"],
  ["RELATED_PARTY_RESTRICTION", "关联关系投标限制", /(?:单位负责人为同一人|存在(?:直接)?(?:控股|管理)关系|存在控股、?管理关系|具有投资参股关系|负责人为同一个|董(?:事|监)|高级管理人员相互兼职|关联企业|与招标人存在利害关系|法律法规规章规定的具有关联关系)/u, "承诺书"],
  ["BID_LOT_RESTRICTION", "标段投标或中标数量限制", /(?:同时投报|同时参加|只能对|可就)[^；。]{0,40}(?:标段|项目)|(?:仅可|只可|只允许|最多)中标[^；。]{0,12}(?:一个|1个)标段/u, null],
  ["MANUFACTURER_STATUS", "制造商或代理商身份要求", /(?:须为|应为|仅限|要求)[^；。]{0,20}(?:制造商|生产厂家)|(?:不)?接受(?:代理商|经销商)(?:投标)?|一个制造商[^；。]{0,50}仅能委托一个(?:代理商|经销商|投标人)/u, "制造商证明"],
  ["PRODUCT_CERTIFICATION", "产品认证证书", /(?:强制性产品认证[^；。]{0,12}|国家实行生产许可或其他强制认证[^；。]{0,30}有效的?证书|出厂合格证|CCC认证|CQC认证|防爆合格证(?:书)?|煤安标志(?:认证)?证书|MA标志证书)/iu, "产品认证证书"],
  ["PRODUCT_TEST_REPORT", "产品检验或检测报告", /(?:产品|货物|材料|设备|油罐|加油机)?[^；。]{0,70}(?:检验|检测|检验测试)(?:[（(](?:检验|检测)[）)])?(?:报告|鉴定证书)/u, "检验检测报告"],
  ["PROFESSIONAL_ROSTER", "专业机构名录或供应商库", /(?:列入|进入|为)[^；。]{0,30}(?:推荐名录|机构名录|供应商库|备选库|入库企业)/u, "名录查询证明"],
  ["EQUIPMENT", "设备产权或租赁证明", /(?:产权证明|设备租赁协议|购置合同(?:及|和|、)发票|购置或者自产[^；。]{0,50}(?:合同|发票))/u, "设备证明"],
  ["FINANCIAL_STATUS", "财务审计或资信证明", /(?:财务审计报告|经审计的财务报告|财务报表|(?:银行|基本开户行)[^；。]{0,10}资信证明)/u, "财务证明"],
  ["REGISTERED_CAPITAL", "注册资本要求", /注册(?:资金|资本)[^；。]{0,18}(?:万元|亿元|元)/u, "营业执照"],
  ["INVOICE_CAPABILITY", "发票开具能力", /(?:可|能够|应能)开具[^；。]{0,16}(?:增值税)?(?:专用)?发票/u, "纳税人证明"],
  ["SUPPLY_CAPABILITY", "供货或售后服务能力", /(?:供货能力|售后服务能力|履行合同所必需的[^；。]{0,20}能力)/u, null],
  ["PERSONNEL_CAPACITY", "项目人员配置能力", /(?:至少配备|人员配置|项目班子成员)[^；。]{0,100}(?:人员|人|名)/u, "人员证明"],
  ["PERSONNEL_EMPLOYMENT", "项目人员劳动或执业关系", /(?:拟派|拟任|项目)[^；。]{0,100}(?:本单位执业|本单位注册|本单位人员|本单位正式员工|缴纳[^；。]{0,12}(?:社保|养老保险)|不得同时担任|不得互相兼任)/u, "劳动或社保证明"],
  ["VEHICLE_DOCUMENT", "车辆证件和保险", /(?:行驶证|道路运输证|车辆保险|机动车保险)/u, "车辆证明"],
  ["VEHICLE_DOCUMENT", "车辆准入公告", /(?:工业和信息化部|工信部)[^；。]{0,30}(?:车辆|车型|规格型号)?[^；。]{0,12}公告/u, "工信部车辆公告"],
  ["SUPPLY_CAPABILITY", "人员设备资金实施能力", /人员、?设备、?资金等方面具有相应的?(?:实施|服务)?能力/u, null],
  ["STORAGE_CAPABILITY", "特定物品储存能力", /(?:民用爆炸物品)?储存库|移动库/u, "储存设施证明"],
  ["PRIOR_SERVICE_CONFLICT", "既往服务利益冲突限制", /不接受已提供[^；。]{0,40}(?:服务|评价|审查)[^；。]{0,20}(?:机构|单位)参加投标|承担过[^；。]{0,30}(?:评价|审查)[^；。]{0,20}不得[^；。]{0,20}投标/u, "承诺书"],
]);

const LICENSE_PATTERNS = Object.freeze([
  "安全生产许可证", "医疗器械经营许可证", "医疗器械生产许可证", "食品经营许可证",
  "食品生产许可证", "道路运输经营许可证", "特种设备生产许可证", "特种设备安装改造维修许可证",
  "劳务派遣经营许可证", "增值电信业务经营许可证", "印刷经营许可证",
  "辐射安全许可证", "爆破作业单位许可证", "危险化学品道路运输许可证",
  "特种设备检验检测机构核准证",
  "危险化学品经营许可证", "危险化学品生产许可证", "金融许可证",
  "特种设备制造许可证", "特种设备设计许可证", "承装电力设施许可证",
  "承修电力设施许可证", "承试电力设施许可证", "制造计量器具许可证",
]);

const CERTIFICATION_PATTERNS = Object.freeze([
  ["质量管理体系认证", "ISO9001"],
  ["环境管理体系认证", "ISO14001"],
  ["职业健康安全管理体系认证", "ISO45001"],
  ["信息安全管理体系认证", "ISO27001"],
  ["信息技术服务管理体系认证", "ISO20000"],
  ["建设施工行业质量管理体系认证", "GB/T 50430"],
]);

const CREDIT_PLATFORMS = Object.freeze([
  "信用中国", "中国执行信息公开网", "国家企业信用信息公示系统", "中国政府采购网",
  "全国建筑市场监管公共服务平台", "裁判文书网", "中国裁判文书网",
]);

const CREDIT_LISTS = Object.freeze([
  "失信被执行人", "重大税收违法失信主体", "重大税收违法案件当事人名单",
  "政府采购严重违法失信行为记录名单", "严重违法失信企业名单", "经营异常名录",
  "行贿犯罪记录", "不良行为记录", "黑名单",
]);

function normalizeText(value) {
  return String(value || "")
    .normalize("NFKC")
    .replace(/\u00a0/gu, " ")
    .replace(/(?:\\u3000|u3000)+/giu, " ")
    .replace(/[\t\f\v]+/gu, " ")
    .replace(/ *\n */gu, "\n")
    .replace(/[ ]{2,}/gu, " ")
    .trim();
}

function compactText(value) {
  return normalizeText(value).replace(/\s+/gu, "");
}

function stripItemPrefix(value) {
  return normalizeText(value)
    .replace(/^(?:第?[一二三四五六七八九十百]+[章节条、.]|[（(]?[一二三四五六七八九十\d]+[）)][、.]?|\d+(?:\.\d+){0,3}[、.)]?)[\s、.:：-]*/u, "")
    .trim();
}

function splitQualificationText(sourceText) {
  let source = normalizeText(sourceText);
  if (!source) return [];
  // 部分旧站点把资格要求之后的文件获取、递交和联系方式一并写入了该字段。
  // 从首个明确的后续章节截断，防止把文件售价、CA办理等误存为资格条件。
  const procedureIndex = source.search(/(?:^|\s)(?:[一二三四五六七八九十]+[、.]?\s*)?(?:(?:招标|采购)文件(?:的)?获取|投标文件(?:的)?递交|响应文件(?:的)?递交|监督部门|联系方式)/u);
  if (procedureIndex > 0) source = source.slice(0, procedureIndex).trim();
  const numbered = source.replace(
    /(^|\s+|[；;。.])(?=(?:\d+[、)]|\d+(?:\.\d+){1,3}[、.)]?|[（(][一二三四五六七八九十\d]+[）)])\s*)/gu,
    "$1\n",
  );
  const rough = numbered.split(/[\n；;]+/u);
  const result = [];
  const seen = new Set();
  for (const raw of rough) {
    const clause = stripItemPrefix(raw).replace(/^[，,。；;:：\s]+|[；;。.\s]+$/gu, "").trim();
    if (!clause || /^(?:(?:通用|专项|特定|供应商|投标人|申请人)?(?:资质|资格)(?:要求|条件)?|投标人不得存在下列情形之一|供应商不得存在下列情形之一|本标段|标段|人员要求|不允许|允许)[：:]?$/u.test(clause)) continue;
    if (/^.{0,80}(?:投标人|供应商|申请人)(?:资格(?:和)?能力|资格)(?:要求|条件)[：:]?$/u.test(clause)) continue;
    if (/^(?:各标段要求|(?:第?[一二三四五六七八九十\d]+|不分)标段(?:[^:：]{0,20})?)[：:]?$/u.test(clause)) continue;
    if (/^各标段要求[：:]?\s*(?:第?[一二三四五六七八九十\d]+标段[：:]?\s*)+$/u.test(clause)) continue;
    if (/^(?:投标人|供应商)具有下列情况之一[^:：]*[：:]$|^(?:近三年内)?[^:：]{0,20}(?:严重不良情形|禁止情形)[：:]$/u.test(clause)) continue;
    if (/[：:]$/u.test(clause) && !/(?:要求|条件|须|应|具有|具备|不得|联合体|资质|资格|证书|许可证|业绩|信用|失信)/u.test(clause)) continue;
    if (/^(?:财务要求|业绩要求|其他要求|其它要求|资质要求|信誉要求|项目负责人(?:的)?资格要求|项目负责人要求|项目负责人|其他禁止情形|其他主要人员要求)[：:]?\s*(?:无|不要求|无要求|[/,，])?$/u.test(clause)) continue;
    if (/^(?:投标人|供应商|申请人)?(?:应|须)?依法设立[^:：]{0,30}(?:如下|以下)要求[：:]$|^本次(?:招标|采购)要求(?:投标人|供应商)[^:：]{0,20}(?:如下|以下)条件[：:]$/u.test(clause)) continue;
    if (/(?:文件|标书)售价|招标文件售后不退|采购文件获取|招标文件获取|^(?:技术)?成果补偿$|技术成果(?:的)?补偿|未成交供应商[^；。]{0,30}成果[^；。]{0,12}不予补偿|CA数字证书|客服电话|咨询电话|联系方式\s+招标人|登录[^；。]{0,40}(?:平台|系统)[”"'’]?下载招标文件|交易市场主体库[^；。]{0,30}完成注册|采用资格后审方式/u.test(clause)) continue;
    if (/^(?:提供|须提供|需提供)?\s*合同\s*(?:及|和|、|,|，)?\s*(?:对应)?发票[^；。]{0,80}(?:查验结果|证明)?[）)\]]*$/u.test(clause)) continue;
    if (/^(?:第?[一二三四五六七八九十\d]+标段[：:]?)?\s*指.{2,120}(?:提供|须提供|需提供)合同.{0,100}(?:发票|证明).{0,30}[）)]*$/u.test(clause)) continue;
    if (/^(?:如|若)合同或验收证明[^；。]{0,160}(?:技术参数|技术协议|证明)$/u.test(clause)) continue;
    if (/^(?:要求)?合同额[^；。]{0,120}(?:验收证明|工程量确认单|发票)[^；。]{0,30}[）)]*$/u.test(clause)) continue;
    if (/^(?:不分标段[：:]?)?\s*(?:\d+(?:\.\d+)?\s*)?(?:本次招标要求)?(?:投标人|供应商)?(?:具备以下条件)?[：:]?\s*[,，/]?$/u.test(clause)) continue;
    if (/^近年指[：:].*(?:合同签订|投标截止)/u.test(clause)) continue;
    if (/^发票要求[：:].*(?:发票号码|开票日期|金额)/u.test(clause)) continue;
    if (/^投标申请[,，]?否则投标无效$/u.test(clause)) continue;
    if (!/(?:投标人|供应商|申请人|资格|资质|具备|具有|应|须|不得|证书|许可|业绩|信用|失信|联合体|要求|能力)/u.test(clause)
        && /(?:项目|工程)(?:EPC)?总承包[）)]?$/iu.test(clause)) continue;
    const pieces = clause.length > 900 ? clause.split(/。(?=\S)/u) : [clause];
    for (const piece of pieces) {
      const text = normalizeText(piece).replace(/^[，,。\s]+|[。.\s]+$/gu, "");
      if (text.length < 2 || seen.has(text)) continue;
      seen.add(text);
      result.push(text);
    }
  }
  return result;
}

function normalizeLevel(raw) {
  if (!raw) return null;
  const text = normalizeText(raw).replace(/[等及或以上以下不低于资质]/gu, "");
  if (/^[一二三四壹贰叁肆甲乙丙特]级$/u.test(text)) {
    const key = text.slice(0, -1);
    return LEVEL_MAP[key] || text;
  }
  return LEVEL_MAP[text] || (text.endsWith("级") ? text : null);
}

function compareFromText(text, fallback = "UNSPECIFIED") {
  if (/(?:及|或)?以上|不低于|至少/u.test(text)) return "AT_LEAST";
  if (/(?:及|或)?以下|不高于|至多/u.test(text)) return "AT_MOST";
  return fallback;
}

function chineseNumber(value) {
  if (!value) return null;
  if (/^\d+$/u.test(value)) return Number(value);
  const values = { 一: 1, 二: 2, 三: 3, 四: 4, 五: 5, 六: 6, 七: 7, 八: 8, 九: 9, 十: 10 };
  return values[value] || null;
}

function amountYuan(value, unit) {
  const amount = Number(String(value || "").replace(/,/gu, ""));
  if (!Number.isFinite(amount)) return null;
  if (unit === "亿元") return Math.round(amount * 100000000);
  if (unit === "万元") return Math.round(amount * 10000);
  return Math.round(amount);
}

function keywordList(values) {
  const result = [];
  const seen = new Set();
  for (const value of values.flat()) {
    const text = normalizeText(value);
    if (!text || seen.has(text)) continue;
    seen.add(text);
    result.push(text);
  }
  return result.slice(0, 20);
}

function stableValue(value) {
  if (Array.isArray(value)) return value.map(stableValue);
  if (value && typeof value === "object") {
    return Object.fromEntries(Object.keys(value).sort().map((key) => [key, stableValue(value[key])]));
  }
  return value;
}

function buildContentHash(record) {
  const business = { ...record.structuredData };
  delete business.evidence;
  delete business.extraction;
  const payload = {
    subtype: record.requirementSubtype,
    text: compactText(record.requirementText),
    structuredData: stableValue(business),
  };
  return createHash("sha256").update(JSON.stringify(payload)).digest("hex");
}

function makeRecord(subtype, evidenceText, details, options = {}) {
  if (!REQUIREMENT_SUBTYPES.includes(subtype)) throw new Error(`Unsupported subtype: ${subtype}`);
  const text = normalizeText(evidenceText);
  const defaults = DETAIL_DEFAULTS[subtype];
  const structuredData = {
    schemaVersion: SCHEMA_VERSION,
    subject: options.subject || SUBJECT_BY_SUBTYPE[subtype],
    rule: options.rule || RULE_BY_SUBTYPE[subtype],
    logic: {
      groupId: options.logicGroup || null,
      operator: options.logicOperator || "AND",
    },
    scope: { lotCodes: Array.isArray(options.lotCodes) ? options.lotCodes : [] },
    validAt: options.validAt || "BID_DEADLINE",
    evidence: { quote: text },
    extraction: {
      method: options.method || "RULE",
      model: options.model || null,
      version: options.version || "qualification-v1",
      confidence: options.confidence ?? 0.8,
      batchId: options.batchId || null,
    },
    ...defaults,
    ...details,
  };
  const record = {
    requirementType: "QUALIFICATION",
    requirementSubtype: subtype,
    requirementText: text,
    keywords: keywordList(options.keywords || []),
    structuredData,
    isMandatory: options.isMandatory ?? !/(?:可不|非必须|可选择|如有)/u.test(text),
    verificationStatus: options.verificationStatus || "UNVERIFIED",
    effectiveStatus: "ACTIVE",
  };
  record.contentHash = buildContentHash(record);
  return record;
}

function uniquePush(records, record) {
  const key = `${record.requirementSubtype}\u0000${record.contentHash}`;
  if (!records.some((item) => `${item.requirementSubtype}\u0000${item.contentHash}` === key)) {
    records.push(record);
  }
}

function qualificationName(raw) {
  let text = normalizeText(raw)
    .replace(/^.*?(?:具备|具有|取得|应具备|须具备|需具备|颁发的|颁布的)/u, "")
    .replace(/^.*?(?:投标人|供应商)(?:应|须)(?:同时)?(?:具备|具有)?/u, "")
    .replace(/^(?:建设行政主管部门颁发的|住房和城乡建设主管部门颁发的|联合体牵头人必须为|持有有效的|综合资质或)+/u, "")
    .replace(/^[、，,和及或]+/u, "")
    .replace(/^(?:有效的?|相应的?)/u, "")
    .replace(/[一二三四壹贰叁肆甲乙丙特]级.*$/u, "")
    .replace(/资质$/u, "")
    .trim();
  // 长句会连续列出多个“甲资质或乙资质”。当前正则可能从上一个资质
  // 的尾部开始捕获；只有当连接词前出现等级/资质时才取其后的完整项，
  // 避免误拆“城市及道路照明工程”这类合法专业名称。
  text = text.replace(
    /^.*?(?:[一二三四壹贰叁肆甲乙丙特]级(?:及以上)?(?:资质)?|综合资质|资质)(?:或|和|及)(?=(?:工程|市政|建筑|水利|电力|电子|消防|环保|矿山|机电|石油|化工|公路|铁路|通信|钢结构|地基|防水|古建筑|建筑幕墙|测绘|城乡|土地))/u,
    "",
  );
  for (const separator of ["中的", "颁发的", "核发的", "资质要求:", "资质要求：", "同时具备:", "同时具备：", "、", "，", ","]) {
    const index = text.lastIndexOf(separator);
    if (index >= 0) {
      const candidate = text.slice(index + separator.length).trim();
      if (/(?:施工总承包|专业承包|工程设计|工程监理|工程勘察|勘察设计|设计|监理|施工|测绘|规划|咨询)/u.test(candidate)) {
        text = candidate;
      }
    }
  }
  return text
    .replace(/^[\d\s:：.、，,（()）)]+/u, "")
    .replace(/^国家建设部颁布的/u, "")
    .replace(/^专业类(?=[（(])/u, "工程勘察专业类")
    .replace(/^建(?=水利水电工程施工总承包)/u, "")
    .replace(/建筑工程工程施工总承包/u, "建筑工程施工总承包")
    .trim();
}

function plausibleQualificationName(name) {
  const text = normalizeText(name);
  const leftParens = (text.match(/[（(]/gu) || []).length;
  const rightParens = (text.match(/[）)]/gu) || []).length;
  return Boolean(
    text
    && text.length >= 2
    && text.length <= 48
    && leftParens === rightParens
    && !/^(?:综合|综合类|施工|设计|承包|施工总承包|专业类|相关专业)$/u.test(text)
    && !/[A-Z]\d|\s/u.test(text)
    && !/(?:https?:|www\.|\.gov\.cn|\.mot\.gov|\/|业绩|合同|发票|类似|承担过|独立承担|项目名称|项目.*(?:工程设计|工程监理|施工)|供应商须|投标人须|第一标段|第二标段|第三标段|资质要求|满足下列|中的|颁发的|颁布的|核发的|许可证|及以上设计|生态修复或|^承修类$)/iu.test(text)
    && !/^[\d:：.、，,（()）)]/u.test(text),
  );
}

function extractCompanyQualifications(clause, options) {
  const result = [];
  const pattern = /((?:[^，,。；;]{0,30}?)(?:施工总承包|专业承包|工程设计(?:专项)?|工程监理|工程勘察|勘察设计|设计资质|监理资质|施工资质))\s*([一二三四壹贰叁肆甲乙丙特]级)?\s*((?:及|或)?以上|不低于)?(?:资质)?/gu;
  for (const match of clause.matchAll(pattern)) {
    const evidence = normalizeText(match[0]);
    if (/(?:https?:\/\/|www\.|承担过|已经完成)/iu.test(evidence)) continue;
    const name = qualificationName(match[1]);
    if (!plausibleQualificationName(name) || /^(?:以上|中规定|相关的)/u.test(name)) continue;
    const level = normalizeLevel(match[2]);
    uniquePush(result, makeRecord("COMPANY_QUALIFICATION", evidence, {
      name,
      profession: name.replace(/(?:施工总承包|专业承包|工程设计专项|工程设计|工程监理|工程勘察|勘察设计|设计资质|监理资质|施工资质)$/u, "") || null,
      level,
      compare: compareFromText(`${match[2] || ""}${match[3] || ""}`, level ? "EXACT" : "EXISTS"),
    }, { ...options, keywords: [name, level, match[3]] }));
  }
  const genericPattern = /((?:土地规划|测绘|地质灾害(?:评估|治理|勘查|设计|监理)?|城乡规划|工程咨询|涉密信息系统集成))\s*([一二三四壹贰叁肆甲乙丙特]级)\s*((?:及|或)?以上|不低于)?(?:资质)?/gu;
  for (const match of clause.matchAll(genericPattern)) {
    const evidence = normalizeText(match[0]);
    const name = qualificationName(match[1]);
    if (!plausibleQualificationName(name)) continue;
    const level = normalizeLevel(match[2]);
    uniquePush(result, makeRecord("COMPANY_QUALIFICATION", evidence, {
      name,
      profession: name,
      level,
      compare: compareFromText(`${match[2] || ""}${match[3] || ""}`, "EXACT"),
    }, { ...options, keywords: [name, level, match[3]] }));
  }
  for (const match of clause.matchAll(/((?:城乡规划编制单位|地质灾害防治单位(?:危险性评估)?))[^；。]{0,16}?([一二三四壹贰叁肆甲乙丙特]级)(?:及以上|或以上)?(?:资质|资质证书)?/gu)) {
    const name = qualificationName(match[1]);
    if (!plausibleQualificationName(name)) continue;
    const level = normalizeLevel(match[2]);
    uniquePush(result, makeRecord("COMPANY_QUALIFICATION", match[0], {
      name,
      profession: name,
      level,
      compare: compareFromText(match[0], "EXACT"),
    }, { ...options, confidence: 0.9, keywords: [name, level] }));
  }
  for (const match of clause.matchAll(/地质灾害防治单位资质证书[（(]([^）)]{2,40}?)(?:资质)?([一二三四壹贰叁肆甲乙丙特]级)(?:及以上|或以上)?[）)]/gu)) {
    const name = qualificationName(match[1]);
    if (!plausibleQualificationName(name)) continue;
    const level = normalizeLevel(match[2]);
    uniquePush(result, makeRecord("COMPANY_QUALIFICATION", match[0], {
      name,
      profession: name,
      level,
      compare: compareFromText(match[0], "EXACT"),
    }, { ...options, confidence: 0.92, keywords: [name, level] }));
  }
  return result;
}

function extractLicenses(clause, options) {
  const result = [];
  for (const name of LICENSE_PATTERNS) {
    if (!clause.includes(name)) continue;
    uniquePush(result, makeRecord("COMPANY_LICENSE", name, { name }, {
      ...options,
      confidence: 0.98,
      keywords: [name, clause.includes("有效") ? "有效" : null],
    }));
  }
  for (const match of clause.matchAll(/([\u4e00-\u9fa5A-Za-z0-9（）()IVX-]{2,36}(?:许可证|核准证))/gu)) {
    const evidence = normalizeText(match[0]);
    if (LICENSE_PATTERNS.some((knownName) => evidence.includes(knownName))) continue;
    if (/(?:暂扣|吊销|撤销)[^；。]{0,8}(?:许可证|核准证)/u.test(evidence)) continue;
    let name = evidence
      .replace(/^(?:投标人|供应商|企业)?(?:须|应)?(?:具有|具备|持有|取得)?(?:相应|合格|有效|的)*/u, "")
      .replace(/^[一二三四壹贰叁肆]级(?:及以上)?/u, "")
      .trim();
    if (name.includes("的") && name.length > 18) name = name.slice(name.lastIndexOf("的") + 1);
    name = name.replace(/^(?:并|且|或|提供|需提供|制造商|生产厂家)+/u, "").trim();
    const canonicalLicensePatterns = [
      "排污许可证", "危险废物经营许可证", "采矿许可证", "保安服务许可证", "道路危险货物运输许可证",
      "基础电信业务经营许可证", "保险许可证", "律师事务所执业许可证", "会计师事务所执业许可证",
      "药品生产许可证", "药品经营许可证", "出版物发行许可证", "出版物经营许可证", "取水许可证",
      "压力管道安装许可证",
    ];
    const canonical = canonicalLicensePatterns.find((candidate) => evidence.includes(candidate));
    if (canonical) name = canonical;
    if (/安[会全]生产许可证/u.test(evidence)) name = "安全生产许可证";
    if (/电力设施许可证/u.test(evidence)) {
      const categories = ["承装", "承修", "承试"].filter((item) => clause.includes(item));
      name = categories.length === 1 ? `${categories[0]}电力设施许可证` : "承装（修、试）电力设施许可证";
    }
    if (!name
        || name.length > 40
        || /(?:开户许可证|基本账户|合同|发票|履行起始日期|建设用地规划许可证|建设工程规划许可证|施工许可证)/u.test(name)) continue;
    name = name.replace(/^\d+(?:[.、])?/u, "").replace(/^(?:须具有|具有|合法有效的|有效的|公安机关颁发的)+/u, "").trim();
    if (!name || /^(?:第一|第二|第三|第四|第五|货物制造商|投标人|供应商)/u.test(name)) continue;
    const levelRaw = clause.slice(Math.max(0, match.index - 12), match.index + match[0].length).match(/([一二三四壹贰叁肆]级)(?:及以上|或以上)?/u)?.[1] || null;
    const level = normalizeLevel(levelRaw);
    uniquePush(result, makeRecord("COMPANY_LICENSE", evidence, {
      name,
      level,
      compare: compareFromText(clause.slice(Math.max(0, match.index - 12), match.index + match[0].length + 8), level ? "EXACT" : "EXISTS"),
    }, { ...options, confidence: 0.82, keywords: [name, level] }));
  }
  if (/危险化学品道路运输许可相关证件/u.test(clause)
      && !result.some((item) => item.structuredData.name === "危险化学品道路运输许可相关证件")) {
    uniquePush(result, makeRecord("COMPANY_LICENSE", clause, {
      name: "危险化学品道路运输许可相关证件",
      permittedScope: "危险化学品道路运输",
    }, { ...options, confidence: 0.92, keywords: ["危险化学品道路运输", "许可相关证件"] }));
  }
  return result;
}

function extractCertifications(clause, options) {
  const result = [];
  for (const [name, standardCode] of CERTIFICATION_PATTERNS) {
    if (!clause.includes(name) && !clause.toUpperCase().includes(standardCode.replace(/\s/gu, ""))) continue;
    const evidence = clause.includes(name) ? name : standardCode;
    uniquePush(result, makeRecord("COMPANY_CERTIFICATION", evidence, { name, standardCode }, {
      ...options,
      confidence: 0.96,
      keywords: [name, standardCode],
    }));
  }
  if (/管理体系认证/u.test(clause) && result.length === 0) {
    uniquePush(result, makeRecord("COMPANY_CERTIFICATION", clause, {
      name: "管理体系认证",
    }, { ...options, confidence: 0.72, keywords: ["管理体系认证"] }));
  }
  for (const match of clause.matchAll(/(?:CMMI|TMMI|ITSS)\s*(?:[（(][^）)]{2,40}[）)])?\s*([1-5一二三四五]级?)?(?:及以上|或以上)?(?:认证)?(?:资质证书|证书|资质)?/giu)) {
    const token = match[0];
    const standardCode = match[0].match(/CMMI|TMMI|ITSS/iu)?.[0].toUpperCase() || null;
    const names = {
      CMMI: "软件能力成熟度集成模型认证",
      TMMI: "测试成熟度模型集成认证",
      ITSS: "信息技术服务标准认证",
    };
    const levelRaw = match[1] || null;
    const level = levelRaw ? (/^\d/u.test(levelRaw) ? `${levelRaw.replace(/级$/u, "")}级` : normalizeLevel(levelRaw)) : null;
    uniquePush(result, makeRecord("COMPANY_CERTIFICATION", token, {
      name: names[standardCode] || standardCode,
      standardCode,
      level,
      compare: compareFromText(token, level ? "EXACT" : "EXISTS"),
    }, { ...options, confidence: 0.93, keywords: [standardCode, level] }));
  }
  if (/(?:检验检测机构资质认定证书|CMA(?:与|和|、)CNAS|CMA认证)/iu.test(clause)) {
    const evidence = clause.match(/检验检测机构资质认定证书(?:[（(]CMA[）)])?|CMA(?:与|和|、)CNAS|CMA认证/iu)?.[0] || "CMA";
    uniquePush(result, makeRecord("COMPANY_CERTIFICATION", evidence, {
      name: "检验检测机构资质认定",
      standardCode: "CMA",
      compare: "EXISTS",
    }, { ...options, confidence: 0.94, keywords: ["CMA", "检验检测机构资质认定"] }));
  }
  for (const [pattern, name, standardCode] of [
    [/特种设备型式试验证书/u, "特种设备型式试验证书", null],
    [/法定计量检定机构计量授权证书/u, "法定计量检定机构计量授权证书", null],
    [/泰尔认证(?:官网查询结果截图)?/u, "泰尔认证", "TLC"],
  ]) {
    const match = clause.match(pattern);
    if (!match) continue;
    uniquePush(result, makeRecord("COMPANY_CERTIFICATION", clause, {
      name,
      standardCode,
      certificationScope: clause.match(/[（(]([^）)]{2,80})[）)]/u)?.[1] || null,
    }, { ...options, confidence: 0.9, keywords: [name, standardCode] }));
  }
  return result;
}

function extractPersonnel(clause, options) {
  const result = [];
  const roleMatch = clause.match(/项目经理|项目副经理|项目负责人|勘察负责人|设计负责人|施工负责人|技术负责人|总监理工程师|监理工程师|专职安全管理员|专职安全员|安全员|作业人员/u);
  const role = roleMatch?.[0] || null;
  const builders = clause.match(/(?:注册)?建造师|注册监理工程师|注册建筑师|注册结构工程师|注册造价工程师|造价工程师/u);
  if (builders) {
    const major = clause.match(/([\u4e00-\u9fa5]{2,12})专业\s*(?:[一二三四壹贰叁肆]级)?(?:及以上|或以上)?\s*(?:注册)?(?:建造师|监理工程师|建筑师|结构工程师|造价工程师)/u)?.[1] || null;
    const levelRaw = clause.match(/([一二三四壹贰叁肆]级)\s*(?:及以上|或以上)?\s*(?:注册)?(?:建造师|监理工程师|建筑师|结构工程师|造价工程师)/u)?.[1] || null;
    const level = normalizeLevel(levelRaw);
    uniquePush(result, makeRecord("PERSONNEL_CERTIFICATE", clause, {
      role,
      certificateName: builders[0].replace(/^注册/u, "注册"),
      major,
      level,
      compare: compareFromText(clause, level ? "EXACT" : "EXISTS"),
      registrationUnitRequired: /本单位|投标人单位执业|注册在/u.test(clause) ? true : null,
      socialSecurityMonths: chineseNumber(clause.match(/(?:连续)?(?:缴纳)?([一二三四五六七八九十\d]+)个?月(?:的)?社保/u)?.[1]),
      noConcurrentProject: /无其他在建|不得.*在建|未担任其他在建/u.test(clause) ? true : null,
    }, { ...options, keywords: [role, builders[0], major, level] }));
  }
  if (/(?:安全生产(?:知识和管理能力)?考核(?:合格)?证(?:书)?|安全[ABC]证|安考[ABC]证)/iu.test(clause)) {
    const certificateEvidence = clause.match(/安全生产(?:知识和管理能力)?考核(?:合格)?证(?:书)?(?:\s*[（(]?[ABC]证?[）)]?)?|安全[ABC]证|安考[ABC]证/iu)?.[0] || "安全生产考核合格证书";
    const certificateLevel = certificateEvidence.match(/[ABC](?=证|[）)])/iu)?.[0]?.toUpperCase() || null;
    const certificateName = "安全生产考核合格证书";
    uniquePush(result, makeRecord("PERSONNEL_CERTIFICATE", certificateEvidence, {
      role,
      certificateName,
      level: certificateLevel ? `${certificateLevel}证` : null,
      compare: "EXISTS",
      registrationUnitRequired: /本单位|投标人单位/u.test(clause) ? true : null,
      noConcurrentProject: /无其他在建|不得.*在建|未担任其他在建/u.test(clause) ? true : null,
    }, { ...options, confidence: 0.92, keywords: [role, certificateName, certificateLevel] }));
  }
  if (/职称/u.test(clause)) {
    const titleLevel = clause.match(/(正高级|副高级|高级|中级|初级)(?:及以上|或以上)?职称/u)?.[1] || null;
    uniquePush(result, makeRecord("PERSONNEL_CERTIFICATE", clause, {
      role,
      certificateName: "专业技术职称",
      compare: compareFromText(clause, titleLevel ? "EXACT" : "EXISTS"),
      titleName: clause.match(/([\u4e00-\u9fa5]{2,12})专业[^；。]{0,8}职称/u)?.[1] || null,
      titleLevel,
    }, { ...options, keywords: [role, titleLevel, "职称"] }));
  }
  if (result.length === 0 && /(?:资格证书|执业证书|监理员证书|注册消防工程师|工程监督资格证书|安全评价师|作业证|检验检测人员证书)/u.test(clause)) {
    let certificateName = clause.match(/(?:[\u4e00-\u9fa5]{2,18}(?:资格证书|执业证书|作业证|检验检测人员证书))|(?:[一二]级)?注册消防工程师|(?:[一二三四]级)?安全评价师|监理员证书|工程监督资格证书/u)?.[0] || "人员资格证书";
    for (const preferred of ["安全管理资格证书", "工程监督资格证书", "检验检测人员证书", "监理员证书", "焊接与热切割作业证", "低压电工作业证", "起重机特种设备作业证", "特种作业证"]) {
      if (certificateName.includes(preferred)) certificateName = preferred;
    }
    if (/安全资格证书/u.test(certificateName)) certificateName = "安全管理资格证书";
    certificateName = certificateName
      .replace(/^(?:拟派项目负责人具有|提供|须提供|需提供|持有|投标人现场施工特殊工种须持有)/u, "")
      .trim();
    if (/(?:会计师事务所执业证书|提供所有人员相应资格证书|特种作业人员应具有相应资格证书)/u.test(clause)) return result;
    if (/^(?:人员资格证书|注册执业证书|运行工等相关专业资格证书|相关专业资格证书)$/u.test(certificateName)) return result;
    uniquePush(result, makeRecord("PERSONNEL_CERTIFICATE", clause, {
      role,
      certificateName,
      compare: "EXISTS",
      registrationUnitRequired: /本单位|工作单位须与本单位一致|注册在/u.test(clause) ? true : null,
      socialSecurityMonths: chineseNumber(clause.match(/连续\s*([一二三四五六七八九十\d]+)\s*个?月[^；。]{0,12}(?:社保|养老保险)/u)?.[1]),
      noConcurrentProject: /无其他在建|不得.*在建|未担任其他在建/u.test(clause) ? true : null,
    }, { ...options, confidence: 0.78, keywords: [role, certificateName] }));
  }
  for (const match of clause.matchAll(/注册(?:土木工程师|电气工程师|公用设备工程师|化工工程师|环保工程师)(?:[（(][^）)]{1,24}[）)])?/gu)) {
    uniquePush(result, makeRecord("PERSONNEL_CERTIFICATE", clause, {
      role,
      certificateName: match[0],
      compare: "EXISTS",
      registrationUnitRequired: /本单位|注册在/u.test(clause) ? true : null,
    }, { ...options, confidence: 0.94, keywords: [role, match[0]] }));
  }
  for (const match of clause.matchAll(/(?:低压电工|高压电工|焊工|移动式压力容器充装|道路运输人员从业资格|B2及以上驾驶)(?:证|操作证|驾驶证|从业资格证)?/gu)) {
    const certificateName = /证/u.test(match[0]) ? match[0] : `${match[0]}证`;
    uniquePush(result, makeRecord("PERSONNEL_CERTIFICATE", clause, {
      role,
      certificateName,
      compare: "EXISTS",
      minimumCount: chineseNumber(clause.slice(Math.max(0, match.index - 12), match.index).match(/(?:至少|不少于)?\s*([一二三四五六七八九十\d]+)\s*名?$/u)?.[1]) || 1,
      registrationUnitRequired: /本单位|社保|养老保险|工伤保险/u.test(clause) ? true : null,
      socialSecurityMonths: chineseNumber(clause.match(/连续\s*([一二三四五六七八九十\d]+)\s*个?月[^；。]{0,20}(?:社保|养老保险)/u)?.[1]),
    }, { ...options, confidence: 0.88, keywords: [role, certificateName] }));
  }
  return result;
}

function extractPerformance(clause, options) {
  if (/^业绩要求[：:]?\s*(?:无|不要求|无要求|[/,，])?$/u.test(clause)) return [];
  if (!/业绩/u.test(clause)
      && !/(?:近\s*[一二三四五六七八九十\d]+\s*年|\d{4}年\d{1,2}月\d{1,2}日)[^；。]{0,100}(?:至少|不少于)[^；。]{0,20}(?:同类|类似)(?:产品|货物|设备|服务|项目|工程)/u.test(clause)) return [];
  const withinYears = chineseNumber(clause.match(/近\s*([一二三四五六七八九十\d]+)\s*年/u)?.[1]);
  const minimumCount = chineseNumber(clause.match(/(?:至少|不少于|具有)\s*([一二三四五六七八九十\d]+)\s*(?:个|项)\s*[^；。]*?(?:业绩|同类|类似)/u)?.[1]) || 1;
  const amountMatch = clause.match(/(?:单项|合同)?[^；。]{0,12}(?:金额|造价)[^\d]{0,5}([\d,.]+)\s*(亿元|万元|元)/u);
  const category = clause.match(/(?:具有|完成|承担|提供)[^；。]{0,8}([^，,；。]{2,32}?)(?:类似|同类)(?:项目|工程|服务|供货)?业绩/u)?.[1]
    || clause.match(/([^，,；。]{2,32}?)(?:类似|同类)(?:项目|工程|服务|供货)?业绩/u)?.[1]
    || null;
  return [makeRecord("PERFORMANCE", clause, {
    projectCategory: normalizeText(category) || null,
    projectKeywords: keywordList([category]),
    withinYears,
    minimumCount,
    minimumSingleAmount: amountMatch ? amountYuan(amountMatch[1], amountMatch[2]) : null,
    completionRequired: /已完成|竣工|验收/u.test(clause) ? true : null,
    contractRequired: /提供[^；。]{0,12}合同|合同扫描件/u.test(clause) ? true : null,
  }, { ...options, keywords: [category, withinYears ? `近${withinYears}年` : null, `${minimumCount}项`, "业绩"] })];
}

function extractCredit(clause, options) {
  if (!/(?:信用|失信|违法|行贿|黑名单|不合格供方|限制准入|经营异常|不良行为|破产|清算|停产停业|吊销|取消投标|禁止参与采购|合同纠纷|诉讼|仲裁|行政处罚|重大安全|重大质量|骗取中标|严重违约)/u.test(clause)) return [];
  const platforms = CREDIT_PLATFORMS.filter((item) => clause.includes(item));
  const lists = CREDIT_LISTS.filter((item) => clause.includes(item));
  // 公告经常在一个长句中同时列出多个平台和多个限制名单，但原文通常
  // 没有说明每个平台与每个名单的一一对应关系。禁止做笛卡尔组合，避免
  // 生成原文不存在的“平台-名单”事实；保留整个原句作为唯一证据。
  const inferredRestriction = lists.length ? lists.join("、")
    : clause.match(/(?:进入清算程序|被宣告破产|责令停产停业|吊销[^，,；。]*|取消投标资格|禁止参与采购活动|重大合同纠纷|诉讼、?仲裁、?行政处罚记录|重大安全、?质量事故|骗取中标|严重违约)/u)?.[0] || null;
  return [makeRecord("CREDIT", clause, {
    platform: platforms.length ? platforms.join("、") : null,
    restrictedList: inferredRestriction,
    condition: /未被|不得|没有|无/u.test(clause) ? "NOT_LISTED" : "NO_RECORD",
    withinYears: chineseNumber(clause.match(/近\s*([一二三四五六七八九十\d]+)\s*年/u)?.[1]),
  }, {
    ...options,
    confidence: platforms.length || lists.length ? 0.9 : 0.65,
    keywords: [...platforms, ...lists],
  })];
}

function extractBasicConditions(clause, options) {
  const result = [];
  for (const [conditionCode, name, pattern, documentType] of BASIC_PATTERNS) {
    if (conditionCode === "BID_LOT_RESTRICTION" && /(?:单位负责人为同一人|控股|管理关系|关联企业|利害关系)/u.test(clause)) continue;
    if (conditionCode === "EQUIPMENT" && /人员、?设备、?资金等方面/u.test(clause)) continue;
    const match = clause.match(pattern);
    if (!match) continue;
    const evidence = conditionCode === "PERSONNEL_EMPLOYMENT" ? clause : match[0];
    uniquePush(result, makeRecord("BASIC_CONDITION", evidence, {
      conditionCode,
      name,
      documentType,
    }, { ...options, confidence: 0.94, keywords: [name, documentType] }));
  }
  return result;
}

function extractConsortium(clause, options) {
  if (!/联合体/u.test(clause)) return [];
  const prohibited = /不接受|不允许|不得|禁止/u.test(clause);
  const allowed = prohibited ? false : /接受|允许|可以/u.test(clause) ? true : null;
  return [makeRecord("CONSORTIUM", clause, {
    allowed,
    maximumMembers: chineseNumber(clause.match(/(?:不超过|最多)\s*([一二三四五六七八九十\d]+)\s*家/u)?.[1]),
    leaderRequired: /牵头人/u.test(clause) ? true : null,
    agreementRequired: /联合体协议/u.test(clause) ? true : null,
    jointLiabilityRequired: /连带责任/u.test(clause) ? true : null,
  }, { ...options, confidence: allowed === null ? 0.65 : 0.96, keywords: ["联合体", allowed === false ? "不接受" : "允许"] })];
}

function extractClause(clause, options = {}) {
  const records = [];
  for (const extractor of [
    extractCompanyQualifications,
    extractLicenses,
    extractCertifications,
    extractPersonnel,
    extractPerformance,
    extractCredit,
    extractBasicConditions,
    extractConsortium,
  ]) {
    for (const record of extractor(clause, options)) uniquePush(records, record);
  }
  if (records.length === 0) {
    records.push(makeRecord("OTHER", clause, {
      description: clause,
      reviewReason: "规则未能确定可计算的资格要求类型",
    }, { ...options, confidence: 0.3, keywords: [] }));
  }
  return records;
}

function extractProjectRequirements(sourceText, options = {}) {
  const source = normalizeText(sourceText);
  const clauses = splitQualificationText(source);
  const records = [];
  for (const clause of clauses) {
    for (const record of extractClause(clause, options)) uniquePush(records, record);
  }
  return { sourceText: source, clauses, records };
}

function validateProjectRequirement(record, sourceText) {
  const errors = [];
  if (record.requirementType !== "QUALIFICATION") errors.push("requirementType must be QUALIFICATION");
  if (!REQUIREMENT_SUBTYPES.includes(record.requirementSubtype)) errors.push("unsupported requirementSubtype");
  if (!record.requirementText) errors.push("requirementText is required");
  if (!compactText(sourceText).includes(compactText(record.requirementText))) errors.push("requirementText is not grounded in source text");
  if (!Array.isArray(record.keywords) || record.keywords.some((item) => typeof item !== "string" || !item)) {
    errors.push("keywords must be an array of non-empty strings");
  }
  if (typeof record.isMandatory !== "boolean") errors.push("isMandatory must be boolean");
  if (!VERIFICATION_STATUS_VALUES.has(record.verificationStatus)) errors.push("invalid verificationStatus");
  if (!EFFECTIVE_STATUS_VALUES.has(record.effectiveStatus)) errors.push("invalid effectiveStatus");
  if (!/^[a-f0-9]{64}$/u.test(record.contentHash || "")) errors.push("contentHash must be sha256");
  const data = record.structuredData;
  if (!data || typeof data !== "object" || Array.isArray(data)) errors.push("structuredData must be an object");
  else {
    if (data.schemaVersion !== SCHEMA_VERSION) errors.push("unsupported schemaVersion");
    if (typeof data.subject !== "string" || !data.subject) errors.push("subject is required");
    if (typeof data.rule !== "string" || !data.rule) errors.push("rule is required");
    if (!VALID_AT_VALUES.has(data.validAt)) errors.push("invalid validAt");
    if (!data.evidence || data.evidence.quote !== record.requirementText) errors.push("evidence.quote must equal requirementText");
    if (!data.logic || !["AND", "OR"].includes(data.logic.operator)) errors.push("invalid logic.operator");
    else if (data.logic.groupId !== null && typeof data.logic.groupId !== "string") errors.push("invalid logic.groupId");
    if (!data.scope || !Array.isArray(data.scope.lotCodes)
        || data.scope.lotCodes.some((item) => typeof item !== "string")) errors.push("scope.lotCodes must be a string array");
    if (!data.extraction || typeof data.extraction.confidence !== "number" || data.extraction.confidence < 0 || data.extraction.confidence > 1) {
      errors.push("invalid extraction confidence");
    } else {
      if (!EXTRACTION_METHOD_VALUES.has(data.extraction.method)) errors.push("invalid extraction method");
      if (typeof data.extraction.version !== "string" || !data.extraction.version) errors.push("invalid extraction version");
      if (data.extraction.model !== null && typeof data.extraction.model !== "string") errors.push("invalid extraction model");
      if (data.extraction.batchId !== null && typeof data.extraction.batchId !== "string") errors.push("invalid extraction batchId");
    }
    const expected = Object.keys(DETAIL_DEFAULTS[record.requirementSubtype] || {}).sort();
    const common = new Set(["schemaVersion", "subject", "rule", "logic", "scope", "validAt", "evidence", "extraction"]);
    const actual = Object.keys(data).filter((key) => !common.has(key)).sort();
    if (JSON.stringify(expected) !== JSON.stringify(actual)) errors.push("type-specific fields do not match V1 contract");
    if (Object.hasOwn(data, "compare") && !COMPARE_VALUES.has(data.compare)) errors.push("invalid compare");
    if (record.requirementSubtype === "BASIC_CONDITION" && !BASIC_CONDITION_CODE_VALUES.has(data.conditionCode)) {
      errors.push("invalid BASIC_CONDITION conditionCode");
    }
    if (record.requirementSubtype === "PERFORMANCE") {
      if (!["COMPANY", "PERSONNEL"].includes(data.performanceOwner)) errors.push("invalid performanceOwner");
      if (!Array.isArray(data.projectKeywords) || data.projectKeywords.some((item) => typeof item !== "string")) {
        errors.push("projectKeywords must be a string array");
      }
    }
    if (record.requirementSubtype === "CREDIT") {
      if (!["COMPANY", "PERSONNEL"].includes(data.creditSubject)) errors.push("invalid creditSubject");
      if (!["NOT_LISTED", "NO_RECORD", "NO_MAJOR_VIOLATION", "SATISFIES_LEVEL", "OTHER"].includes(data.condition)) {
        errors.push("invalid credit condition");
      }
    }
    if (record.requirementSubtype === "CONSORTIUM") {
      if (!Array.isArray(data.memberRoles) || data.memberRoles.some((item) => typeof item !== "string")) {
        errors.push("memberRoles must be a string array");
      }
      if (!Array.isArray(data.memberRequirementTexts)
          || data.memberRequirementTexts.some((item) => typeof item !== "string")) {
        errors.push("memberRequirementTexts must be a string array");
      }
    }
  }
  return errors;
}

module.exports = {
  DETAIL_DEFAULTS,
  REQUIREMENT_SUBTYPES,
  SCHEMA_VERSION,
  buildContentHash,
  compareFromText,
  extractClause,
  extractProjectRequirements,
  makeRecord,
  normalizeLevel,
  normalizeText,
  splitQualificationText,
  validateProjectRequirement,
};
