#!/usr/bin/env node
import { createRequire } from "node:module";
import { readFileSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const ROOT = join(__dirname, "..");
const require = createRequire(import.meta.url);
const $root = require(join(ROOT, "analysis/feige_electron_sdk/rust-sdk-api/index.js"));

const raw = readFileSync(join(ROOT, "analysis/feige_push_all.bin"));

for (let i = 0; i < raw.length - 20; i++) {
  try {
    const dec = $root.packedMessage.PackedMessage.decode(raw.subarray(i));
    if (dec.context?.cmdId !== 11356 || !(dec.response?.body?.length > 500)) continue;
    console.log("11356 at", i, "body", dec.response.body.length);
    const body = dec.response.body;
    for (const name of [
      "IMDataUpdateMessage",
      "IMDataUpdate",
    ]) {
      try {
        const upd = $root.biz.pigeon.im[name]?.Response?.decode(body);
        if (upd) console.log(name, JSON.stringify(upd).slice(0, 1200));
      } catch {}
    }
    const txt = body.toString("latin1");
    const ticket = txt.match(/MS4w[A-Za-z0-9+/=_-]{20,200}/);
    if (ticket) console.log("ticket", ticket[0].slice(0, 80));
  } catch {}
}
