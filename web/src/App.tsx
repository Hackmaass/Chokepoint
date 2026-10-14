import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import Graph from './Graph'
import { api, buildCard, STEP_NAMES, usd, usd2, type Card, type CaseRow, type Example, type GraphView, type TraceEvent } from './api'

type Mode = 'live' | 'replay' | 'tools'
type Tab = 'steps' | 'compare' | 'receipt' | 'changes' | 'audit'

function loadTheme(): string {
  try {
    return localStorage.getItem('theme') || 'system'
  } catch {
    return 'system'
  }
}

function fmtSummary(s: Record<string, any>): string {
  if (!s) return ''
  if ('accounts' in s && Array.isArray(s.accounts)) return `${s.accounts.length} account${s.accounts.length === 1 ? '' : 's'} · ${usd(s.movable_before_usd_cents ?? 0)} movable`
  if ('max_movable_usd_cents' in s) return usd(s.max_movable_usd_cents)
  if ('verified' in s) return s.verified ? 'verified' : 'not verified'
  return `${s.accounts} accounts · ${s.perimeter_accounts} on the perimeter`
}

export default function App() {
  const [examples, setExamples] = useState<Example[]>([])
  const [recent, setRecent] = useState<CaseRow[]>([])
  const [account, setAccount] = useState('')
  const [alertTs, setAlertTs] = useState('')
  const [mode, setMode] = useState<Mode>('live')
  const [events, setEvents] = useState<TraceEvent[]>([])
  const [running, setRunning] = useState(false)
  const [error, setError] = useState('')
  const [view, setView] = useState<GraphView | null>(null)
  const [tab, setTab] = useState<Tab>('steps')
  const [showRule, setShowRule] = useState(false)
  const [theme, setTheme] = useState(loadTheme())
  const [receipt, setReceipt] = useState<Record<string, any> | null>(null)
  const [resumed, setResumed] = useState(false)
  const [resumedCard, setResumedCard] = useState<Card | null>(null)
  const [info, setInfo] = useState<Record<string, any> | null>(null)
  const [refresh, setRefresh] = useState<Record<string, any> | null>(null)
  const [demo, setDemo] = useState(false)
  const [busy, setBusy] = useState(false)
  const [audit, setAudit] = useState<{ checks?: any[]; verdict?: string; note?: string; blocking?: string[]; running?: boolean; error?: string } | null>(null)
  const source = useRef<EventSource | null>(null)

  useEffect(() => {
    api.examples().then((x) => {
      setExamples(x)
      if (x[0]) {
        setAccount(x[0].account)
        setAlertTs(x[0].alert_ts)
      }
    }).catch(() => setError('The API is not reachable. Start it with: uv run uvicorn chokepoint.api:app'))
    api.cases().then(setRecent).catch(() => undefined)
    api.health().then((h) => setDemo(h.demo)).catch(() => undefined)
  }, [])

  useEffect(() => {
    const root = document.documentElement
    if (theme === 'system') root.removeAttribute('data-theme')
    else root.setAttribute('data-theme', theme)
    try {
      localStorage.setItem('theme', theme)
    } catch {
      /* storage can be blocked */
    }
  }, [theme])

  const results = useMemo(
    () => events.filter((e) => e.type === 'tool_result').map((e) => ({ tool: e.tool!, result: e.result! })),
    [events],
  )
  const card: Card = useMemo(() => (resumedCard && events.length === 0 ? resumedCard : buildCard(results)), [results, resumedCard, events.length])
  const finalText = events.find((e) => e.type === 'final')?.text
  const cachedRun = events.some((e) => e.cached) || events.find((e) => e.type === 'card')?.mode === 'replay'
  const caseId = card.case?.case_id
  const planId = card.plan?.plan_id
  const verified = !!card.verification?.verified

  const checkInfo = useCallback((id: string) => {
    api.tool('resume_case', { case_id: id }).then((r) => setInfo(r.error ? null : r)).catch(() => undefined)
  }, [])

  useEffect(() => {
    if (caseId && !running) checkInfo(caseId)
  }, [caseId, running, checkInfo])

  useEffect(() => {
    if (!caseId) return
    api.graph(caseId, planId).then(setView).catch(() => undefined)
  }, [caseId, planId])

  const steps = useMemo(() => {
    const out: { tool: string; args: any; cached?: boolean; ms?: number; ok?: boolean; summary?: string; result?: any; thought?: string }[] = []
    let pendingThought = ''
    for (const e of events) {
      if (e.type === 'thought') pendingThought = e.text || ''
      if (e.type === 'tool_call') {
        out.push({ tool: e.tool!, args: e.args, cached: e.cached, thought: pendingThought })
        pendingThought = ''
      }
      if (e.type === 'tool_result') {
        const s = [...out].reverse().find((x) => x.tool === e.tool && x.ms === undefined)
        if (s) Object.assign(s, { ms: e.ms, ok: e.ok, summary: e.summary, result: e.result })
      }
    }
    return out
  }, [events])

  const push = useCallback((e: TraceEvent) => setEvents((prev) => [...prev, e]), [])

  async function runTools() {
    push({ type: 'start' })
    const seq: [string, () => Record<string, unknown>][] = [
      ['open_case', () => ({ account, alert_ts: alertTs })],
      ['flow', () => ({ case_id: acc.case_id })],
      ['freeze_plan', () => ({ case_id: acc.case_id })],
      ['verify_plan', () => ({ plan_id: acc.plan_id })],
    ]
    const acc: Record<string, any> = {}
    for (const [tool, args] of seq) {
      const a = args()
      push({ type: 'tool_call', tool, args: a })
      const t0 = performance.now()
      const result = await api.tool(tool, a)
      const ok = !result.error
      push({ type: 'tool_result', tool, ms: Math.round(performance.now() - t0), ok, summary: ok ? '' : `${result.error.code}: ${result.error.message}`, result })
      if (!ok) break
      Object.assign(acc, { case_id: result.case_id ?? acc.case_id, plan_id: result.plan_id ?? acc.plan_id })
      if (tool === 'open_case' && !result.has_route_to_perimeter) {
        push({ type: 'final', text: 'No route from the alerted account to the perimeter in this window. Widen the window or check the account.' })
        break
      }
    }
  }

  async function run() {
    source.current?.close()
    setEvents([])
    setView(null)
    setError('')
    setReceipt(null)
    setResumed(false)
    setResumedCard(null)
    setTab('steps')
    setRunning(true)
    try {
      if (mode === 'tools') {
        await runTools()
        setRunning(false)
        return
      }
      const task = `Account ${account} was alerted at ${alertTs}. Investigate and recommend which accounts to freeze.`
      const { run_id } = await api.startRun(task, mode)
      const es = new EventSource(`/api/runs/${run_id}/events`)
      source.current = es
      es.onmessage = (m) => push(JSON.parse(m.data) as TraceEvent)
      es.addEventListener('end', () => {
        es.close()
        setRunning(false)
        api.cases().then(setRecent).catch(() => undefined)
      })
      es.onerror = () => {
        es.close()
        setRunning(false)
      }
    } catch (e) {
      setError(String(e).replace(/^Error: /, ''))
      setRunning(false)
    }
  }

  async function resume(id: string, goTo: Tab = 'receipt') {
    if (!id) return
    source.current?.close()
    setEvents([])
    setView(null)
    setReceipt(null)
    setError('')
    const r = await api.tool('resume_case', { case_id: id })
    if (r.error) {
      setError(`${r.error.code}: ${r.error.message}`)
      return
    }
    setAccount(r.alerted_account)
    setAlertTs(r.alert_ts)
    const regionF = r.findings.find((f: any) => f.kind === 'region')
    const planF = r.findings.find((f: any) => f.kind === 'freeze_plan')
    const verF = r.findings.find((f: any) => f.kind === 'verification')
    const flowF = r.findings.find((f: any) => f.kind === 'flow')
    const synth: { tool: string; result: Record<string, any> }[] = [
      { tool: 'open_case', result: { case_id: id, alerted_account: r.alerted_account, alert_ts: r.alert_ts, window_days: r.window_days, perimeter_hops: r.perimeter_hops, data_version: r.case_data_version, ...(regionF?.summary ?? {}), has_route_to_perimeter: regionF?.summary?.has_route_to_perimeter ?? false } },
    ]
    if (flowF) synth.push({ tool: 'flow', result: { max_movable_usd_cents: flowF.summary.max_movable_usd_cents } })
    if (planF) {
      const ex = await api.tool('explain_finding', { finding_id: planF.finding_id })
      synth.push({ tool: 'freeze_plan', result: { ...ex.result, plan_id: planF.plan_id, finding_id: planF.finding_id } })
      if (verF) synth.push({ tool: 'verify_plan', result: { ...verF.summary, plan_id: planF.plan_id, method: 'see receipt', perimeter_reachable_before: 0, perimeter_reachable_after: 0 } })
    }
    setResumedCard(buildCard(synth))
    setResumed(true)
    setInfo(r)
    setTab(goTo)
  }

  async function simulateLate() {
    if (!caseId) return
    setBusy(true)
    try {
      await api.post(`/api/demo/late-transfers/${caseId}`)
      checkInfo(caseId)
    } catch (e) {
      setError(String(e).replace(/^Error: /, ''))
    }
    setBusy(false)
  }

  async function rerunAffected() {
    if (!caseId) return
    setBusy(true)
    const r = await api.tool('refresh_case', { case_id: caseId })
    if (r.error) setError(`${r.error.code}: ${r.error.message}`)
    else {
      setRefresh(r)
      await resume(caseId, 'changes')
    }
    setBusy(false)
  }

  async function askAuditor() {
    if (!plan || !caseId) return
    setAudit({ running: true })
    setTab('audit')
    try {
      if (mode === 'tools') {
        const r = await api.tool('audit_checks', { plan_id: plan.plan_id })
        setAudit(r.error ? { error: `${r.error.code}: ${r.error.message}` } : { checks: r.checks, blocking: r.blocking_failed })
        return
      }
      const { run_id } = await api.startRun(`Audit plan ${plan.plan_id} for case ${caseId}.`, mode, 'auditor')
      const es = new EventSource(`/api/runs/${run_id}/events`)
      es.onmessage = (m) => {
        const e = JSON.parse(m.data) as TraceEvent
        if (e.type === 'tool_result' && e.tool === 'audit_checks' && e.result)
          setAudit((a) => ({ ...a, checks: e.result!.checks, blocking: e.result!.blocking_failed, running: true }))
        if (e.type === 'tool_result' && e.tool === 'submit_audit' && e.result)
          setAudit((a) => ({ ...a, ...(e.result!.error ? { error: `${e.result!.error.code}: ${e.result!.error.message}` } : { verdict: e.result!.verdict, note: e.result!.note }) }))
        if (e.type === 'error') setAudit((a) => ({ ...a, error: e.message, running: false }))
      }
      es.addEventListener('end', () => {
        es.close()
        setAudit((a) => ({ ...a, running: false }))
      })
      es.onerror = () => {
        es.close()
        setAudit((a) => ({ ...a, running: false }))
      }
    } catch (e) {
      setAudit({ error: String(e).replace(/^Error: /, '') })
    }
  }

  async function loadReceipt() {
    if (!card.plan) return
    setReceipt(await api.tool('explain_finding', { finding_id: card.plan.finding_id }))
  }

  const movable = card.movable_usd_cents ?? card.plan?.before_usd_cents
  const plan = card.plan
  const noRoute = card.case && !card.case.has_route_to_perimeter
  const verifiedLine = card.verification
  const exSel = examples.findIndex((x) => x.account === account && x.alert_ts === alertTs)

  return (
    <div className="app">
      <header className="header">
        <span className="brand">Chokepoint</span>
        <div className="field" style={{ gap: 0 }}>
          <label className="sr-only" htmlFor="recent">Saved cases</label>
          <select id="recent" className="input" style={{ height: 32 }} value="" onChange={(e) => resume(e.target.value)}>
            <option value="">Resume a saved case…</option>
            {recent.map((c) => (
              <option key={c.case_id} value={c.case_id}>{c.alerted_account} · {c.alert_ts} · {c.case_id}</option>
            ))}
          </select>
        </div>
        <span className="spacer" />
        <div className="seg" role="group" aria-label="How to run">
          {(['live', 'replay', 'tools'] as Mode[]).map((m) => (
            <button key={m} aria-pressed={mode === m} onClick={() => setMode(m)}>
              {m === 'live' ? 'Live agent' : m === 'replay' ? 'Replay' : 'Tools only'}
            </button>
          ))}
        </div>
        <span className="badge">Synthetic data</span>
        <button className="btn small" onClick={() => setTheme(theme === 'dark' ? 'light' : 'dark')} aria-label="Toggle light and dark theme">
          {theme === 'dark' ? 'Light' : 'Dark'}
        </button>
      </header>

      <main className="main">
        <section className="left" aria-label="Case and graph">
          <div className="panel">
            <div className="row">
              <div className="field">
                <label htmlFor="ex">Example alert</label>
                <select id="ex" className="input" value={exSel} onChange={(e) => { const x = examples[Number(e.target.value)]; if (x) { setAccount(x.account); setAlertTs(x.alert_ts) } }}>
                  {exSel < 0 && <option value={-1}>Custom</option>}
                  {examples.map((x, i) => (
                    <option key={x.account} value={i}>{x.account} · {x.alert_ts} · {usd(x.movable_usd_cents)} movable</option>
                  ))}
                </select>
              </div>
              <div className="field">
                <label htmlFor="acct">Alerted account (bank:account)</label>
                <input id="acct" className="input mono" value={account} onChange={(e) => setAccount(e.target.value)} />
              </div>
              <div className="field" style={{ flex: '0 1 190px' }}>
                <label htmlFor="ts">Alert time (UTC)</label>
                <input id="ts" className="input mono" value={alertTs} onChange={(e) => setAlertTs(e.target.value)} />
              </div>
              <button className="btn primary" disabled={running || !account || !alertTs} onClick={run}>
                {running ? 'Investigating…' : mode === 'tools' ? 'Open case and build plan' : 'Investigate'}
              </button>
            </div>
            {mode === 'replay' && <p className="note">Replay serves saved agent runs. If this alert has none, run it live once or use Tools only.</p>}
            {error && <p className="error" role="alert">{error}</p>}
          </div>

          <div className="panel canvas-wrap">
            {view ? (
              <Graph view={view} showRule={showRule} verified={verified} theme={theme} />
            ) : (
              <div className="canvas-empty">
                <h2>Open a case to see where the money can go.</h2>
                <p>An alerted account is one flagged for holding stolen money that has already started moving. Pick an example alert above, or enter an account and a time, then investigate.</p>
              </div>
            )}
          </div>

          <div>
            <div className="legend" aria-label="Legend">
              <span><i className="sw diamond" /> alerted account (already frozen)</span>
              <span><i className="sw square" /> perimeter</span>
              <span><i className="sw circle" /> freeze</span>
              <span><i className="sw ring" /> rule-of-thumb pick</span>
              <span><i className="sw line" /> route that can carry money</span>
              {view?.truncated && <span>Showing {view.shown_accounts} of {view.total_accounts} accounts</span>}
            </div>
            <p className="note">
              {card.case ? `Transfers in the ${card.case.window_days} days before the alert · ${card.case.perimeter_hops}-hop perimeter · data version ${card.case.data_version}` : 'Static view of past transfers; it does not know what happens after the alert.'}
              {demo && caseId && (
                <button className="btn small" style={{ marginLeft: 16 }} disabled={busy} onClick={simulateLate}>Simulate late transfers</button>
              )}
              {view && card.plan && (
                <label style={{ marginLeft: 16 }}>
                  <input type="checkbox" checked={showRule} onChange={(e) => setShowRule(e.target.checked)} /> Show the rule-of-thumb picks
                </label>
              )}
            </p>
          </div>
        </section>

        <aside className="right" aria-label="Decision">
          <div className="panel" aria-live="polite">
            {movable !== undefined ? (
              <>
                <h1 className="hero">
                  {usd(movable)}
                  <small>can move along these routes · route capacity from past transfers, not a forecast</small>
                </h1>
                {plan && (
                  <>
                    <p className="card-line">Freeze {plan.chokepoint.count} account{plan.chokepoint.count === 1 ? '' : 's'} to cut these routes</p>
                    {verifiedLine ? (
                      <span className={`chip ${verifiedLine.verified ? 'good' : 'bad'}`}>
                        <span className="ico" aria-hidden>{verifiedLine.verified ? '✓' : '✕'}</span>
                        {verifiedLine.verified ? `Verified: no route left to the ${plan.perimeter_hops}-hop perimeter` : 'Not verified: routes remain'}
                      </span>
                    ) : running ? (
                      <span className="chip"><span className="ico" aria-hidden>…</span> Verifying</span>
                    ) : (
                      <span className="chip warn"><span className="ico" aria-hidden>!</span> Not verified yet</span>
                    )}
                    {verifiedLine && <p className="note">{verifiedLine.method}</p>}
                    {!plan.actionable && (
                      <div className="banner" role="note"><span className="ico" aria-hidden>!</span><span>This cut needs {plan.chokepoint.count} accounts, more than a team can act on. Try a smaller perimeter or a shorter window.</span></div>
                    )}
                  </>
                )}
              </>
            ) : noRoute ? (
              <div className="banner" role="note"><span className="ico" aria-hidden>!</span><span>No route to the perimeter in this window. Widen the window or check the account.</span></div>
            ) : (
              <>
                <h1 className="hero" style={{ color: 'var(--ink-secondary)' }}>—<small>Nothing run yet.</small></h1>
              </>
            )}
            {finalText && (
              <p className="narrative">
                {finalText}
                <span className="note" style={{ display: 'block' }}>
                  {cachedRun ? 'Replay: this is a saved run, not a live response. ' : ''}The model writes the words; every number comes from the tools.
                </span>
              </p>
            )}
            {resumed && !refresh && <p className="note">Resumed from a saved case; nothing was recomputed.</p>}
            {info?.has_newer_data && (
              <div className="banner" role="note">
                <span className="ico" aria-hidden>!</span>
                <span>
                  Late transfers arrived (data version {info.latest_data_version}). Results saved at version {info.case_data_version} may be out of date.{' '}
                  <button className="btn small" disabled={busy} onClick={rerunAffected}>Re-run affected findings</button>
                </span>
              </div>
            )}
          </div>

          <div className="panel">
            <div className="tabs" role="tablist">
              {(['steps', 'compare', 'receipt', ...(plan ? ['audit'] : []), ...(refresh ? ['changes'] : [])] as Tab[]).map((t) => (
                <button key={t} role="tab" aria-selected={tab === t} onClick={() => setTab(t)}>
                  {t === 'steps' ? 'Steps' : t === 'compare' ? 'Compare' : t === 'receipt' ? 'Receipt' : t === 'audit' ? 'Audit' : 'Changes'}
                </button>
              ))}
            </div>
            <div className="tabpanel">
              {tab === 'steps' && (
                <div aria-live="polite">
                  {steps.length === 0 && <p className="note">Steps appear here as the agent works. Each one is a tool run stored in the graph.</p>}
                  {steps.map((s, i) => (
                    <div key={i}>
                      {s.thought && <div className="decision"><strong>Agent note:</strong> {s.thought}</div>}
                      <details className="step">
                        <summary>
                          <span aria-hidden>{s.ms === undefined ? '…' : s.ok ? '✓' : '✕'}</span>
                          <span className="name">{STEP_NAMES[s.tool] ?? s.tool}{s.cached ? ' (saved)' : ''}</span>
                          <span className="ms">{s.ms === undefined ? 'running' : `${(s.ms / 1000).toFixed(1)} s`}</span>
                        </summary>
                        <pre>{JSON.stringify(s.args, null, 1)}{'\n'}{s.summary}</pre>
                      </details>
                    </div>
                  ))}
                </div>
              )}
              {tab === 'compare' && (
                <div>
                  {!plan ? (
                    <p className="note">Build a freeze plan to compare it with the rule of thumb.</p>
                  ) : (
                    <>
                      <table>
                        <thead><tr><th>Same number of accounts</th><th className="num">Chokepoint</th><th className="num">Rule of thumb</th></tr></thead>
                        <tbody>
                          <tr><td>Accounts frozen</td><td className="num">{plan.chokepoint.count}</td><td className="num">{plan.rule_of_thumb.count}</td></tr>
                          <tr><td>Money still able to move</td><td className="num">{usd2(plan.chokepoint.residual_movable_usd_cents)}</td><td className="num">{usd2(plan.rule_of_thumb.residual_movable_usd_cents)}</td></tr>
                          <tr><td>Routes left to the perimeter</td><td className="num">{plan.chokepoint.routes_left ? 'yes' : 'none'}</td><td className="num">{plan.rule_of_thumb.routes_left ? 'yes' : 'none'}</td></tr>
                          <tr><td>Frozen accounts the money can pass through</td><td className="num">{plan.chokepoint.on_a_route_to_perimeter} of {plan.chokepoint.count}</td><td className="num">{plan.rule_of_thumb.on_a_route_to_perimeter} of {plan.rule_of_thumb.count}</td></tr>
                          {(plan.chokepoint.baseline_activity_transfers > 0 || plan.rule_of_thumb.baseline_activity_transfers > 0) && (
                            <tr><td>Everyday activity of those accounts (transfers)</td><td className="num">{plan.chokepoint.baseline_activity_transfers}</td><td className="num">{plan.rule_of_thumb.baseline_activity_transfers}</td></tr>
                          )}
                        </tbody>
                      </table>
                      <p className="note">The rule of thumb freezes the accounts the alerted account paid most recently. Held-out evaluation on 124 synthetic case-times: the two block about the same share of the money that had already escaped; Chokepoint's frozen accounts were more often part of the laundering (+19 points of precision) and it proves no route is left.</p>
                      <table>
                        <thead><tr><th>Chokepoint plan: accounts to freeze</th><th>Role</th></tr></thead>
                        <tbody>
                          {plan.chokepoint.accounts.map((a) => (
                            <tr key={a}><td className="mono">{a}</td><td>{view?.nodes.find((n) => n.id === a)?.depth ?? '?'} hop(s) from the alerted account</td></tr>
                          ))}
                        </tbody>
                      </table>
                    </>
                  )}
                </div>
              )}
              {tab === 'audit' && plan && (
                <div aria-live="polite">
                  <p className="note">A second agent, the auditor, sees only the case and plan stored in the graph, not the investigator's messages. It runs deterministic checks and records a verdict; code refuses an "agree" while a blocking check fails.</p>
                  <button className="btn small" disabled={audit?.running} onClick={askAuditor}>{audit?.running ? 'Auditing…' : mode === 'tools' ? 'Run audit checks' : 'Ask the auditor'}</button>
                  {audit?.error && <p className="error" role="alert">{audit.error}</p>}
                  {audit?.checks && (
                    <table style={{ marginTop: 12 }}>
                      <thead><tr><th>Check</th><th>Result</th></tr></thead>
                      <tbody>
                        {audit.checks.map((c: any) => (
                          <tr key={c.check}>
                            <td>{c.check.replace(/_/g, ' ')}{c.blocking ? ' (blocking)' : ''}<div className="note">{c.detail}</div></td>
                            <td>{c.passed ? '✓ passed' : '✕ failed'}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  )}
                  {audit?.verdict && (
                    <div className="decision" style={{ marginTop: 12 }}>
                      <strong>Auditor verdict: {audit.verdict.replace('_', ' ')}.</strong> {audit.note}
                      <div className="note">Recorded in the graph as a finding; it appears when this case is resumed.</div>
                    </div>
                  )}
                </div>
              )}
              {tab === 'changes' && refresh && (
                <div>
                  <p className="card-line">{refresh.summary}</p>
                  <p className="note">{refresh.new_transfers_in_scope} new transfer(s) touched accounts these findings had read. Everything else was carried forward without re-running.</p>
                  <table>
                    <thead><tr><th>Finding</th><th>Before</th><th>After</th></tr></thead>
                    <tbody>
                      {refresh.changes.map((c: any, i: number) => (
                        <tr key={i}>
                          <td>{c.kind === 'freeze_plan' ? 'Freeze plan' : c.kind === 'flow' ? 'Money flow' : c.kind === 'region' ? 'Region' : 'Verification'}</td>
                          <td className="mono">{fmtSummary(c.before)}</td>
                          <td className="mono">{c.after ? fmtSummary(c.after) : c.note}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                  <p className="note">Old findings stay in the graph, marked superseded and linked to their replacements.</p>
                </div>
              )}
              {tab === 'receipt' && (
                <div>
                  {!plan ? (
                    <p className="note">Every result is stored as a receipt: which run produced it, with what parameters, on which data version. Build a plan, then open its receipt.</p>
                  ) : (
                    <>
                      <button className="btn small" onClick={loadReceipt}>Open receipt for this plan</button>
                      {receipt && (
                        <>
                          <table style={{ marginTop: 12 }}>
                            <tbody>
                              <tr><th>Finding</th><td className="mono">{receipt.finding_id}</td></tr>
                              <tr><th>Produced by</th><td>{STEP_NAMES[receipt.tool] ?? receipt.tool} · solver {receipt.solver}</td></tr>
                              <tr><th>Parameters</th><td className="mono">{JSON.stringify(receipt.parameters)}</td></tr>
                              <tr><th>Data version</th><td>{receipt.data_version}</td></tr>
                              <tr><th>Accounts read</th><td>{receipt.accounts_read}</td></tr>
                            </tbody>
                          </table>
                          <button className="btn small" style={{ marginTop: 8 }} onClick={() => navigator.clipboard?.writeText(JSON.stringify(receipt, null, 2))}>Copy receipt</button>
                        </>
                      )}
                    </>
                  )}
                </div>
              )}
            </div>
          </div>
        </aside>
      </main>
      <footer className="footer">Synthetic data for demonstration. A freeze plan is a recommendation, not an instruction.</footer>
    </div>
  )
}
