// Screenshot of the Compare tab for one example (manual check, not part of the smoke test).
import { chromium } from 'playwright-core'
const BASE = process.env.BASE_URL || 'http://127.0.0.1:8080'
const browser = await chromium.launch({ executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe', headless: true })
const page = await (await browser.newContext({ viewport: { width: 1440, height: 1100 } })).newPage()
await page.goto(BASE)
await page.getByRole('button', { name: /Run the example alert/ }).click()
await page.waitForSelector('.chip.good', { timeout: 60000 })
await page.getByRole('tab', { name: 'Compare' }).click()
await page.locator('.panel').last().screenshot({ path: process.env.OUT || 'shot.png' })
await browser.close()
