#!/usr/bin/env node
/** Smoke-probe @pigeon-sdk/rust-sdk native binding (Feige Electron install). */
import { createRequire } from "node:module";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { existsSync, readFileSync } from "node:fs";

const __dirname = dirname(fileURLToPath(import.meta.url));
const FEIGE_SDK = "E:/feige-electron/抖店工作台/1.1.7/resources/app.asar.unpacked/node_modules/@pigeon-sdk";
const rustSdkDir = join(FEIGE_SDK, "rust-sdk");
const nativePkg = join(FEIGE_SDK, "rust-sdk-win32-x64-msvc");

process.chdir(nativePkg);
process.env.PATH = `${nativePkg};${process.env.PATH || ""}`;

const require = createRequire(join(rustSdkDir, "index.js"));
const report = {
  rust_sdk_dir: rustSdkDir,
  native_pkg: nativePkg,
  node_file: join(nativePkg, "rust-sdk.win32-x64-msvc.node"),
  node_exists: existsSync(join(nativePkg, "rust-sdk.win32-x64-msvc.node")),
  version: null,
  load_ok: false,
  exports: [],
  probes: {},
};

try {
  const ver = JSON.parse(readFileSync(join(nativePkg, "version"), "utf8"));
  report.version = ver;
} catch {}

try {
  const sdk = require(join(rustSdkDir, "index.js"));
  report.load_ok = true;
  report.exports = Object.keys(sdk);
  if (typeof sdk.sum === "function") {
    try {
      report.probes.sum = sdk.sum(1, 2);
    } catch (e) {
      report.probes.sum_error = String(e);
    }
  }
  if (typeof sdk.getDevice === "function") {
    try {
      report.probes.getDevice = sdk.getDevice();
    } catch (e) {
      report.probes.getDevice_error = String(e);
    }
  }
} catch (e) {
  report.load_error = String(e);
}

console.log(JSON.stringify(report, null, 2));
process.exit(report.load_ok ? 0 : 1);
