#!/usr/bin/env node
"use strict";

const path = require("node:path");

const API_ROOT = path.resolve(
  __dirname,
  "../../recommendation/project-recommendation-system/api",
);
const DATA_SOURCE_ID = 12;
const DATA_SOURCE_CODE = "bitbid";
const EXPECTED_SOURCE_NAME = "比比网电子招标投标交易平台";
const PROJECT_BATCH_SIZE = 500;

function requireFromApi(packageName) {
  return require(require.resolve(packageName, { paths: [API_ROOT] }));
}

function chunks(values, size) {
  const result = [];
  for (let index = 0; index < values.length; index += size) {
    result.push(values.slice(index, index + size));
  }
  return result;
}

function stringify(value) {
  return JSON.stringify(
    value,
    (key, item) => typeof item === "bigint" ? item.toString() : item,
    2,
  );
}

async function count(prisma, model, where) {
  return prisma[model].count({ where });
}

async function resolveScope(prisma) {
  const source = await prisma.dataSource.findUnique({ where: { id: DATA_SOURCE_ID } });
  if (!source) throw new Error(`data_source.id=${DATA_SOURCE_ID} does not exist`);
  if (source.shortCode !== DATA_SOURCE_CODE || source.name !== EXPECTED_SOURCE_NAME) {
    throw new Error(
      `data_source.id=${DATA_SOURCE_ID} is ${source.shortCode}/${source.name}, `
      + `expected ${DATA_SOURCE_CODE}/${EXPECTED_SOURCE_NAME}`,
    );
  }
  const projectRows = await prisma.projectNotice.findMany({
    where: { sourceSite: source.name },
    select: { projectId: true },
    distinct: ["projectId"],
  });
  const projectIds = projectRows.map((row) => row.projectId);
  const crossSiteProjects = projectIds.length
    ? await prisma.project.count({
      where: {
        id: { in: projectIds },
        notices: { some: { sourceSite: { not: source.name } } },
      },
    })
    : 0;
  if (crossSiteProjects !== 0) {
    throw new Error(
      `${crossSiteProjects} Bitbid-linked projects also belong to other sites; refusing deletion`,
    );
  }
  return { source, projectIds };
}

async function mysqlPlan(prisma, scope) {
  const projectWhere = { projectId: { in: scope.projectIds } };
  const noticeWhere = { sourceSite: scope.source.name };
  const rawWhere = { dataSourceId: scope.source.id };
  const plan = {
    dataSourcePreserved: {
      id: scope.source.id,
      shortCode: scope.source.shortCode,
      name: scope.source.name,
    },
    rawNotice: await count(prisma, "rawNotice", rawWhere),
    rawNoticeAttachment: await count(prisma, "rawNoticeAttachment", {
      rawNotice: { is: rawWhere },
    }),
    crawlTaskNotice: await count(prisma, "crawlTaskNotice", {
      rawNotice: { is: rawWhere },
    }),
    noticeExtraction: await count(prisma, "noticeExtraction", {
      rawNotice: { is: rawWhere },
    }),
    projectNotice: await count(prisma, "projectNotice", noticeWhere),
    projectNoticeAttachment: await count(prisma, "projectNoticeAttachment", {
      notice: { is: noticeWhere },
    }),
    project: scope.projectIds.length,
    dependencies: {},
  };
  for (const model of [
    "competitionAnalysis",
    "contract",
    "projectCompanyRelation",
    "projectRequirement",
    "recommendationResult",
    "userFavorite",
    "userFeedback",
    "winProbabilityAnalysis",
  ]) {
    plan.dependencies[model] = scope.projectIds.length
      ? await count(prisma, model, projectWhere)
      : 0;
  }
  return plan;
}

async function mongoPlan(mongo) {
  const rawUids = await mongo.collection("raw_notices")
    .find({ dataSourceId: DATA_SOURCE_ID }, { projection: { rawNoticeUid: 1 } })
    .map((row) => row.rawNoticeUid)
    .toArray();
  return {
    rawUids,
    rawNotice: rawUids.length,
    noticeExtraction: rawUids.length
      ? await mongo.collection("notice_extractions").countDocuments({
        rawNoticeUid: { $in: rawUids },
      })
      : 0,
  };
}

async function deleteMongo(mongo, plan) {
  let extractionDeleted = 0;
  for (const batch of chunks(plan.rawUids, 1000)) {
    const result = await mongo.collection("notice_extractions").deleteMany({
      rawNoticeUid: { $in: batch },
    });
    extractionDeleted += result.deletedCount;
  }
  const rawResult = await mongo.collection("raw_notices").deleteMany({
    dataSourceId: DATA_SOURCE_ID,
  });
  return { rawNotice: rawResult.deletedCount, noticeExtraction: extractionDeleted };
}

async function deleteProjectDependencies(tx, projectIds) {
  const totals = {};
  for (const model of [
    "competitionAnalysis",
    "contract",
    "projectCompanyRelation",
    "projectRequirement",
    "recommendationResult",
    "userFavorite",
    "userFeedback",
    "winProbabilityAnalysis",
  ]) totals[model] = 0;

  for (const batch of chunks(projectIds, PROJECT_BATCH_SIZE)) {
    for (const model of Object.keys(totals)) {
      const result = await tx[model].deleteMany({ where: { projectId: { in: batch } } });
      totals[model] += result.count;
    }
  }
  return totals;
}

