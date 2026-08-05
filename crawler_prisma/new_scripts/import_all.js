#!/usr/bin/env node

"use strict";

const path = require("node:path");
const { spawnSync } = require("node:child_process");

const supported = new Set([
  "--commit",
  "--allow-missing-files",
  "--help",
  "-h",
]);

function validateArgs(args) {
  for (const arg of args) {
    if (
      supported.has(arg) ||
      arg.startsWith("--site=") ||
      arg.startsWith("--output-root=") ||
      arg.startsWith("--api-root=")
    ) continue;
    throw new Error(`Unknown argument: ${arg}`);
  }
}

function run(fileName, args) {
  const result = spawnSync(process.execPath, [path.join(__dirname, fileName), ...args], {
    stdio: "inherit",
  });
  if (result.error) throw result.error;
  if (result.status !== 0) process.exit(result.status || 1);
}

function main() {
  const args = process.argv.slice(2);
  validateArgs(args);
  if (args.includes("--help") || args.includes("-h")) {
    console.log(`Usage:
  node import_all.js [--commit] [--site=<site>] [--output-root=<path>]
                     [--api-root=<path>] [--allow-missing-files]

Order:
  1. raw notice metadata -> MySQL; payload/text -> MongoDB
  2. attachment binaries -> MinIO; object metadata -> MySQL
  3. extraction metadata -> MySQL; extracted fields -> MongoDB

The default is a dry run. Each stage is idempotent and can be rerun.
`);
    return;
  }

  const commonArgs = args.filter((arg) => arg !== "--allow-missing-files");
  run("import_raw_notices.js", commonArgs);
  run(
    "import_raw_notice_attachments.js",
    args.includes("--allow-missing-files") ? [...commonArgs, "--allow-missing-files"] : commonArgs,
  );
  run("import_notice_extractions.js", commonArgs);
}

try {
  main();
} catch (error) {
  console.error(`Import pipeline failed: ${error.stack || error.message}`);
  process.exitCode = 1;
}
