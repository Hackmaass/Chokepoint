// Screenshots of the main states for a visual review (manual; not part of the smoke test).
import { chromium } from 'playwright-core'
const BASE = process.env.BASE_URL || 'http://127.0.0.1:8080'
const OUT = process.env.OUT
const browser = await chromium.launch({ executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe', headless: true })
async function run(name, viewport, scheme) {
  const ctx = await browser.newContext({ viewport, colorScheme: scheme })
  const page = await ctx.newPage()
  await page.goto(BASE)
  await page.waitForTimeout(800)
  await page.screenshot({ path: `${OUT}/${name}-empty.png`, fullPage: true })
  await page.getByRole('button', { name: /Run the example alert/ }).click()
  await page.waitForSelector('.chip.good', { timeout: 60000 })
  await page.waitForTimeout(1500)
  await page.screenshot({ path: `${OUT}/${name}-result.png`, fullPage: true })
  await ctx.close()
}
await run('desk-light', { width: 1440, height: 900 }, 'light')
await run('desk-dark', { width: 1440, height: 900 }, 'dark')
await run('phone', { width: 390, height: 844 }, 'light')
await browser.close()
