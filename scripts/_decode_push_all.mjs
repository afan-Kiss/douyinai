#!/usr/bin/env node
import { createRequire } from "node:module";
import { readFileSync } from "node:fs";
import { join, resolve, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const ROOT = resolve(__dirname, "..");
const API = join(ROOT, "analysis/feige_electron_sdk/rust-sdk-api/index.js");
const require = createRequire(import.meta.url);
const $root = require(API);

function decodePacked(buf) {
  const msg = $root.packedMessage.PackedMessage.decode(buf);
  return {
    cmdId: msg.context?.cmdId,
    status: msg.status,
    err: msg.response?.error || msg.error || null,
    body: msg.response?.body ? Buffer.from(msg.response.body) : null,
  };
}

function find231a(buf) {
  const b = Buffer.from(buf);
  for (let i = 0; i + 169 <= b.length; i++) {
    if (b[i] === 0x23 && b[i + 1] === 0x1a) return b.subarray(i, i + 169).toString("hex");
  }
  return null;
}

const report = JSON.parse(readFileSync(join(ROOT, "analysis/last_invoke2.json"), "utf8"));
const pushes = report.steps?.push_log || [];
const raw = readFileSync(join(ROOT, "analysis/feige_push_all.bin"));
let off = 0;
for (const row of pushes) {
  const slice = raw.subarray(off, off + row.len);
  off += row.len;
  try {
    const d = decodePacked(slice);
    const inner = d.body ? find231a(d.body) : find231a(slice);
    let extra = {};
    if (d.cmdId === 11356 && d.body) {
      try {
        const upd = $root.biz.pigeon.im.IMDataUpdateMessage.Response.decode(d.body);
        extra = {
          keys: Object.keys(upd?.biz_response || {}),
          biz_response: upd?.biz_response,
        };
      } catch (e) {
        extra = { decode_err: String(e), body_head: d.body.subarray(0, 48).toString("hex") };
      }
    }
    console.log(
      JSON.stringify(
        {
          cmdId: d.cmdId,
          status: d.status,
          err: d.err,
          bodyLen: d.body?.length ?? 0,
          inner169: inner?.slice(0, 32) || null,
          extra,
        },
        null,
        0
      )
    );
  } catch (e) {
    console.log(JSON.stringify({ cmdId: row.cmdId, len: row.len, error: String(e) }));
  }
}
