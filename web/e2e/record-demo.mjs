// Records silent footage of the demo beats (docs/demo-script.md) in Replay mode: no model calls. You narrate over it.
// Each beat starts at the timestamp in the script and holds until the next one (about 2:50 in total).
//   BASE_URL=http://127.0.0.1:8080 OUT_DIR=<folder outside the repo> node e2e/record-demo.mjs
import { existsSync } from 'node:fs'
import { chromium } from 'playwright-core'

const BASE = process.env.BASE_URL || 'http://127.0.0.1:8080'
const OUT = process.env.OUT_DIR || './demo-footage'
const CHROME = process.env.CHROME_PATH || ['C:/Program Files/Google/Chrome/Application/chrome.exe', '/usr/bin/google-chrome'].find((p) => existsSync(p))
const ACCOUNT = '021174:800737690'

const browser = await chromium.launch({ executablePath: CHROME, headless: true })
const reset = async () => {
  const ctx = await browser.newContext()
  const r = await ctx.request.post(`${BASE}/api/demo/reset`).catch(() => null)
  await ctx.close()
  return r && r.ok()
}
if (!(await reset())) console.log('note: /api/demo/reset not available (DEMO_MODE=0?); recording anyway')

const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, recordVideo: { dir: OUT, size: { width: 1440, height: 900 } } })
// a small dot that follows the pointer, so a viewer can see what is being clicked
await ctx.addInitScript(() => {
  addEventListener('mousemove', (e) => {
    let d = document.getElementById('rec-cursor')
    if (!d) {
      d = document.createElement('div')
      d.id = 'rec-cursor'
      d.style.cssText = 'position:fixed;width:18px;height:18px;margin:-9px 0 0 -9px;border-radius:50%;background:rgba(220,60,40,.55);border:2px solid #fff;box-shadow:0 0 0 1px rgba(0,0,0,.4);pointer-events:none;z-index:2147483647;transition:left .08s,top .08s'
      document.documentElement.appendChild(d)
    }
    d.style.left = `${e.clientX}px`
    d.style.top = `${e.clientY}px`
  }, true)
})
const page = await ctx.newPage()
const t0 = Date.now()
const until = async (sec) => page.waitForTimeout(Math.max(0, sec * 1000 - (Date.now() - t0)))
const tail = (p, ms = 15000) => p.waitFor({ timeout: ms }).catch(() => undefined)

await page.goto(BASE + '/#/app', { waitUntil: 'networkidle' })
await until(15) // 0:00 empty state

const opts = await page.locator('#ex option').allInnerTexts()
await page.selectOption('#ex', { index: opts.findIndex((t) => t.includes(ACCOUNT)) })
await page.waitForTimeout(800)
await page.getByRole('button', { name: /^Investigate$/ }).click() // 0:15 steps appear
await page.waitForSelector('.chip.good', { timeout: 30000 })
await until(40)

await page.getByRole('button', { name: 'Play routes' }).click() // 0:40 routes appear hop by hop, then fade at the check
await until(60)

await page.locator('.chip.good').hover() // 1:00 the verified chip and its method note
await until(80)

await page.getByRole('tab', { name: 'Compare' }).click() // 1:20
await until(110)

await page.getByRole('tab', { name: 'Audit' }).click() // 1:50
await page.waitForTimeout(1500)
await page.getByRole('button', { name: 'Ask the auditor' }).click()
await tail(page.getByText(/Auditor verdict/))
await until(130)

if (await page.getByRole('button', { name: 'Simulate late transfers' }).count()) { // 2:10
  await page.getByRole('button', { name: 'Simulate late transfers' }).click()
  await tail(page.getByText(/Late transfers arrived/))
  await page.waitForTimeout(3000)
  await page.getByRole('button', { name: 'Re-run affected findings' }).click()
  await tail(page.getByRole('tab', { name: 'Changes' }), 60000)
  await page.getByRole('tab', { name: 'Changes' }).click()
}
await until(155)

await page.getByRole('tab', { name: 'Receipt' }).click() // 2:35
await page.waitForTimeout(1500)
const open = page.getByRole('button', { name: 'Open receipt for this plan' })
if (await open.count()) await open.click()
await tail(page.getByText(/solver/), 10000)
await until(170)

const video = page.video()
await ctx.close()
const file = await video.path()
await reset()
await browser.close()
console.log('footage:', file)