async function deleteMySql(prisma, scope) {
  return prisma.$transaction(async (tx) => {
    const dependencies = await deleteProjectDependencies(tx, scope.projectIds);
    const projectNoticeAttachment = await tx.projectNoticeAttachment.deleteMany({
      where: { notice: { is: { sourceSite: scope.source.name } } },
    });
    const noticeExtraction = await tx.noticeExtraction.deleteMany({
      where: { rawNotice: { is: { dataSourceId: scope.source.id } } },
    });
    const projectNotice = await tx.projectNotice.deleteMany({
      where: { sourceSite: scope.source.name },
    });
    const crawlTaskNotice = await tx.crawlTaskNotice.deleteMany({
      where: { rawNotice: { is: { dataSourceId: scope.source.id } } },
    });
    const rawNoticeAttachment = await tx.rawNoticeAttachment.deleteMany({
      where: { rawNotice: { is: { dataSourceId: scope.source.id } } },
    });
    const rawNotice = await tx.rawNotice.deleteMany({
      where: { dataSourceId: scope.source.id },
    });
    let project = 0;
    for (const batch of chunks(scope.projectIds, PROJECT_BATCH_SIZE)) {
      const result = await tx.project.deleteMany({ where: { id: { in: batch } } });
      project += result.count;
    }
    return {
      rawNotice: rawNotice.count,
      rawNoticeAttachment: rawNoticeAttachment.count,
      crawlTaskNotice: crawlTaskNotice.count,
      noticeExtraction: noticeExtraction.count,
      projectNotice: projectNotice.count,
      projectNoticeAttachment: projectNoticeAttachment.count,
      project,
      dependencies,
    };
  }, { maxWait: 10_000, timeout: 600_000 });
}

function assertDeletionMatches(plan, deleted, layer) {
  for (const [key, value] of Object.entries(deleted)) {
    if (key === "dependencies") continue;
    if (value !== plan[key]) {
      throw new Error(`${layer}.${key}: deleted=${value}, planned=${plan[key]}`);
    }
  }
  if (deleted.dependencies) {
    for (const [key, value] of Object.entries(deleted.dependencies)) {
      if (value !== plan.dependencies[key]) {
        throw new Error(`${layer}.dependencies.${key}: deleted=${value}, planned=${plan.dependencies[key]}`);
      }
    }
  }
}

async function main() {
  const commit = process.argv.includes("--commit");
  if (!process.env.DATABASE_URL || !process.env.MONGODB_URL) {
    throw new Error("DATABASE_URL and MONGODB_URL are required; start Node with --env-file");
  }
  const { PrismaClient } = requireFromApi("@prisma/client");
  const { MongoClient } = requireFromApi("mongodb");
  const prisma = new PrismaClient();
  const mongoClient = new MongoClient(process.env.MONGODB_URL);
  await prisma.$connect();
  await mongoClient.connect();
  const mongo = mongoClient.db(
    process.env.MONGODB_DATABASE || "project_recommendation_documents",
  );
  try {
    const scope = await resolveScope(prisma);
    const mysql = await mysqlPlan(prisma, scope);
    const mongoData = await mongoPlan(mongo);
    console.log(stringify({ mode: commit ? "COMMIT" : "DRY_RUN", mysql, mongo: {
      rawNotice: mongoData.rawNotice,
      noticeExtraction: mongoData.noticeExtraction,
    } }));
    if (!commit) {
      console.log("Dry run complete. Add --commit to delete only Bitbid data.");
      return;
    }

    // MongoDB 先删除；若后续 MySQL 事务失败，脚本可安全重跑，Mongo 删除是幂等的。
    const mongoDeleted = await deleteMongo(mongo, mongoData);
    assertDeletionMatches(
      { rawNotice: mongoData.rawNotice, noticeExtraction: mongoData.noticeExtraction },
      mongoDeleted,
      "mongo",
    );
    const mysqlDeleted = await deleteMySql(prisma, scope);
    assertDeletionMatches(mysql, mysqlDeleted, "mysql");

    const afterScope = await resolveScope(prisma);
    const afterMySql = await mysqlPlan(prisma, afterScope);
    const afterMongo = await mongoPlan(mongo);
    const remaining = [
      afterMySql.rawNotice,
      afterMySql.rawNoticeAttachment,
      afterMySql.crawlTaskNotice,
      afterMySql.noticeExtraction,
      afterMySql.projectNotice,
      afterMySql.projectNoticeAttachment,
      afterMySql.project,
      ...Object.values(afterMySql.dependencies),
      afterMongo.rawNotice,
      afterMongo.noticeExtraction,
    ];
    if (remaining.some((value) => value !== 0)) {
      throw new Error(`post-delete verification found remaining Bitbid data: ${stringify({ afterMySql, afterMongo })}`);
    }
    console.log(stringify({ deleted: { mysql: mysqlDeleted, mongo: mongoDeleted } }));
    console.log("Bitbid database data deleted and verified; data_source configuration was preserved.");
  } finally {
    await mongoClient.close();
    await prisma.$disconnect();
  }
}

main().catch((error) => {
  console.error(`Bitbid deletion failed: ${error.stack || error.message}`);
  process.exitCode = 1;
});
