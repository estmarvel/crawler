#!/usr/bin/env node
"use strict";

const { iterateJsonNotices, parseCommonArgs } = require("./lib/runtime");
const { mapBusinessRecord } = require("./lib/business");

async function main() {
  const options = parseCommonArgs(process.argv.slice(2));
  if (options.help) {
    console.log("Usage: node new_scripts/audit_business_mapping.js [--site=all|SITE] [--output-root=PATH]");
    return;
  }
  let checked = 0;
  let failed = 0;
  const examples = [];
  for await (const record of iterateJsonNotices(options.outputRoot, options.sites)) {
    checked += 1;
    try {
      mapBusinessRecord(record, { includeContent: false });
    } catch (error) {
      failed += 1;
      if (examples.length < 100) examples.push(error.message);
    }
    if (checked % 5000 === 0) {
      console.log(`[映射审计] 已检查=${checked} 失败=${failed}`);
    }
  }
  console.log(`Mapping audit complete: checked=${checked}, failed=${failed}`);
  for (const message of examples) console.log(`  - ${message}`);
  if (failed) process.exitCode = 1;
}

main().catch((error) => {
  console.error(`Mapping audit failed: ${error.stack || error.message}`);
  process.exitCode = 1;
});
