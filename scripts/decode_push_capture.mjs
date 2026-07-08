#!/usr/bin/env node
import { readFileSync, writeFileSync } from "node:fs";
import { join, resolve } from "node:path";
import { createRequire } from "node:module";

const ROOT = resolve(import.meta.dirname, "..");
const $root = createRequire(import.meta.url)(join(ROOT, "analysis/feige_electron_sdk/rust-sdk-api/index.js"));
const raw = readFileSync(join(ROOT, "analysis/feige_push_capture.bin"));

function shannonEntropy(buf) {
  const freq = new Array(256).fill(0);
  for (const b of buf) freq[b]++;
  let ent = 0;
  for (const f of freq) {
    if (!f) continue;
    const p = f / buf.length;
    ent -= p * Math.log2(p);
  }
  return ent;
}

function find169(buf) {
  const b = Buffer.from(buf);
  for (let i = 0; i + 169 <= b.length; i++) {
    const slice = b.subarray(i, i + 169);
    const body = slice.subarray(8);
    const h0 = slice.readUInt32LE(0);
    const h1 = slice.readUInt32LE(4);
    if (h0 === 0 && h1 === 0) continue;
    if (shannonEntropy(body) >= 6.0) {
      return { offset: i, hex: slice.toString("hex"), ent: shannonEntropy(body) };
    }
  }
  return null;
}

const dec = $root.packedMessage.PackedMessage.decode(raw);
const report = {
  len: raw.length,
  cmdId: dec.context?.cmdId,
  status: dec.status,
  error: dec.error || null,
  req_len: dec.request?.length ?? 0,
  body_len: dec.response?.body?.length ?? 0,
};

for (const [label, blob] of [
  ["raw", raw],
  ["request", dec.request],
  ["response_body", dec.response?.body],
].filter(([, b]) => b?.length)) {
  const hit = find169(blob);
  if (hit) report.inner = { via: label, ...hit };
}

if (dec.response?.body?.length) {
  writeFileSync(join(ROOT, "analysis/feige_push_body.bin"), Buffer.from(dec.response.body));
  report.body_saved = "analysis/feige_push_body.bin";
}

console.log(JSON.stringify(report, null, 2));
