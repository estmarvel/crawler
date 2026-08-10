#!/usr/bin/env node
"use strict";

const fs = require("node:fs");
const path = require("node:path");
const { createHash } = require("node:crypto");
const { spawn } = require("node:child_process");
const { createGzip } = require("node:zlib");
const { pipeline } = require("node:stream/promises");

function timestamp() {
  return new Date().toISOString().replace(/[-:]/g, "").replace(/\.\d{3}Z$/, "Z");
}

function decoded(value) {
  return decodeURIComponent(value || "");
}

function waitFor(child, stderrChunks, stdinValue = null) {
  child.stderr.on("data", (chunk) => stderrChunks.push(chunk));
  if (stdinValue !== null) child.stdin.end(`${stdinValue}\n`);
  return new Promise((resolve, reject) => {
    child.once("error", reject);
    child.once("close", (code, signal) => {
      if (code === 0) resolve();
      else reject(new Error(`process failed (code=${code}, signal=${signal || "none"}): ${Buffer.concat(stderrChunks).toString("utf8").trim()}`));
    });
  });
}

async function sha256(filePath) {
  const digest = createHash("sha256");
  await pipeline(fs.createReadStream(filePath), digest);
  return digest.digest("hex");
}

async function dumpMySql(url, target) {
  const database = decoded(url.pathname.replace(/^\//, ""));
  const args = [
    `--host=${url.hostname}`,
    `--port=${url.port || "3306"}`,
    `--user=${decoded(url.username)}`,
    "--single-transaction",
    "--quick",
    "--triggers",
    "--hex-blob",
    "--set-gtid-purged=OFF",
    "--column-statistics=0",
    "--no-tablespaces",
    database,
  ];
  const stderr = [];
  const child = spawn("mysqldump", args, {
    env: { ...process.env, MYSQL_PWD: decoded(url.password) },
    stdio: ["ignore", "pipe", "pipe"],
  });
  await Promise.all([
    pipeline(child.stdout, createGzip({ level: 6 }), fs.createWriteStream(target, { mode: 0o600 })),
    waitFor(child, stderr),
  ]);
  return database;
}

async function dumpMongo(url, databaseName, target) {
  const configPath = path.join(path.dirname(target), ".mongodump-config.json");
  const safeUrl = new URL(url.toString());
  const password = decoded(safeUrl.password);
  safeUrl.password = "";
  fs.writeFileSync(
    configPath,
    `${JSON.stringify({ uri: safeUrl.toString(), password }, null, 2)}\n`,
    { mode: 0o600 },
  );
  const args = [
    `--config=${configPath}`,
    `--db=${databaseName}`,
    `--archive=${target}`,
    "--gzip",
    "--quiet",
  ];
  const stderr = [];
  try {
    const child = spawn("mongodump", args, { stdio: ["ignore", "ignore", "pipe"] });
    await waitFor(child, stderr);
  } finally {
    fs.rmSync(configPath, { force: true });
  }
}

async function main() {
  for (const name of ["DATABASE_URL", "MONGODB_URL", "MONGODB_DATABASE"]) {
    if (!process.env[name]) throw new Error(`${name} is required; start Node with --env-file`);
  }
  const requested = process.argv.find((arg) => arg.startsWith("--output-dir="));
  const root = requested
    ? path.resolve(requested.slice("--output-dir=".length))
    : path.resolve("db_backups", `pre_crawler_import_${timestamp()}`);
  fs.mkdirSync(root, { recursive: true, mode: 0o700 });
  fs.chmodSync(root, 0o700);

  const mysqlUrl = new URL(process.env.DATABASE_URL);
  const mongoUrl = new URL(process.env.MONGODB_URL);
  const mysqlFile = path.join(root, "mysql.sql.gz");
  const mongoFile = path.join(root, "mongo.archive.gz");
  console.log(`Backup directory: ${root}`);
  console.log("Backing up MySQL...");
  const mysqlDatabase = await dumpMySql(mysqlUrl, mysqlFile);
  console.log("Backing up MongoDB...");
  await dumpMongo(mongoUrl, process.env.MONGODB_DATABASE, mongoFile);

  const files = [];
  for (const filePath of [mysqlFile, mongoFile]) {
    const stat = fs.statSync(filePath);
    if (!stat.size) throw new Error(`backup file is empty: ${filePath}`);
    files.push({ file: path.basename(filePath), bytes: stat.size, sha256: await sha256(filePath) });
  }
  const manifest = {
    createdAt: new Date().toISOString(),
    reason: "before importing refactored crawler new_output",
    mysql: { host: mysqlUrl.hostname, port: mysqlUrl.port || "3306", database: mysqlDatabase },
    mongo: { host: mongoUrl.hostname, port: mongoUrl.port || "27017", database: process.env.MONGODB_DATABASE },
    files,
  };
  fs.writeFileSync(path.join(root, "manifest.json"), `${JSON.stringify(manifest, null, 2)}\n`, { mode: 0o600 });
  console.log(JSON.stringify(manifest, null, 2));
}

main().catch((error) => {
  console.error(`Backup failed: ${error.message}`);
  process.exitCode = 1;
});
