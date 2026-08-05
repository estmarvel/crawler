#!/usr/bin/env node

"use strict";

const path = require("node:path");
const { DEFAULT_API_ROOT, DEFAULT_OUTPUT_ROOT, openStores, resolveDataSources } = require("./lib/runtime");
const { buildProjects, hydrateMappings, readMappings } = require("./lib/derived");

function parseArgs(argv) {
  const options = {
    commit: false,
    apiRoot: process.env.PROJECT_RECOMMENDATION_API_ROOT || DEFAULT_API_ROOT,
    mappingRoot: path.join(DEFAULT_OUTPUT_ROOT, "project_identity_mapping"),
  };
  for (const arg of argv) {
    if (arg === "--commit") options.commit = true;
    else if (arg === "--help" || arg === "-h") options.help = true;
    else if (arg.startsWith("--api-root=")) options.apiRoot = path.resolve(arg.slice(11));
    else if (arg.startsWith("--mapping-root=")) options.mappingRoot = path.resolve(arg.slice(15));
    else throw new Error(`Unknown argument: ${arg}`);
  }
  return options;
}

function printHelp() {
  console.log(`Usage:
  node import_projects.js [--commit] [--api-root=<path>] [--mapping-root=<path>]

Reads current MySQL/MongoDB notice data plus huaxin/jiubang mapping JSON.
Without --commit it performs a read-only dry run.
`);
}

function databaseData(project) {
  const { groupKey, records, ...data } = project;
  return data;
}

async function commitProjects(prisma, projects) {
  let inserted = 0;
  let updated = 0;
  await prisma.$transaction(async (transaction) => {
    for (const project of projects) {
      const data = databaseData(project);
      let existing;
      if (project.projectCode) {
        existing = await transaction.project.findUnique({ where: { projectCode: project.projectCode } });
      } else {
        const matches = await transaction.project.findMany({
          where: { projectCode: null, projectName: project.projectName },
        });
        if (matches.length > 1) throw new Error(`Multiple standalone projects named ${project.projectName}`);
        existing = matches[0] || null;
      }
      if (existing) {
        await transaction.project.update({ where: { id: existing.id }, data });
        updated += 1;
      } else {
        await transaction.project.create({ data });
        inserted += 1;
      }
    }
  }, { maxWait: 10_000, timeout: 300_000 });
  return { inserted, updated };
}

async function main() {
  const options = parseArgs(process.argv.slice(2));
  if (options.help) return printHelp();
  const sites = ["huaxin", "jiubang"];
  const mappings = readMappings(options.mappingRoot, sites);
  const stores = await openStores(options.apiRoot, { mongo: true });
  try {
    const dataSources = await resolveDataSources(stores.prisma, sites);
    const hydrated = await hydrateMappings(stores, mappings.selected, dataSources);
    const projects = buildProjects(hydrated.hydrated);
    const counts = new Map();
    for (const project of projects) counts.set(project.currentStatus, (counts.get(project.currentStatus) || 0) + 1);

    console.log(`Mode: ${options.commit ? "COMMIT" : "DRY RUN (read only)"}`);
    console.log(`Selected mapping rows: ${mappings.selected.length}`);
    console.log(`Stale mapping rows ignored: ${hydrated.stale.length}`);
    console.log(`Review-required mapping rows excluded: ${mappings.review.length}`);
    console.log(`Hydrated notices: ${hydrated.hydrated.length}`);
    console.log(`Projects to upsert: ${projects.length}`);
    console.log(`Projects with code: ${projects.filter((row) => row.projectCode).length}`);
    console.log(`Standalone projects: ${projects.filter((row) => !row.projectCode).length}`);
    for (const [status, count] of [...counts].sort()) console.log(`  ${status}: ${count}`);
    if (!options.commit) return console.log("Dry run complete. Add --commit to upsert project rows.");

    const result = await commitProjects(stores.prisma, projects);
    console.log(`Commit completed: inserted=${result.inserted}, updated=${result.updated}.`);
  } finally {
    await stores.close();
  }
}

main().catch((error) => {
  console.error(`Import failed: ${error.stack || error.message}`);
  process.exitCode = 1;
});
