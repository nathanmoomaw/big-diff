// Browser smoke test: both diff tabs against a running site (default: `vite preview`, or pass a URL).
// The engine is the real one (Pyodide from jsDelivr), so this catches Python changes that break the
// browser path. Usage: node tests/smoke.mjs [url]
import assert from 'node:assert/strict'
import zlib from 'node:zlib'
import { chromium } from 'playwright'
import { preview } from 'vite'

const server = process.argv[2] ? null : await preview({ preview: { port: 0 }, logLevel: 'silent' })
const url = process.argv[2] ?? server.resolvedUrls.local[0]

// A 4x4 RGB PNG; `px(x, y)` gives each pixel's colour.
function png(px) {
  const chunk = (type, data) => {
    const body = Buffer.concat([Buffer.from(type), data])
    const out = Buffer.alloc(body.length + 8)
    out.writeUInt32BE(data.length)
    body.copy(out, 4)
    out.writeUInt32BE(zlib.crc32(body), body.length + 4)
    return out
  }
  const ihdr = Buffer.alloc(13)
  ihdr.writeUInt32BE(4, 0); ihdr.writeUInt32BE(4, 4); ihdr[8] = 8; ihdr[9] = 2
  const rows = Array.from({ length: 4 }, (_, y) => Buffer.from([0, ...Array.from({ length: 4 }, (_, x) => px(x, y)).flat()]))
  return Buffer.concat([Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]), chunk('IHDR', ihdr), chunk('IDAT', zlib.deflateSync(Buffer.concat(rows))), chunk('IEND', Buffer.alloc(0))])
}

const browser = await chromium.launch()
try {
  const page = await browser.newPage()
  const errors = []
  page.on('pageerror', e => errors.push(e.message))
  const status = () => page.locator('.status:visible').first().innerText()
  const diff = () => page.click('button.go:visible')

  await page.goto(url)
  await page.waitForSelector('button.go:visible:not([disabled])', { timeout: 120_000 })

  // JSON matched by id: reorder ignored, 1.0 → 1.0005 within tolerance, one real change
  await page.fill('#ta', '{"items":[{"id":1,"v":1.0},{"id":2,"v":"a"}]}')
  await page.fill('#tb', '{"items":[{"id":2,"v":"b"},{"id":1,"v":1.0005}]}')
  await page.fill('#key', 'id')
  await page.fill('#tol', '0.001')
  await diff()
  await page.waitForSelector('ul.ops li')
  assert.equal(await page.locator('ul.ops').innerText(), '~ $.items[id=2].v: "a" → "b"')

  await page.selectOption('#kind', 'text')
  await page.fill('#ta', 'a\nb\nc')
  await page.fill('#tb', 'a\nB\nc\nd')
  await diff()
  await page.waitForSelector('table.sbs')
  assert.equal(await status(), '2 line(s) added or changed, 1 removed or changed')

  await page.selectOption('#kind', 'json')
  await page.fill('#ta', '{"a":')
  await diff()
  await page.waitForSelector('.status.err')
  assert.match(await status(), /JSONDecodeError/)

  await page.click('nav button:has-text("Images")')
  const file = (name, px) => ({ name, mimeType: 'image/png', buffer: png(px) })
  await page.setInputFiles('#ia', file('a.png', () => [255, 255, 255]))
  await page.setInputFiles('#ib', file('b.png', (x, y) => (x === 1 && y === 1 ? [0, 0, 0] : [255, 255, 255])))
  await diff()
  await page.waitForSelector('.imgs')
  assert.equal(await status(), '1 of 16 px changed (6.25%), within box (1, 1, 2, 2)')

  await page.setViewportSize({ width: 390, height: 844 })
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth), 390, 'no sideways scroll at phone width')
  assert.deepEqual(errors, [])
  console.log('site smoke: ok')
} finally {
  await browser.close()
  await server?.close()
}
