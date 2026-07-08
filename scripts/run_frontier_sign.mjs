#!/usr/bin/env node
/** Call byted_acrawler.frontierSign if available (stdin: JSON stub input). */
import { readFileSync } from "node:fs";
import { JSDOM } from "jsdom";

const raw = readFileSync(0, "utf8").trim();
const stubIn = raw ? JSON.parse(raw) : { "X-MS-STUB": "0".repeat(32) };

const dom = new JSDOM("<!doctype html><html><body></body></html>", {
  url: "https://im.jinritemai.com/pc_seller_v2/main/workspace",
  pretendToBeVisual: true,
});

global.window = dom.window;
global.document = dom.window.document;
global.navigator = dom.window.navigator;

let headers = {};
try {
  // bdms/acrawler may be injected by page scripts — optional load
  const ac = global.window.byted_acrawler;
  if (ac && typeof ac.frontierSign === "function") {
    headers = ac.frontierSign(stubIn) || {};
  }
} catch (e) {
  process.stdout.write(JSON.stringify({ ok: false, error: String(e), headers: {} }));
  process.exit(2);
}

process.stdout.write(JSON.stringify({ ok: Object.keys(headers).length > 0, headers }));
