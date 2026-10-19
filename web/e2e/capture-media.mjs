// Captures the README screenshots of the current app (Replay mode, default example alert, synthetic data).
// The approval's freeze request goes to a local mock of GitHub, so start the app with
//   OPS_GITHUB_API=http://127.0.0.1:9101 OPS_GITHUB_TOKEN=demo uv run uvicorn chokepoint.api:app --port 8001
//   BASE_URL=http://127.0.0.1:8001 OUT_DIR=docs/media node web/e2e/capture-media.mjs     (then convert to webp with ffmpeg)
import { existsSync, mkdirSync } from 'node:fs'
import { createServer } from 'node:http'
import { chromium } from 'playwright-core'

const BASE = process.env.BASE_URL || 'http://127.0.0.1:8001'
const OUT = process.env.OUT_DIR || './docs/media'
const CHROME = process.env.CHROME_PATH || ['C:/Program Files/Google/Chrome/Application/chrome.exe', '/usr/bin/google-chrome'].find((p) => existsSync(p))
mkdirSync(OUT, { recursive: true })
const issues = []
const mock = createServer((req, res) => {
  let b = ''
  req.on('data', (c) => (b += c))
  req.on('end', () => {
    res.setHeader('Content-Type', 'application/json')
    if (req.method === 'GET') return res.end(JSON.stringify(issues))
    issues.push({ title: JSON.parse(b).title, html_url: `http://127.0.0.1:9101/local-standin/issues/${issues.length + 1}` })
    res.statusCode = 201
    res.end(JSON.stringify(issues.at(-1)))
  })
})
await new Promise((r) => mock.listen(9101, '127.0.0.1', r))
const browser = await chromium.launch({ executablePath: CHROME, headless: true })
const c0 = await browser.newContext()
await c0.request.post(`${BASE}/api/demo/reset`)
await c0.close()
const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } })
const page = await ctx.newPage()
await page.goto(BASE + '/#/app', { waitUntil: 'networkidle' })
await page.getByRole('button', { name: /Run the example alert/ }).click()
await page.waitForSelector('.chip.good', { timeout: 30000 })
await page.waitForSelector('.after', { timeout: 8000 })
await page.waitForTimeout(1500)
await page.screenshot({ path: `${OUT}/app-result.png` })
await page.getByRole('tab', { name: 'Compare' }).click()
await page.waitForTimeout(900)
await page.evaluate(() => document.querySelector('.right')?.scrollTo({ top: 300 }))
await page.waitForTimeout(600)
await page.screenshot({ path: `${OUT}/app-compare.png` })
await page.evaluate(() => document.querySelector('.right')?.scrollTo({ top: 0 }))
await page.getByRole('button', { name: /Approve a freeze order/ }).click()
await page.waitForSelector('.gate li.ok')
await page.getByLabel('Your name').fill('A. Rao')
await page.waitForTimeout(500)
await page.screenshot({ path: `${OUT}/app-approve.png` })
await page.getByRole('button', { name: 'Record freeze order' }).click()
await page.getByText(/Dispatched/).first().waitFor({ timeout: 15000 })
await page.waitForTimeout(800)
await page.screenshot({ path: `${OUT}/app-dispatched.png` })
await page.getByRole('tab', { name: /Receipt/ }).click()
await page.getByRole('button', { name: 'Verify chain' }).click()
await page.getByText(/every link matches/).waitFor({ timeout: 10000 })
await page.waitForTimeout(600)
await page.screenshot({ path: `${OUT}/app-chain.png` })
await page.locator('details.connect > summary').click()
await page.waitForTimeout(700)
await page.screenshot({ path: `${OUT}/app-mcp.png` })
await ctx.close()
const c1 = await browser.newContext()
await c1.request.post(`${BASE}/api/demo/reset`)
await c1.close()
await browser.close()
mock.close()
console.log('wrote', OUT)
