#!/usr/bin/env node

"use strict";

const {
  openStores,
  parseCommonArgs,
  resolveDataSources,
} = require("./lib/runtime");
const { loadBusinessDataset } = require("./lib/business");

function printHelp() {
  console.log(`Usage:
  node import_projects.js [options]

Options:
  --commit                 Upsert project rows.
  --site=<site>            all or any configured crawler site.
  --output-root=<path>     Crawler new_output root.
  --env-file=<path>        crawler_prisma environment file (default: .env).
  --help                   Show this help.

Project identity priority:
  1. 项目编号
  2. 招标编号 (stored as a namespaced TENDER:<site>:<code> fallback)
  3. exact normalized 项目名称

Without --commit this validates crawler JSON and performs no database writes.
`);
}

function nonNullUpdateData(data) {
  return Object.fromEntries(
    Object.entries(data).filter(([, value]) => value !== null && value !== undefined),
  );
}

async function findExistingProject(transaction, project) {
  const codes = [...new Set([project.data.projectCode, ...project.aliases].filter(Boolean))];
  if (codes.length) {
    const matches = await transaction.project.findMany({ where: { projectCode: { in: codes } } });
    if (matches.length > 1) {
      throw new Error(
        `${project.groupKey}: project identity already points to multiple database rows: ${matches.map((row) => row.id).join(", ")}`,
      );
    }
    if (matches.length === 1) return matches[0];
  }
  if (project.identitySource === "PROJECT_NAME") {
    const matches = await transaction.project.findMany({
      // 仅接管旧导入器留下的无 project_code 行。带其他站点 NAME 哈希、
      // TENDER 代号或真实项目编号的同名项目都不能仅凭名称被合并。
      where: { projectCode: null, projectName: project.data.projectName },
    });
    // 多个历史同名项目无法可靠判定归属；不猜测旧行，创建带稳定 NAME 哈希
    // 的归并项目。下一次运行会先按 projectCode 命中，保持幂等。
    if (matches.length > 1) return null;
    return matches[0] || null;
  }
  return null;
}

async function commitProjects(prisma, projects) {
  const result = { inserted: 0, updated: 0, projectIds: new Map() };
  let processed = 0;
  for (let index = 0; index < projects.length; index += 200) {
    const batch = projects.slice(index, index + 200);
    await prisma.$transaction(async (transaction) => {
      for (const project of batch) {
        const existing = await findExistingProject(transaction, project);
        let stored;
        if (existing) {
          stored = await transaction.project.update({
            where: { id: existing.id },
            data: nonNullUpdateData(project.data),
          });
          result.updated += 1;
        } else {
          stored = await transaction.project.create({ data: project.data });
          result.inserted += 1;
        }
        result.projectIds.set(project.groupKey, stored.id);
      }
    }, { maxWait: 10_000, timeout: 600_000 });
    processed += batch.length;
    console.log(`  Processed ${processed}/${projects.length}`);
  }
  return result;
}

async function main() {
  const options = parseCommonArgs(process.argv.slice(2));
  if (options.help) return printHelp();
  const dataset = await loadBusinessDataset(options.outputRoot, options.sites, {
    includeContent: false,
    fieldMode: "project",
  });
  const sourceCounts = new Map();
  for (const project of dataset.projects) {
    sourceCounts.set(project.identitySource, (sourceCounts.get(project.identitySource) || 0) + 1);
  }
  console.log(`Mode: ${options.commit ? "COMMIT" : "DRY RUN (no database writes)"}`);
  console.log(`Output root: ${options.outputRoot}`);
  console.log(`Validated notices: ${dataset.records.length}`);
  console.log(`Non-PARSED notices kept only in raw/extraction storage: ${dataset.skippedNonParsedCount}`);
  console.log(`Duplicate notices skipped: ${dataset.duplicateCount}`);
  console.log(`Projects to upsert: ${dataset.projects.length}`);
  for (const [source, count] of [...sourceCounts].sort()) console.log(`  ${source}: ${count}`);
  if (!options.commit) return console.log("Dry run complete. Add --commit to upsert project rows.");

  const stores = await openStores(options.envFile);
  try {
    await resolveDataSources(stores.prisma, options.sites);
    const result = await commitProjects(stores.prisma, dataset.projects);
    console.log(`Commit completed: inserted=${result.inserted}, updated=${result.updated}.`);
  } finally {
    await stores.close();
  }
}

if (require.main === module) {
  main().catch((error) => {
    console.error(`Import failed: ${error.stack || error.message}`);
    process.exitCode = 1;
  });
}

module.exports = { commitProjects, findExistingProject, nonNullUpdateData };
