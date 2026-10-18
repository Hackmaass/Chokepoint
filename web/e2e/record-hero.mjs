// Records the hero clip for the README: alert -> agent steps -> cut on the map -> checked on a copy -> approve -> dispatched -> chain verified.
// The GitHub step runs against a local mock (start the app with OPS_GITHUB_API=http://127.0.0.1:9101 OPS_GITHUB_TOKEN=demo).
//   BASE_URL=http://127.0.0.1:8001 OUT_DIR=<folder> node e2e/record-hero.mjs   then convert with ffmpeg (see docs/deploy.md)
import { existsSync } from 'node:fs'
import { createServer } from 'node:http'
import { chromium } from 'playwright-core'

const BASE = process.env.BASE_URL || 'http://127.0.0.1:8001'
const OUT = process.env.OUT_DIR || './hero-footage'
const CHROME = process.env.CHROME_PATH || ['C:/Program Files/Google/Chrome/Application/chrome.exe', '/usr/bin/google-chrome'].find((p) => existsSync(p))
const issues = []
const mock = createServer((req, res) => {
  let b = ''
  req.on('data', (c) => (b += c))
  req.on('end', () => {
    res.setHeader('Content-Type', 'application/json')
    if (req.method === 'GET') return res.end(JSON.stringify(issues))
    issues.push({ title: JSON.parse(b).title, html_url: `https://github.com/Hackmaass/chokepoint-ops/issues/${issues.length + 1}` })
    res.statusCode = 201
    res.end(JSON.stringify(issues.at(-1)))
  })
})
await new Promise((r) => mock.listen(9101, '127.0.0.1', r))
const browser = await chromium.launch({ executablePath: CHROME, headless: true })
const ctx0 = await browser.newContext()
await ctx0.request.post(`${BASE}/api/demo/reset`)
await ctx0.close()
const ctx = await browser.newContext({ viewport: { width: 1280, height: 800 }, recordVideo: { dir: OUT, size: { width: 1280, height: 800 } } })
const page = await ctx.newPage()
await page.goto(BASE + '/#/app', { waitUntil: 'networkidle' })
await page.waitForTimeout(1500)
await page.getByRole('button', { name: /Run the example alert/ }).click()
await page.waitForSelector('.chip.good', { timeout: 30000 })
await page.waitForSelector('.after', { timeout: 5000 })
await page.waitForTimeout(2500)
await page.getByRole('button', { name: 'Play routes' }).click().catch(() => undefined)
await page.waitForTimeout(4500)
await page.getByRole('button', { name: /Approve a freeze order/ }).click()
await page.waitForSelector('.gate li.ok', { timeout: 10000 })
await page.waitForTimeout(1500)
await page.getByLabel('Your name').fill('A. Rao')
await page.waitForTimeout(600)
await page.getByRole('button', { name: 'Record freeze order' }).click()
await page.getByText(/Dispatched/).first().waitFor({ timeout: 15000 })
await page.waitForTimeout(3000)
await page.getByRole('tab', { name: /Receipt/ }).click()
await page.getByRole('button', { name: 'Verify chain' }).click()
await page.getByText(/every link matches/).waitFor({ timeout: 10000 })
await page.waitForTimeout(3000)
await ctx.close()
await browser.close()
mock.close()
console.log('issues filed on the mock:', issues.length)
