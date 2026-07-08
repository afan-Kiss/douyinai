#!/usr/bin/env node
import { createRequire } from "node:module";
import { readFileSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const ROOT = join(__dirname, "..");
const require = createRequire(join(ROOT, "package.json"));
const $root = require(join(ROOT, "analysis/feige_electron_sdk/rust-sdk-api/index.js"));

const raw = readFileSync(join(ROOT, "analysis/feige_push_all.bin"));
let pos = 0;
let n = 0;
const cmds = {};

while (pos < raw.length) {
  try {
    const dec = $root.packedMessage.PackedMessage.decode(raw.subarray(pos));
    const enc = $root.packedMessage.PackedMessage.encode(dec).finish();
    pos += enc.length;
    n++;
    const cmd = dec.context?.cmdId;
    const body = dec.response?.body;
    cmds[cmd ?? 0] = (cmds[cmd ?? 0] || 0) + 1;
    if (cmd === 11356 || cmd === 11345 || cmd === 11333) {
      console.log("frame", n, "cmd", cmd, "body", body?.length ?? 0);
      if (body?.length) {
        const txt = body.toString("latin1");
        const t = txt.match(/MS4w[A-Za-z0-9+/=_-]{20,120}/);
        if (t) console.log("  ticket", t[0].slice(0, 72));
        for (let j = 0; j + 169 <= body.length; j++) {
          const sl = body.subarray(j, j + 169);
          if (sl[0] === 0x23 && sl[1] === 0x1a) console.log("  inner231a@", j);
        }
        try {
          const conv = $root.biz.pigeon.im.IMGetConversation.Response.decode(body);
          const c = conv?.biz_response?.conversation;
          if (c) console.log("  conv ticket", c.ticket?.slice(0, 40), "short", String(c.short_id));
        } catch {}
      }
    }
  } catch {
    pos++;
  }
}
console.log("total frames", n, "consumed", pos, "/", raw.length);
console.log("cmds", cmds);
