#!/usr/bin/env node
/**
 * Decode edbX INIT_SYNC 169B inner — extract embedded ticket / protobuf hints.
 * Usage: node scripts/decode_edbx_inner.mjs [inner_hex]
 */
import { readFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const ROOT = resolve(__dirname, "..");

function readVarint(buf, i) {
  let val = 0;
  let shift = 0;
  const start = i;
  while (i < buf.length) {
    const b = buf[i++];
    val |= (b & 0x7f) << shift;
    if (!(b & 0x80)) return [val, i];
    shift += 7;
  }
  return [null, start];
}

function scanFields(buf, max = 24) {
  const fields = [];
  let i = 0;
  while (i < buf.length && fields.length < max) {
    const start = i;
    const [tag, i2] = readVarint(buf, i);
    if (tag === null || i2 === start) break;
    i = i2;
    const fn = tag >> 3;
    const wire = tag & 7;
    const row = { offset: start, field: fn, wire };
    if (wire === 0) {
      const [v, i3] = readVarint(buf, i);
      row.varint = v;
      i = i3;
    } else if (wire === 2) {
      const [ln, i3] = readVarint(buf, i);
      i = i3;
      const chunk = buf.subarray(i, i + ln);
      i += ln;
      row.len = ln;
      row.hex = chunk.subarray(0, 48).toString("hex");
      row.ascii = chunk.toString("utf8").replace(/[^\x20-\x7e]/g, ".");
    } else if (wire === 5) {
      row.fixed32 = buf.subarray(i, i + 4).toString("hex");
      i += 4;
    } else if (wire === 1) {
      row.fixed64 = buf.subarray(i, i + 8).toString("hex");
      i += 8;
    } else break;
    fields.push(row);
  }
  return fields;
}

function decodeInnerHex(hx) {
  const inner = Buffer.from(hx, "hex");
  if (inner.length !== 169) {
    return { ok: false, error: `expected 169 bytes, got ${inner.length}` };
  }
  const magic = inner.subarray(0, 4).toString("ascii");
  if (magic !== "edbX") {
    const body = inner.subarray(8);
    return {
      ok: true,
      variant: "encrypted_send",
      header_hex: inner.subarray(0, 8).toString("hex"),
      body_len: body.length,
      body_head: body.subarray(0, 32).toString("hex"),
      aes_gcm: {
        nonce: body.subarray(0, 12).toString("hex"),
        ciphertext_len: body.length - 28,
        tag: body.subarray(-16).toString("hex"),
      },
      wire_scan: scanFields(body),
    };
  }
  const payload = inner.subarray(4);
  const ticketRe = /[A-Za-z0-9_+/=-]{20,}:[0-9]+::[0-9]+:[0-9]+:pigeon/;
  const ticketMatch = payload.toString("utf8").match(ticketRe);
  return {
    ok: true,
    variant: "edbx_init",
    magic,
    payload_len: payload.length,
    ticket: ticketMatch ? ticketMatch[0] : null,
    payload_fields: scanFields(payload),
    send_usable: false,
    note: "INIT_SYNC seed from get_message_by_init — not WS send inner",
  };
}

function main() {
  let hx = process.argv[2] || "";
  if (!hx) {
    const cachePath = join(ROOT, "session/ws_inner_cache.json");
    const cache = JSON.parse(readFileSync(cachePath, "utf8"));
    outer: for (const ent of Object.values(cache)) {
      if (!ent || typeof ent !== "object") continue;
      for (const [k, v] of Object.entries(ent)) {
        if (k.startsWith("_") || typeof v !== "string" || v.length !== 338) continue;
        if (Buffer.from(v, "hex").subarray(0, 4).toString("ascii") === "edbX") {
          hx = v;
          break outer;
        }
      }
    }
  }
  if (!hx) {
    console.error(JSON.stringify({ ok: false, error: "no edbX inner found" }));
    process.exit(1);
  }
  console.log(JSON.stringify(decodeInnerHex(hx), null, 2));
}

main();
