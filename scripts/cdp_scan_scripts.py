#!/usr/bin/env python3
import asyncio, json, re
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]

async def main():
    from playwright.async_api import async_playwright
    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp('http://127.0.0.1:9222')
        page = next(pg for pg in browser.contexts[0].pages if 'jinritemai' in (pg.url or ''))
        data = await page.evaluate(r"""() => {
          const scripts = [...document.scripts].map(s => s.textContent||'').filter(t => t.length > 50);
          const hits = [];
          for (const t of scripts) {
            if (/paths|pageId|bdms\.init|1383|30026|backstage|cmpoent/.test(t))
              hits.push(t.slice(0, 3000));
          }
          return { hits, cookie: document.cookie.match(/gfkadpd=[^;]+/)?.[0]||'' };
        }""")
        return data

r = asyncio.run(main())
(ROOT/'analysis'/'page_sdk_scripts.json').write_text(json.dumps(r, ensure_ascii=False, indent=2), encoding='utf-8')
print('hits', len(r['hits']), 'cookie', r['cookie'])
for i,h in enumerate(r['hits'][:3]):
    print('---', i, '---')
    print(h[:800])
