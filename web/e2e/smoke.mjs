// Browser smoke test against a running app (API + built UI on one port) with the dataset loaded.
//
//   cd web && npm run build            # the API serves web/dist
//   uv run uvicorn chokepoint.api:app  # from the project root, with the database up
//   BASE_URL=http://localhost:8000 CHROME_PATH=/path/to/chrome npm run e2e
//
// Uses replay mode, so it makes no model calls. Failure states are produced by intercepting the API in the page.
import { existsSync } from 'node:fs'
import { chromium } from 'playwright-core'

const BASE = process.env.BASE_URL || 'http://localhost:8000'
const CHROME =
  process.env.CHROME_PATH ||
  ['C:/Program Files/Google/Chrome/Application/chrome.exe', '/usr/bin/google-chrome', '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'].find((p) =>
    existsSync(p),
  )

const browser = await chromium.launch({ executablePath: CHROME, headless: true })
const failures = []
const check = (ok, what) => {
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${what}`)
  if (!ok) failures.push(what)
}

async function open(viewport, intercept) {
  const ctx = await browser.newContext({ viewport })
  const page = await ctx.newPage()
  const problems = []
  page.on('pageerror', (e) => problems.push(e.message))
  page.on('console', (m) => m.type() === 'error' && problems.push(m.text()))
  if (intercept) await intercept(page)
  await page.goto(BASE + '/#/app', { waitUntil: 'networkidle' })
  return { page, problems }
}

// 1. First run: one click from the empty state to a verified plan, with nothing on the console.
{
  const { page, problems } = await open({ width: 1440, height: 900 })
  await page.getByRole('button', { name: /Run the example alert/ }).click()
  await page.waitForSelector('.chip.good', { timeout: 30000 })
  check(await page.getByText(/Verified: no route left/).isVisible(), 'example alert reaches a verified plan in one click')
  check((await page.locator('.hero').innerText()).includes('$'), 'the answer card shows the money at risk')
  await page.getByRole('button', { name: 'Copy account list' }).isVisible()
  check(problems.length === 0, `no console errors (${problems.join(' | ') || 'none'})`)
}

// 2. A custom alert cannot be replayed: the UI says so and runs the tools instead of failing.
{
  const { page } = await open({ width: 1440, height: 900 })
  await page.locator('#acct').fill('not-an-example:123')
  check(await page.getByText(/this custom alert uses Tools only/).isVisible(), 'custom alert falls back to Tools only with an explanation')
}

// 3. Database still loading: a status bar explains it and Investigate is disabled; it clears when ready.
{
  let ready = false
  const { page } = await open({ width: 1440, height: 900 }, (p) =>
    p.route('**/api/ready', (route) =>
      ready
        ? route.continue()
        : route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ ready: false, state: 'loading', message: 'x' }) }),
    ),
  )
  check(await page.getByText(/loading the transfer graph/).isVisible(), 'loading state is explained')
  check(await page.getByRole('button', { name: /^Investigate$/ }).isDisabled(), 'Investigate is disabled while the database loads')
  ready = true
  await page.getByText(/loading the transfer graph/).waitFor({ state: 'detached', timeout: 10000 })
  check(await page.getByRole('button', { name: /^Investigate$/ }).isEnabled(), 'the page becomes usable on its own once the database is ready')
}

// 4. No model key on the server: Live is disabled with a reason.
{
  const { page } = await open({ width: 1440, height: 900 }, (p) =>
    p.route('**/api/health', (route) =>
      route.fulfill({ contentType: 'application/json', body: JSON.stringify({ ok: true, version: 't', demo: true, live_available: false }) }),
    ),
  )
  check(await page.getByRole('button', { name: 'Live agent' }).isDisabled(), 'Live agent is disabled when the server has no key')
}

// 5. A server error while running is shown in words, not as a raw response.
{
  const { page } = await open({ width: 1440, height: 900 }, (p) =>
    p.route('**/api/runs', (route) =>
      route.fulfill({ status: 429, contentType: 'application/json', body: JSON.stringify({ error: { code: 'RATE_LIMITED', message: 'x' } }) }),
    ),
  )
  await page.getByRole('button', { name: /^Investigate$/ }).click()
  check(await page.getByText(/Too many requests/).waitFor({ timeout: 5000 }).then(() => true, () => false), 'a 429 is shown as a readable message')
}

// 6. Phone: every mode is reachable, the page does not scroll sideways, and the answer comes before the map.
{
  const { page } = await open({ width: 390, height: 844 })
  for (const name of ['Live agent', 'Replay', 'Tools only']) check(await page.getByRole('button', { name }).isVisible(), `phone: "${name}" is visible`)
  check(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), 'phone: no horizontal scroll')
  await page.getByRole('button', { name: /^Investigate$/ }).click()
  await page.waitForSelector('.chip.good', { timeout: 30000 })
  const answerTop = await page.locator('.hero').boundingBox()
  const mapTop = await page.locator('.canvas-wrap').boundingBox()
  check(answerTop.y < mapTop.y, 'phone: the answer card is above the map')
}

// 7. Audit, receipt and resume work in the default (replay) mode.
{
  const { page, problems } = await open({ width: 1440, height: 900 })
  await page.getByRole('button', { name: /Run the example alert/ }).click()
  await page.waitForSelector('.chip.good', { timeout: 30000 })
  await page.getByRole('tab', { name: 'Audit' }).click()
  await page.getByRole('button', { name: 'Ask the auditor' }).click()
  await page.getByText(/Auditor verdict: agree/).waitFor({ timeout: 30000 }).catch(() => undefined)
  check(await page.getByText(/Auditor verdict: agree/).isVisible(), 'the auditor records a verdict from a saved run')
  await page.getByRole('tab', { name: 'Receipt' }).click()
  await page.getByRole('button', { name: 'Open receipt for this plan' }).click()
  await page.getByText(/solver/).waitFor({ timeout: 10000 }).catch(() => undefined)
  check(await page.getByText(/Built freeze plan/).isVisible(), 'the receipt shows which tool produced the plan')
  await page.getByText('The fixed queries behind these tools').click()
  await page.getByText(/algo\.maxFlow/).first().waitFor({ timeout: 10000 }).catch(() => undefined)
  check(await page.getByText(/algo\.maxFlow/).first().isVisible(), 'the receipt tab shows the fixed queries, including algo.maxFlow')
  await page.reload({ waitUntil: 'networkidle' })
  await page.selectOption('#recent', { index: 1 })
  await page.getByText(/Resumed from a saved case/).waitFor({ timeout: 15000 }).catch(() => undefined)
  check(await page.getByText(/Resumed from a saved case/).isVisible(), 'a saved case resumes from the header menu')
  check(problems.length === 0, `no console errors (${problems.join(' | ') || 'none'})`)
}

// 9. Guided tour: offered on a first visit, walks the saved example to the end, leaves on Escape, and remembers it was seen.
{
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } })
  await ctx.addInitScript(() => Object.defineProperty(navigator, 'webdriver', { get: () => false })) // look like a person
  const page = await ctx.newPage()
  await page.goto(BASE + '/#/app')
  await page.waitForSelector('.tour-card.welcome', { timeout: 15000 })
  check(true, 'tour: the welcome card is offered on a first visit')
  for (let n = 0; n < 4; n++) await page.keyboard.press('Tab')
  check(await page.evaluate(() => !!document.activeElement?.closest('.welcome')), 'tour: Tab stays inside the welcome card')
  await page.getByRole('button', { name: 'Start the tour' }).click()
  for (let n = 0; n < 7; n++) {
    await page.waitForFunction(() => !document.querySelector('.tour-card .btn.primary')?.disabled, null, { timeout: 30000 })
    await page.waitForTimeout(450) // the spotlight glides to its target
    if (!(await page.locator('.tour-spot').count())) failures.push(`tour: step ${n + 1} has no spotlight, so its target moved or is missing`)
    await page.getByRole('button', { name: 'Next' }).click()
  }
  check(await page.getByRole('button', { name: 'Explore on my own' }).isVisible(), 'tour: eight steps reach the last card')
  await page.keyboard.press('Escape')
  check((await page.locator('.tour').count()) === 0, 'tour: Escape leaves it')
  check((await page.evaluate(() => localStorage.getItem('chokepoint.tour'))) === 'done', 'tour: a seen tour is remembered')
  await page.reload({ waitUntil: 'networkidle' })
  await page.waitForTimeout(1200)
  check((await page.locator('.tour').count()) === 0, 'tour: it does not come back on the next visit')
  await page.getByRole('button', { name: 'Tour', exact: true }).click()
  check((await page.locator('.tour-card').count()) === 1, 'tour: the Tour button reopens it')
  await ctx.close()
}

// 10. Landing page: it is the front door, its buttons reach the app and the tour, and it fits a phone.
{
  const ctx = await browser.newContext({ viewport: { width: 390, height: 844 } })
  const page = await ctx.newPage()
  await page.goto(BASE + '/', { waitUntil: 'networkidle' })
  check(await page.getByRole('heading', { level: 1, name: /Which accounts do you freeze/ }).isVisible(), 'landing: the first page is the landing page')
  check((await page.evaluate(() => document.documentElement.scrollWidth)) <= 390, 'landing: no horizontal scroll at 390 px')
  await page.getByRole('button', { name: 'Open the demo' }).first().click()
  await page.getByRole('button', { name: /Run the example alert/ }).waitFor({ timeout: 15000 })
  check(true, 'landing: "Open the demo" reaches the app')
  await page.goto(BASE + '/', { waitUntil: 'networkidle' })
  await page.getByRole('button', { name: /Take the one-minute tour/ }).click()
  await page.waitForSelector('.tour-card', { timeout: 20000 })
  check((await page.locator('.tour-card.welcome').count()) === 0, 'landing: "Take the tour" skips the welcome card and starts the steps')
  await ctx.close()
  // a visitor whose system is in light mode and who has never chosen a theme still gets the dark default
  const light = await browser.newContext({ viewport: { width: 1280, height: 800 }, colorScheme: 'light' })
  const p2 = await light.newPage()
  await p2.goto(BASE + '/', { waitUntil: 'networkidle' })
  check((await p2.evaluate(() => document.documentElement.getAttribute('data-theme'))) === 'dark', 'landing: dark by default even when the system is light')
  await light.close()
  // an older visit saved the theme as "system" under the old key: it must not keep that visitor on the light look
  const old = await browser.newContext({ viewport: { width: 1280, height: 800 }, colorScheme: 'light' })
  await old.addInitScript(() => localStorage.setItem('theme', 'system'))
  const p3 = await old.newPage()
  await p3.goto(BASE + '/#/app', { waitUntil: 'networkidle' })
  check((await p3.evaluate(() => document.documentElement.getAttribute('data-theme'))) === 'dark', 'landing: a theme saved by an older visit does not hold back the new look')
  await old.close()
}

// 11. Result column: the deliverable and the tab bar fit a laptop screen; the "after the freeze" figure appears only once verified.
{
  const ctx = await browser.newContext({ viewport: { width: 1366, height: 768 } })
  const page = await ctx.newPage()
  await page.goto(BASE + '/#/app', { waitUntil: 'networkidle' })
  await page.getByRole('button', { name: /Run the example alert/ }).waitFor()
  check((await page.locator('.after').count()) === 0, 'result: no "after the freeze" figure before a run')
  await page.getByRole('button', { name: /Run the example alert/ }).click()
  await page.waitForSelector('.chip.good', { timeout: 30000 })
  await page.waitForSelector('.after', { timeout: 5000 })
  check(true, 'result: the "after the freeze" figure appears once the plan is verified')
  await page.waitForTimeout(1500)
  const bar = await page.locator('[role=tablist]').boundingBox()
  check(!!bar && bar.y + bar.height <= 768, `result: the tab bar is on screen at 1366x768 without scrolling (bottom ${bar ? Math.round(bar.y + bar.height) : '?'})`)
  const accts = await page.locator('.acct-list li').count()
  check(accts > 0, 'result: the accounts to freeze are listed in the decision card')
  await ctx.close()
}

// 12. Human approval: refused until a person types a name; records an order bound to the checked plan; the model has no way to do it.
{
  const ctx = await browser.newContext({ viewport: { width: 1366, height: 768 } })
  const page = await ctx.newPage()
  await page.goto(BASE + '/#/app', { waitUntil: 'networkidle' })
  check((await page.getByRole('button', { name: /Approve a freeze order/ }).count()) === 0, 'approval: no approve button before a plan is checked')
  await page.getByRole('button', { name: /Run the example alert/ }).click()
  await page.waitForSelector('.chip.good', { timeout: 30000 })
  await page.getByRole('button', { name: /Approve a freeze order/ }).click()
  await page.waitForSelector('.gate li.ok', { timeout: 10000 })
  check((await page.locator('.gate li.ok').count()) >= 4 && (await page.locator('.gate li.no').count()) === 0, 'approval: the checks before approval are listed and passing, including the second check on the source graph')
  check(await page.getByRole('button', { name: 'Record freeze order' }).isDisabled(), 'approval: the order cannot be recorded without a name')
  await page.getByLabel('Your name').fill('Asha Verma')
  await page.getByRole('button', { name: 'Record freeze order' }).click()
  await page.getByText(/Freeze order fo-/).waitFor({ timeout: 10000 })
  check(await page.getByText(/bound to plan hash/).isVisible(), 'approval: the order is recorded and bound to the plan by a hash')
  check(await page.getByText(/No bank is called/).first().isVisible(), 'approval: the page says no bank is called')
  const toolReply = await page.evaluate(() => fetch('/api/tools/order_freeze', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' }).then((r) => r.status))
  check(toolReply === 404, 'approval: the model-facing tool route does not know it')
  await ctx.close()
}

// 8. Late data (demo mode only): banner, re-run only the affected findings, and a replay that no longer matches says
//    why and offers a reset. The demo data is reset afterwards.
{
  const { page } = await open({ width: 1440, height: 900 })
  const demo = await page.evaluate(() => fetch('/api/health').then((r) => r.json()).then((h) => h.demo))
  if (!demo) console.log('SKIP  late-data flow (DEMO_MODE=0)')
  else {
    await page.getByRole('button', { name: /Run the example alert/ }).click()
    await page.waitForSelector('.chip.good', { timeout: 30000 })
    await page.getByRole('button', { name: 'Simulate late transfers' }).click()
    await page.getByText(/Late transfers arrived/).waitFor({ timeout: 15000 })
    await page.getByRole('button', { name: 'Re-run affected findings' }).click()
    await page.getByText(/Old findings stay in the graph/).waitFor({ timeout: 60000 })
    check(await page.getByRole('tab', { name: 'Changes' }).isVisible(), 'late data: affected findings are re-run and listed under Changes')
    await page.getByRole('button', { name: /^Investigate$/ }).click()
    await page.getByRole('button', { name: 'Reset demo data' }).waitFor({ timeout: 15000 }).catch(() => undefined)
    check(await page.getByRole('button', { name: 'Reset demo data' }).isVisible(), 'late data: a replay that no longer matches offers a reset')
    await page.getByRole('button', { name: 'Reset demo data' }).click()
    await page.getByText(/Where can the money still go/).waitFor({ timeout: 15000 })
    check(true, 'late data: reset returns to the start screen')
  }
}

await browser.close()
if (failures.length) {
  console.error(`\n${failures.length} check(s) failed`)
  process.exit(1)
}
console.log('\nall checks passed')
