"use strict";

const assert = require("node:assert/strict");
const test = require("node:test");
const {
  extractProjectRequirements,
  compareFromText,
  normalizeLevel,
  splitQualificationText,
  validateProjectRequirement,
} = require("../lib/project_requirements");

test("normalizes AI-style levels and comparison phrases", () => {
  assert.equal(normalizeLevel("D级及以上"), "D级");
  assert.equal(normalizeLevel("贰级及以上"), "二级");
  assert.equal(compareFromText("固定式压力容器D级及以上", "UNSPECIFIED"), "AT_LEAST");
});

test("splits numbered requirements and truncates later procurement sections", () => {
  const text = "1、具有独立法人资格。 2、具备有效营业执照。 3、近三年具有类似业绩。 四、招标文件获取 1、文件售价500元";
  const clauses = splitQualificationText(text);
  assert.deepEqual(clauses, ["具有独立法人资格", "具备有效营业执照", "近三年具有类似业绩"]);
});

test("keeps requirements before supervision and contact sections", () => {
  const text = "2、投标人须具备有效期内的TMMi3(含)以上认证资质证书。 二、监督部门 本项目监督部门为采购人。 三、联系方式 招标人:测试单位";
  const result = extractProjectRequirements(text, { batchId: "test" });
  assert.ok(result.records.some(
    (record) => record.requirementSubtype === "COMPANY_CERTIFICATION"
      && record.structuredData.standardCode === "TMMI",
  ));
  assert.equal(result.records.some((record) => record.requirementSubtype === "OTHER"), false);
});

test("drops headings and standalone supporting-document fragments", () => {
  const text = [
    "投标人资格和能力要求:",
    "各标段要求: 第一标段:",
    "提供合同、对应发票及发票在国家税务总局网站的有效查验结果)",
    "自产设备仅需提供标明生产单位的出厂合格证",
  ].join("；");
  const clauses = splitQualificationText(text);
  assert.deepEqual(clauses, ["自产设备仅需提供标明生产单位的出厂合格证"]);
  const result = extractProjectRequirements(text, { batchId: "test" });
  const certification = result.records.find(
    (record) => record.requirementSubtype === "BASIC_CONDITION"
      && record.structuredData.conditionCode === "PRODUCT_CERTIFICATION",
  );
  assert.ok(certification);
  assert.equal(result.records.some((record) => record.requirementSubtype === "OTHER"), false);
});

test("drops detached performance proof fragments and bare compensation text", () => {
  const text = [
    "成果补偿",
    "提供合同及对应发票)",
    "第二标段:指潜油电泵维修)。(提供合同、发票以及发票查验结果)",
    "要求合同额(1000万元及以上)需通过验收证明或工程量确认单或发票进行证明)",
    "投标人须具有有效营业执照",
  ].join("；");
  const clauses = splitQualificationText(text);
  assert.deepEqual(clauses, ["投标人须具有有效营业执照"]);
});

test("keeps only complete qualification names from chained qualifications", () => {
  const text = [
    "投标人须具备工程设计综合甲级资质或工程设计电力行业乙级资质",
    "投标人须具备建筑装修装饰工程专业承包二级及以上和电子与智能化工程专业承包一级资质",
    "第一标段供应商须具备电力工程施工总承包二级及以上资质",
  ].join("；");
  const result = extractProjectRequirements(text, { batchId: "test" });
  const names = result.records
    .filter((record) => record.requirementSubtype === "COMPANY_QUALIFICATION")
    .map((record) => record.structuredData.name);
  assert.equal(names.some((name) => ["综合", "施工", "设计", "承包"].includes(name)), false);
  assert.ok(names.includes("工程设计"));
  assert.ok(names.includes("电子与智能化工程专业承包"));
  assert.ok(names.includes("电力工程施工总承包"));
});

test("normalizes personnel certificate names and leaves vague certificates for review", () => {
  const concrete = extractProjectRequirements(
    "拟派项目负责人具有安全资格证书",
    { batchId: "test" },
  );
  assert.equal(
    concrete.records.find((record) => record.requirementSubtype === "PERSONNEL_CERTIFICATE")?.structuredData.certificateName,
    "安全管理资格证书",
  );
  const vague = extractProjectRequirements("运行工等相关专业资格证书", { batchId: "test" });
  assert.deepEqual(vague.records.map((record) => record.requirementSubtype), ["OTHER"]);
});

test("drops punctuated empty requirements and procurement procedure text", () => {
  const text = [
    "财务要求:无。",
    "其他要求:无。",
    "技术成果的补偿。",
    "本次采购对未成交供应商的技术成果不予补偿。",
    "获取方法:登录‘玖邦招标采购电子交易平台’下载招标文件。",
    "具有良好的商业信誉和健全的财务会计制度。",
  ].join("；");
  const clauses = splitQualificationText(text);
  assert.deepEqual(clauses, ["具有良好的商业信誉和健全的财务会计制度"]);
  const result = extractProjectRequirements(text, { batchId: "test" });
  assert.ok(result.records.some(
    (record) => record.requirementSubtype === "BASIC_CONDITION"
      && record.structuredData.conditionCode === "FINANCIAL_STATUS",
  ));
  assert.equal(result.records.some((record) => record.requirementSubtype === "OTHER"), false);
});

