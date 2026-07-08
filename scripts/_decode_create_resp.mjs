#!/usr/bin/env node
import { createRequire } from "node:module";
import { join, resolve, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { readFileSync } from "node:fs";

const __dirname = dirname(fileURLToPath(import.meta.url));
const ROOT = resolve(__dirname, "..");
const API = join(ROOT, "analysis/feige_electron_sdk/rust-sdk-api/index.js");
const require = createRequire(import.meta.url);
const $root = require(API);

const report = JSON.parse(readFileSync(join(ROOT, "analysis/feige_rust_invoke_report.json"), "utf8"));
const hex = report.node?.steps?.createMessage?.response_body_hex || "";
if (!hex) {
  console.log("no body hex");
  process.exit(1);
}
const body = Buffer.from(hex, "hex");
const biz = $root.biz.pigeon.im.IMCreateMessage.Response.decode(body);
const msg = biz?.biz_response?.message;
console.log(
  JSON.stringify(
    {
      flight_status: msg?.flight_status,
      client_id: msg?.client_message_id || msg?.client_id,
      server_id: msg?.server_id != null ? String(msg.server_id) : null,
      content: msg?.content,
      type: msg?.type,
      ext_keys: msg?.ext ? Object.keys(msg.ext).slice(0, 12) : [],
    },
    null,
    2
  )
);