test("drops empty performance headings and project-title contamination", () => {
  const text = [
    "业绩要求:无",
    "业绩要求:",
    "近年指:2023年1月1日至投标截止时间(以合同签订时间为准)",
    "阜新高新区100MW飞轮、电化学混合储能独立调频电站项目EPC总承包",
    "近三年具有至少1项储能项目业绩",
  ].join("；");
  const result = extractProjectRequirements(text, { batchId: "test" });
  assert.deepEqual(result.clauses, ["近三年具有至少1项储能项目业绩"]);
  assert.deepEqual(result.records.map((record) => record.requirementSubtype), ["PERFORMANCE"]);
});

test("extracts explicit disaster-prevention qualification and safety knowledge certificate", () => {
  const text = [
    "具备地质灾害防治单位资质证书(地质灾害评估和治理工程勘查设计资质乙级及以上)",
    "项目负责人须持有安全生产知识和管理能力考核合格证",
  ].join("；");
  const result = extractProjectRequirements(text, { batchId: "test" });
  const qualification = result.records.find((record) => record.requirementSubtype === "COMPANY_QUALIFICATION");
  assert.equal(qualification?.structuredData.name, "地质灾害评估和治理工程勘查设计");
  assert.equal(qualification?.structuredData.level, "乙级");
  assert.ok(result.records.some(
    (record) => record.requirementSubtype === "PERSONNEL_CERTIFICATE"
      && record.structuredData.certificateName === "安全生产考核合格证书",
  ));
});

test("extracts independent qualification, license, personnel and consortium records", () => {
  const text = [
    "具有消防设施工程专业承包贰级及以上资质和有效的安全生产许可证",
    "项目经理须具备机电工程专业贰级及以上注册建造师执业资格并具备安全B证",
    "本项目不接受联合体投标",
  ].join("；");
  const result = extractProjectRequirements(text, { batchId: "test" });
  const subtypes = new Set(result.records.map((record) => record.requirementSubtype));
  assert.ok(subtypes.has("COMPANY_QUALIFICATION"));
  assert.ok(subtypes.has("COMPANY_LICENSE"));
  assert.ok(subtypes.has("PERSONNEL_CERTIFICATE"));
  assert.ok(subtypes.has("CONSORTIUM"));
  assert.equal(result.records.find((record) => record.requirementSubtype === "COMPANY_QUALIFICATION").structuredData.level, "二级");
  assert.equal(result.records.find((record) => record.requirementSubtype === "CONSORTIUM").structuredData.allowed, false);
  for (const record of result.records) assert.deepEqual(validateProjectRequirement(record, text), []);
});

test("does not invent platform-to-list relationships in compound credit clauses", () => {
  const text = "未被信用中国和中国执行信息公开网列入失信被执行人、重大税收违法失信主体";
  const result = extractProjectRequirements(text, { batchId: "test" });
  const credits = result.records.filter((record) => record.requirementSubtype === "CREDIT");
  assert.equal(credits.length, 1);
  assert.equal(credits[0].requirementText, text);
  assert.equal(credits[0].structuredData.platform, "信用中国、中国执行信息公开网");
  assert.equal(credits[0].structuredData.restrictedList, "失信被执行人、重大税收违法失信主体");
});

test("classifies related-party restrictions once without creating a lot-count rule", () => {
  const text = "单位负责人为同一人或者存在控股、管理关系的不同单位不得同时参加本项目同一标段投标";
  const result = extractProjectRequirements(text, { batchId: "test" });
  const basicCodes = result.records
    .filter((record) => record.requirementSubtype === "BASIC_CONDITION")
    .map((record) => record.structuredData.conditionCode);
  assert.deepEqual(basicCodes, ["RELATED_PARTY_RESTRICTION"]);
});

test("uses the fixed V1 fields and nulls for an information-system certification", () => {
  const text = "投标人须具有CMMI3级及以上认证资质证书";
  const result = extractProjectRequirements(text, { batchId: "test" });
  const certification = result.records.find((record) => record.requirementSubtype === "COMPANY_CERTIFICATION");
  assert.ok(certification);
  assert.equal(certification.structuredData.standardCode, "CMMI");
  assert.equal(certification.structuredData.level, "3级");
  assert.equal(certification.structuredData.compare, "AT_LEAST");
  assert.equal(certification.structuredData.certificationScope, null);
  assert.deepEqual(validateProjectRequirement(certification, text), []);
});

test("classifies frequent historical wording variants without AI", () => {
  const source = [
    "投标人具有相应有效的危险化学品道路运输许可相关证件。",
    "提供特种设备型式试验证书（压力管道元件）。",
    "拟派勘察负责人须具备注册土木工程师（岩土）执业资格。",
    "投标人近三年内至少有一项同类产品。",
  ].join("；");
  const result = extractProjectRequirements(source, { batchId: "test" });
  const subtypes = result.records.map((item) => item.requirementSubtype);
  assert.ok(subtypes.includes("COMPANY_LICENSE"));
  assert.ok(subtypes.includes("COMPANY_CERTIFICATION"));
  assert.ok(subtypes.includes("PERSONNEL_CERTIFICATE"));
  assert.ok(subtypes.includes("PERFORMANCE"));
  assert.equal(subtypes.includes("OTHER"), false);
  for (const record of result.records) assert.deepEqual(validateProjectRequirement(record, source), []);
});
