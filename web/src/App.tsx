import { Tour, TourWelcome, type TourStep } from './Tour'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import Graph from './Graph'
import { api, buildCard, errorText, openRunStream, STEP_NAMES, usd, usd2, type Card, type CaseRow, type Example, type GraphView, type Ready, type TraceEvent } from './api'

type Mode = 'live' | 'replay' | 'tools'
type Tab = 'steps' | 'compare' | 'receipt' | 'changes' | 'audit'

const TOUR_KEY = 'chokepoint.tour'
const TOUR: TourStep[] = [
  { id: 'alert', target: 'alert', title: 'Start from an alert', body: "An alert names one account that held stolen money and the time it was flagged. These examples come from IBM's synthetic transfers. Replay plays a saved agent run, so it takes no model call." },
  { id: 'map', target: 'map', needsResult: true, title: 'Every route out', body: 'The diamond is the alerted account and the green squares are the perimeter, three hops out. Lines are the transfers of the 7 days before the alert. Dashed lines are routes the plan has cut; Play routes shows them hop by hop.' },
  { id: 'hero', target: 'hero', needsResult: true, title: 'How much is at risk', body: 'The most money that could still reach the perimeter along these transfers. It is a ceiling worked out from past transfers, not a forecast.' },
  { id: 'plan', target: 'plan', needsResult: true, title: 'The accounts to freeze', body: 'The cheapest set of accounts that cuts every route. The amount beside each is what it received from the alerted account’s downstream network, an upper bound on what a hold would have to cover.' },
  { id: 'verified', target: 'verified', needsResult: true, title: 'A check on a copy', body: 'Chokepoint deletes those accounts from a copy of the graph and searches again. Green means no route to the perimeter is left in the recorded transfers. It is a consistency check on the same data, not proof about the real world. The figure beneath is what could still reach the perimeter afterwards.' },
  { id: 'timing', target: 'timing', needsResult: true, title: 'Does time order change it?', body: "Money cannot go back in time. This line keeps only routes whose transfers happen in order. In three of the eight examples no route survives, so try the others." },
  { id: 'tabs', target: 'tabs', needsResult: true, title: 'Compare, receipt, audit', body: 'Compare sets the plan beside a same-size rule of thumb. Receipt shows the tool run behind each result. Audit lets a second agent, which sees only the graph, record a verdict.' },
  { id: 'done', title: 'That is the loop', body: 'Pick another alert above, or use Tools only to run the same steps without the agent. Everything here is synthetic, and a plan is a recommendation, not an instruction.' },
]

function loadTheme(): string {
  try {
    return localStorage.getItem('chokepoint.theme') || 'dark'
  } catch {
    return 'dark'
  }
}

function Icon({ kind }: { kind: 'check' | 'cross' }) {
  return (
    <svg viewBox="0 0 12 12" width="12" height="12" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      {kind === 'check' ? <path d="M2.5 6.4l2.4 2.4 4.6-5" /> : <path d="M3 3l6 6M9 3l-6 6" />}
    </svg>
  )
}

function fmtSummary(s: Record<string, any>): string {
  if (!s) return ''
  if ('accounts' in s && Array.isArray(s.accounts)) return `${s.accounts.length} account${s.accounts.length === 1 ? '' : 's'} · ${usd(s.movable_before_usd_cents ?? 0)} movable`
  if ('max_movable_usd_cents' in s) return usd(s.max_movable_usd_cents)
  if ('verified' in s) return s.verified ? 'verified' : 'not verified'
  return `${s.accounts} accounts · ${s.perimeter_accounts} on the perimeter`
}

const MODE_NOTES: Record<string, string> = {
  live: 'The language model chooses the tools and writes the summary. Takes about a minute.',
  replay: 'Plays back a saved agent run for this example alert: instant, no model call.',
  tools: 'Runs the same four tools directly, with no language model: instant and deterministic.',
}

function friendlyRunError(m: string): string {
  if (m.startsWith('no recorded answer'))
    return 'There is no saved run that matches the current data. This happens for a custom alert, or after simulated late transfers have changed the data. Use Tools only, or reset the demo data.'
  return m
}

/** Counts a dollar figure from one value to another once; shows the end value straight away under reduced motion. */
function Countdown({ from, to }: { from: number; to: number }) {
  const [v, setV] = useState(from)
  useEffect(() => {
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches || from === to) {
      setV(to)
      return
    }
    let raf = 0
    const t0 = performance.now()
    const tick = (t: number) => {
      const k = Math.min(1, (t - t0) / 1100)
      setV(Math.round(from + (to - from) * (1 - Math.pow(1 - k, 3))))
      if (k < 1) raf = window.requestAnimationFrame(tick)
    }
    raf = window.requestAnimationFrame(tick)
    return () => window.cancelAnimationFrame(raf)
  }, [from, to])
  return <>{usd(v)}</>
}

/** The fixed Cypher behind each tool, fetched when opened. The agent never writes a query; these are the ones the tools run. */
function QueriesPanel() {
  const [qs, setQs] = useState<{ tool: string; name: string; purpose: string; cypher: string }[] | null>(null)
  const [failed, setFailed] = useState(false)
  return (
    <details className="how-checked queries" onToggle={(e) => {
      if ((e.currentTarget as HTMLDetailsElement).open && !qs) {
        fetch('/api/queries').then((r) => r.json()).then((d) => setQs(d.queries)).catch(() => setFailed(true))
      }
    }}>
      <summary>The fixed queries behind these tools</summary>
      {failed && <p className="note">Could not load the queries.</p>}
      {qs?.map((q) => (
        <div key={q.name} className="query">
          <p className="note"><strong>{q.name}</strong> · {STEP_NAMES[q.tool] ?? q.tool}. {q.purpose}</p>
          <pre className="mono">{q.cypher}</pre>
        </div>
      ))}
    </details>
  )
}

export default function App({ autoTour = false }: { autoTour?: boolean }) {
  const [examples, setExamples] = useState<Example[]>([])
  const [recent, setRecent] = useState<CaseRow[]>([])
  const [account, setAccount] = useState('')
  const [tour, setTour] = useState<'off' | 'welcome' | 'steps'>('off')
  const [pendingTourRun, setPendingTourRun] = useState(false)
  const tourButton = useRef<HTMLButtonElement>(null)
  const autoTourUsed = useRef(false)
  const [orders, setOrders] = useState<any[]>([])
  const [orderOpen, setOrderOpen] = useState(false)
  const [approver, setApprover] = useState('')
  const [orderReason, setOrderReason] = useState('')
  const [orderBusy, setOrderBusy] = useState(false)
  const [orderError, setOrderError] = useState('')
  const [alts, setAlts] = useState<{ lambda: number; r: any }[] | null>(null)
  const [altBusy, setAltBusy] = useState(false)
  const [gate, setGate] = useState<{ check: string; passed: boolean; detail: string; blocking: boolean }[] | null>(null)
  const [alertTs, setAlertTs] = useState('')
  const [question, setQuestion] = useState('')
  const [mode, setMode] = useState<Mode>('replay')
  const [server, setServer] = useState<Ready>({ ready: false, state: 'checking' })
  const [liveAvailable, setLiveAvailable] = useState(true)
  const [reconnecting, setReconnecting] = useState(false)
  const [copied, setCopied] = useState(false)
  const [events, setEvents] = useState<TraceEvent[]>([])
  const [running, setRunning] = useState(false)
  const [error, setError] = useState('')
  const [view, setView] = useState<GraphView | null>(null)
  const [tab, setTab] = useState<Tab>('steps')
  const [showRule, setShowRule] = useState(false)
  const [theme, setTheme] = useState(loadTheme())
  const effectiveTheme = theme === 'system' ? (window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light') : theme
  const [receipt, setReceipt] = useState<Record<string, any> | null>(null)
  const [resumed, setResumed] = useState(false)
  const [resumedCard, setResumedCard] = useState<Card | null>(null)
  const [info, setInfo] = useState<Record<string, any> | null>(null)
  const [refresh, setRefresh] = useState<Record<string, any> | null>(null)
  const [demo, setDemo] = useState(false)
  const [busy, setBusy] = useState(false)
  const [audit, setAudit] = useState<{ checks?: any[]; verdict?: string; note?: string; blocking?: string[]; running?: boolean; error?: string } | null>(null)
  const source = useRef<EventSource | null>(null)
  const exSel = examples.findIndex((x) => x.account === account && x.alert_ts === alertTs)

  // Saved runs exist only for the example alerts: a custom alert uses Tools only instead of failing.
  const replayUnavailable = examples.length > 0 && exSel < 0
  const runMode: Mode = mode === 'replay' && replayUnavailable ? 'tools' : mode
  const notice = runMode !== mode ? 'Saved runs exist only for the example alerts, so this custom alert uses Tools only.' : ''

  // First visit: offer the tour once the data is ready. Automated browsers (tests, recordings) are never interrupted.
  useEffect(() => {
    if (tour !== 'off' || !server.ready || examples.length === 0 || navigator.webdriver) return
    try { if (localStorage.getItem(TOUR_KEY)) return } catch { return }
    const t = window.setTimeout(() => setTour((v) => (v === 'off' ? 'welcome' : v)), 700)
    return () => window.clearTimeout(t)
  }, [server.ready, examples.length, tour])

  // "Take the tour" on the landing page opens #/app/tour: start straight away and tidy the address.
  useEffect(() => {
    if (autoTour && !autoTourUsed.current && server.ready && examples.length > 0 && tour === 'off') {
      autoTourUsed.current = true
      window.history.replaceState(null, '', '#/app')
      startTour()
    }
  }, [autoTour, server.ready, examples.length]) // eslint-disable-line react-hooks/exhaustive-deps

  // The tour plays the first example in Replay: wait for the form to hold it, then run once.
  useEffect(() => {
    const x = examples[0]
    if (pendingTourRun && x && account === x.account && alertTs === x.alert_ts && runMode === 'replay' && !running) {
      setPendingTourRun(false)
      run()
    }
  }, [pendingTourRun, account, alertTs, runMode, running, examples]) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    api.examples().then((x) => {
      setExamples(x)
      if (x[0]) {
        setAccount(x[0].account)
        setAlertTs(x[0].alert_ts)
      }
    }).catch((e) => setError(errorText(e)))
    api.health().then((h) => {
      setDemo(h.demo)
      setLiveAvailable(h.live_available)
      if (h.live_available && !navigator.webdriver) setMode('live') // browser tests stay on replay, like the tour
    }).catch(() => undefined)
  }, [])

  // Poll readiness until the database has loaded the graph (about a minute after a restart), then stop.
  useEffect(() => {
    let stop = false
    let timer: ReturnType<typeof setTimeout> | undefined
    const poll = async () => {
      const r = await api.ready()
      if (stop) return
      setServer(r)
      if (r.ready) api.cases().then(setRecent).catch(() => undefined)
      else timer = setTimeout(poll, 3000)
    }
    poll()
    return () => {
      stop = true
      clearTimeout(timer)
    }
  }, [])

  useEffect(() => {
    const root = document.documentElement
    if (theme === 'system') root.removeAttribute('data-theme')
    else root.setAttribute('data-theme', theme)
    try {
      localStorage.setItem('chokepoint.theme', theme)
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
  const runError = events.find((e) => e.type === 'error')?.message
  const unmatched = events.find((e) => e.type === 'final')?.faithful
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
    let stale = false // the graph is fetched first without and then with the plan; a late answer must not win
    api.graph(caseId, planId).then((v) => !stale && setView(v)).catch(() => undefined)
    return () => {
      stale = true
    }
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

  // a fallback event means the live model failed and a saved run follows: drop what the failed attempt showed
  const push = useCallback((e: TraceEvent) => setEvents((prev) => (e.type === 'fallback' ? [e] : [...prev, e])), [])

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
    setReconnecting(false)
    setTab('steps')
    setRunning(true)
    try {
      if (runMode === 'tools') {
        await runTools()
        setRunning(false)
        return
      }
      const q = runMode === 'live' ? question.trim().slice(0, 400) : ''
      const task = `Account ${account} was alerted at ${alertTs}. ${q || 'Investigate and recommend which accounts to freeze.'}`
      const { run_id } = await api.startRun(task, runMode)
      source.current = openRunStream(run_id, {
        event: push,
        end: () => {
          setRunning(false)
          api.cases().then(setRecent).catch(() => undefined)
        },
        lost: (message) => {
          setError(message)
          setRunning(false)
        },
        reconnecting: setReconnecting,
      })
    } catch (e) {
      setError(errorText(e))
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
    let r: Record<string, any>
    try {
      r = await api.tool('resume_case', { case_id: id })
    } catch (e) {
      setError(errorText(e))
      return
    }
    if (r.error) {
      setError(`${r.error.message} ${r.error.hint ?? ''}`.trim())
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
      const ex = await api.tool('explain_finding', { finding_id: planF.finding_id }).catch(() => ({}) as Record<string, any>)
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
      setError(errorText(e))
    }
    setBusy(false)
  }

  async function resetDemo() {
    setBusy(true)
    try {
      await api.post('/api/demo/reset')
      setError('')
      setEvents([])
      setView(null)
      setInfo(null)
      setRefresh(null)
      api.cases().then(setRecent).catch(() => undefined)
    } catch (e) {
      setError(errorText(e))
    }
    setBusy(false)
  }

  async function rerunAffected() {
    if (!caseId) return
    setBusy(true)
    try {
      const r = await api.tool('refresh_case', { case_id: caseId })
      if (r.error) setError(`${r.error.message} ${r.error.hint ?? ''}`.trim())
      else {
        setRefresh(r)
        await resume(caseId, 'changes')
      }
    } catch (e) {
      setError(errorText(e))
    }
    setBusy(false)
  }

  async function askAuditor() {
    if (!plan || !caseId) return
    setAudit({ running: true })
    setTab('audit')
    try {
      if (runMode === 'tools') {
        const r = await api.tool('audit_checks', { plan_id: plan.plan_id })
        setAudit(r.error ? { error: `${r.error.code}: ${r.error.message}` } : { checks: r.checks, blocking: r.blocking_failed })
        return
      }
      const { run_id } = await api.startRun(`Audit plan ${plan.plan_id} for case ${caseId}.`, runMode, 'auditor')
      openRunStream(run_id, {
        event: (e) => {
          if (e.type === 'tool_result' && e.tool === 'audit_checks' && e.result)
            setAudit((a) => ({ ...a, checks: e.result!.checks, blocking: e.result!.blocking_failed, running: true }))
          if (e.type === 'tool_result' && e.tool === 'submit_audit' && e.result)
            setAudit((a) => ({ ...a, ...(e.result!.error ? { error: `${e.result!.error.code}: ${e.result!.error.message}` } : { verdict: e.result!.verdict, note: e.result!.note }) }))
          if (e.type === 'error') setAudit((a) => ({ ...a, error: friendlyRunError(e.message ?? ''), running: false }))
        },
        end: () => setAudit((a) => ({ ...a, running: false })),
        lost: (message) => setAudit((a) => ({ ...a, error: message, running: false })),
        reconnecting: () => undefined,
      })
    } catch (e) {
      setAudit({ error: errorText(e) })
    }
  }

  async function loadReceipt() {
    if (!card.plan) return
    try {
      setReceipt(await api.tool('explain_finding', { finding_id: card.plan.finding_id }))
    } catch (e) {
      setError(errorText(e))
    }
  }

  function copyPlan() {
    if (!card.plan) return
    navigator.clipboard?.writeText(card.plan.chokepoint.accounts.join('\n')).then(() => {
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    }, () => setError('Copying was blocked by the browser. Select the accounts in the Compare tab instead.'))
  }

  const movable = card.movable_usd_cents ?? card.plan?.before_usd_cents
  const plan = card.plan
  const sameSet = !!plan?.time_ordered && plan.time_ordered.accounts.length === plan.chokepoint.accounts.length && plan.time_ordered.accounts.every((a) => plan.chokepoint.accounts.includes(a))
  const noRoute = card.case && !card.case.has_route_to_perimeter
  const verifiedLine = card.verification
  const tourReady = (!!plan && !!card.verification && !running) || (!running && !!(error || runError))
  function startTour() {
    const x = examples[0]
    if (!x) return
    setAccount(x.account)
    setAlertTs(x.alert_ts)
    setMode('replay')
    setPendingTourRun(true)
    setTour('steps')
  }
  function endTour() {
    try { localStorage.setItem(TOUR_KEY, 'done') } catch { /* private mode: the tour just offers itself again */ }
    setTour('off')
    setPendingTourRun(false)
    tourButton.current?.focus()
  }
  const LEGEND = (
    <div className="legend" aria-label="Legend">
              <span><i className="sw diamond" /> alerted account (already frozen)</span>
              <span><i className="sw square" /> perimeter</span>
              <span><i className="sw circle" /> freeze</span>
              <span><i className="sw ring" /> rule-of-thumb pick</span>
              <span><i className="sw line" /> route that can carry money</span>
              {view?.truncated && <span>Showing {view.shown_accounts} of {view.total_accounts} accounts</span>}
            </div>
  )
  async function loadAlts() {
    const id = card.case?.case_id
    if (!id || !plan || alts || altBusy) return
    setAltBusy(true)
    try {
      const got: { lambda: number; r: any }[] = []
      for (const lambda of [2, 8]) {
        const r = await api.tool('freeze_plan', { case_id: id, lambda_weight: lambda })
        if (!r.error) got.push({ lambda, r })
      }
      const seen = new Set([plan.chokepoint.accounts.join('|')])
      setAlts(got.filter((g) => { const k = g.r.chokepoint_plan.accounts.join('|'); if (seen.has(k)) return false; seen.add(k); return true }))
    } catch {
      setAlts([])
    } finally {
      setAltBusy(false)
    }
  }
  async function switchToPlan(g: { lambda: number; r: any }) {
    setAltBusy(true)
    try {
      push({ type: 'tool_call', tool: 'freeze_plan', args: { case_id: g.r.case_id, lambda_weight: g.lambda } })
      push({ type: 'tool_result', tool: 'freeze_plan', ms: 0, ok: true, summary: '', result: g.r })
      push({ type: 'tool_call', tool: 'verify_plan', args: { plan_id: g.r.plan_id } })
      const t0 = performance.now()
      const v = await api.tool('verify_plan', { plan_id: g.r.plan_id })
      push({ type: 'tool_result', tool: 'verify_plan', ms: Math.round(performance.now() - t0), ok: !v.error, summary: v.error ? v.error.message : '', result: v })
      setReceipt(null)
      setAudit(null)
      setAlts(null)
      setOrderOpen(false)
    } finally {
      setAltBusy(false)
    }
  }
  const myOrders = orders.filter((o) => o.plan_id === plan?.plan_id)
  async function submitOrder() {
    if (!plan) return
    setOrderBusy(true)
    setOrderError('')
    try {
      const r = await fetch(`/api/plans/${plan.plan_id}/order`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ approver, reason: orderReason }) })
      const d = await r.json()
      if (!r.ok) setOrderError(d?.error ? `${d.error.message}${d.error.hint ? `. ${d.error.hint}` : ''}` : 'Check the name (2 to 60 characters) and the reason (at most 300).')
      else { setOrders((o) => [...o, d]); setOrderOpen(false) }
    } catch {
      setOrderError('Could not reach the server.')
    } finally {
      setOrderBusy(false)
    }
  }
  async function orderAction(o: any, kind: 'retry' | 'withdraw') {
    setOrderError('')
    try {
      const r = kind === 'retry'
        ? await fetch(`/api/orders/${o.order_id}/dispatch`, { method: 'POST' })
        : await fetch(`/api/orders/${o.order_id}`, { method: 'DELETE', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ approver: o.approver, reason: 'withdrawn from the case screen' }) })
      const d = await r.json()
      if (r.ok) setOrders((os) => os.map((x) => (x.order_id === o.order_id ? { ...x, ...d } : x)))
      else setOrderError(d?.error?.message ?? 'The server refused.')
    } catch {
      setOrderError('Could not reach the server.')
    }
  }
  const [chain, setChain] = useState<string>('')
  // Recompute every record's hash in the browser from the exact text the server hashed (sha256(canon + "|" + parent_hash)).
  async function verifyChain() {
    if (!caseId) return
    setChain('Checking…')
    try {
      const { records } = await fetch(`/api/cases/${caseId}/chain`).then((r) => r.json())
      const enc = new TextEncoder()
      const sha = async (t: string) => Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', enc.encode(t)))).map((b) => b.toString(16).padStart(2, '0')).join('')
      const stored: Record<string, string> = {}
      let checked = 0
      let unchained = 0
      for (const r of records as any[]) {
        if (!r.hash) { unchained++; continue }
        const parentOk = r.parent_id === null ? r.parent_hash === '' : stored[r.parent_id] === r.parent_hash
        if (!parentOk || (await sha(`${r.canon}|${r.parent_hash}`)) !== r.hash) { setChain(`✕ Broken at ${r.type} ${r.id}. ${checked} earlier records were intact.`); return }
        stored[r.id] = r.hash
        checked++
      }
      setChain(`✓ ${checked} receipt records re-hashed in this browser; every link matches.${unchained ? ` ${unchained} older records were written before chaining.` : ''}`)
    } catch {
      setChain('Could not fetch the receipt chain.')
    }
  }
  function orderText(o: any): string {
    const amt = plan?.chokepoint.traced_in_usd_cents ?? {}
    return [
      `FREEZE ORDER ${o.order_id} (recorded in the graph; no bank is called)`,
      `Alert: ${card.case?.alerted_account ?? ''} at ${card.case?.alert_ts ?? ''} UTC`,
      `Approved by: ${o.approver} at ${new Date(o.recorded_at * 1000).toISOString().slice(0, 16).replace('T', ' ')} UTC${o.reason ? `. Reason: ${o.reason}` : ''}`,
      `Plan ${o.plan_id}, bound by hash ${o.plan_hash}, data version ${o.data_version}, check ${o.verification_finding_id}`,
      `Accounts to freeze (${o.accounts.length}):`,
      ...o.accounts.map((a: string) => `  ${a}  received up to ${usd2(amt[a] ?? 0)} from the alerted account's downstream network`),
      `Check: no route to the ${plan?.perimeter_hops}-hop perimeter is left on a copy of the graph, in the recorded transfers.`,
    ].join(String.fromCharCode(10))
  }
  const canRun = server.ready && !running && !!account && !!alertTs
  const shownError = error || (runError ? friendlyRunError(runError) : '')

  return (
    <div className="app">
      <a className="skip-link" href="#main">Skip to the case</a>
      <header className="header">
        <a className="brand" href="#/" aria-label="Chokepoint home">
          <svg className="brand-mark" viewBox="0 0 26 26" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" aria-hidden>
            <path d="M3 5l9 8M3 21l9-8M14 13h9" />
            <circle cx="13" cy="13" r="3" fill="currentColor" stroke="none" />
            <path d="M18.5 8.5l5 9" stroke="var(--data-frozen)" />
          </svg>
          Chokepoint
          <span className="brand-sub">mule-money investigation</span>
        </a>
        <div className="field">
          <label className="sr-only" htmlFor="recent">Saved cases</label>
          <select id="recent" className="input select-compact" value="" onChange={(e) => resume(e.target.value)}>
            <option value="">Resume a saved case…</option>
            {recent.map((c) => (
              <option key={c.case_id} value={c.case_id}>{c.alerted_account} · {c.alert_ts} · {c.case_id}</option>
            ))}
          </select>
        </div>
        <span className="spacer" />
        <div className="seg" role="group" aria-label="How to run">
          {(['live', 'replay', 'tools'] as Mode[]).map((m) => (
            <button key={m} aria-pressed={runMode === m} disabled={m === 'live' && !liveAvailable}
              title={m === 'live' && !liveAvailable ? 'This server has no language-model key, so live runs are off.' : MODE_NOTES[m]}
              onClick={() => setMode(m)}>
              {m === 'live' ? 'Live agent' : m === 'replay' ? 'Replay' : 'Tools only'}
            </button>
          ))}
        </div>
        <span className="badge">Synthetic data</span>
        <button ref={tourButton} className="btn small tour-trigger" disabled={!server.ready || examples.length === 0} onClick={startTour}>Tour</button>
        <button className="btn small" onClick={() => setTheme(effectiveTheme === 'dark' ? 'light' : 'dark')} aria-label="Toggle light and dark theme">
          {effectiveTheme === 'dark' ? 'Light' : 'Dark'}
        </button>
      </header>

      {!server.ready && server.state !== 'checking' && (
        <div className="status-bar" role="status">
          <span className="ico" aria-hidden>…</span>
          <span>
            {server.state === 'loading'
              ? 'The database is loading the transfer graph from disk. This takes about a minute after a restart; this page will be ready on its own.'
              : server.state === 'dataset_missing'
                ? (server.message ?? 'The transfer graph has not been loaded yet.')
                : 'Cannot reach the server or the database. Retrying…'}
          </span>
        </div>
      )}

      <main className="main" id="main">
        <section className="left" aria-label="Case and graph">
          <div className="panel" data-tour="alert">
            <div className="row">
              <div className="field">
                <label htmlFor="ex">Example alert (chosen where a rule of thumb leaves money movable)</label>
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
              <div className="field ts">
                <label htmlFor="ts">Alert time (UTC)</label>
                <input id="ts" className="input mono" value={alertTs} onChange={(e) => setAlertTs(e.target.value)} />
              </div>
              <button className="btn primary" disabled={!canRun} onClick={run}>
                {running ? 'Investigating…' : runMode === 'tools' ? 'Open case and build plan' : 'Investigate'}
              </button>
            </div>
            {runMode === 'live' && (
              <div className="field">
                <label htmlFor="q">Ask your own question about this alert (optional)</label>
                <input id="q" className="input" value={question} maxLength={400} placeholder="Investigate and recommend which accounts to freeze." onChange={(e) => setQuestion(e.target.value)} />
              </div>
            )}
            <p className="note">{notice ? `${notice} ` : ''}{MODE_NOTES[runMode]}</p>
            {events.find((e) => e.type === 'fallback') && <p className="note" role="status">{events.find((e) => e.type === 'fallback')?.message}</p>}
            {reconnecting && <p className="note" role="status">Connection interrupted, reconnecting…</p>}
            {shownError && (
              <p className="error" role="alert">
                {shownError}
                {demo && shownError.includes('reset the demo data') && (
                  <button className="btn small ml-12" disabled={busy} onClick={resetDemo}>Reset demo data</button>
                )}
              </p>
            )}
          </div>

          <div className="panel canvas-wrap" data-tour="map">
            {view ? (
              <>
                <Graph view={view} showRule={showRule} verified={verified} theme={theme} />
                <div className="legend-float">{LEGEND}</div>
              </>
            ) : (
              <div className="canvas-empty">
                <svg className="empty-glyph" viewBox="0 0 84 84" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" aria-hidden>
                  <circle cx="14" cy="42" r="4" fill="currentColor" stroke="none" />
                  <path d="M18 42h14M36 42l16-20M36 42h16M36 42l16 20" strokeDasharray="2 4" />
                  <rect x="52" y="18" width="8" height="8" /><rect x="52" y="38" width="8" height="8" /><rect x="52" y="58" width="8" height="8" />
                  <path d="M62 30l14 24M76 30L62 54" stroke="var(--data-frozen)" strokeWidth="2" />
                </svg>
                <h2>{running ? 'Opening the case…' : 'Where can the money still go?'}</h2>
                <p>
                  {running
                    ? 'The map appears as soon as the case is open.'
                    : 'An alerted account is one flagged for holding stolen money that has already started moving. Chokepoint looks for a small set of accounts that cuts every route the money could take in past transfers, then checks the result.'}
                </p>
                {!running && (
                  <button className="btn primary center-self" disabled={!canRun} onClick={run}>
                    {server.ready ? (exSel >= 0 ? 'Run the example alert' : 'Investigate this alert') : 'Waiting for the database…'}
                  </button>
                )}
                {!running && server.ready && examples.length > 0 && (
                  <button className="link-btn" onClick={startTour}>New here? Take the one-minute tour</button>
                )}
              </div>
            )}
          </div>

          <div>
            {!view ? LEGEND : <div className="legend-below">{LEGEND}</div>}
            <p className="note legend-note">
              {card.case ? `Transfers in the ${card.case.window_days} days before the alert · ${card.case.perimeter_hops}-hop perimeter · data version ${card.case.data_version}` : 'Static view of past transfers; it does not know what happens after the alert.'}
              {demo && caseId && (
                <button className="btn small" disabled={busy} onClick={simulateLate}>Simulate late transfers</button>
              )}
              {view && card.plan && (
                <label>
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
                <h1 className="hero" data-tour="hero">
                  {usd(movable)}{' '}
                  <small>could still reach the perimeter · a ceiling from past transfers, not a forecast</small>
                </h1>
                {plan?.time_ordered && plan.time_ordered.money_movable_usd_cents !== movable && (
                  <p className="hero-timed">In time order: <strong>{usd(plan.time_ordered.money_movable_usd_cents)}</strong> (transfers out of order are not routes)</p>
                )}
                {plan && (
                  <>
                    <div className="line-actions">
                      <p className="card-line">Freeze {plan.chokepoint.count} account{plan.chokepoint.count === 1 ? '' : 's'} to cut these routes</p>
                      <button className="btn small" onClick={copyPlan}>{copied ? 'Copied' : 'Copy account list'}</button>
                    </div>
                    <div className="verify" data-tour="verified">
                      {verifiedLine ? (
                        <span className={`chip ${verifiedLine.verified ? 'good' : 'bad'}`}>
                          <span className="ico" aria-hidden><Icon kind={verifiedLine.verified ? 'check' : 'cross'} /></span>
                          {verifiedLine.verified ? `Verified: no route left to the ${plan.perimeter_hops}-hop perimeter` : 'Not verified: routes remain'}
                        </span>
                      ) : running ? (
                        <span className="chip"><span className="ico" aria-hidden>…</span> Verifying</span>
                      ) : (
                        <span className="chip warn"><span className="ico" aria-hidden>!</span> Not verified yet</span>
                      )}
                      {verifiedLine?.verified && (
                        <p className="after">
                          <span>After the freeze</span>
                          <strong><Countdown from={movable ?? 0} to={plan.chokepoint.residual_movable_usd_cents} /></strong>
                          <span>in the recorded transfers, checked on a copy</span>
                        </p>
                      )}
                    </div>
                    {verifiedLine && <details className="how-checked"><summary>How this was checked</summary>
                      <p className="note">{verifiedLine.method}</p>
                      {verifiedLine.source_recheck && (
                        <p className="note">A second check, separate from the copy: it {verifiedLine.source_recheck.method}. With nothing frozen it reached {verifiedLine.source_recheck.perimeter_reachable_before} perimeter account{verifiedLine.source_recheck.perimeter_reachable_before === 1 ? '' : 's'}; with the plan applied, {verifiedLine.source_recheck.perimeter_reachable_after}. It checks the same recorded transfers, by a different route.</p>
                      )}
                    </details>}
                    {plan.chokepoint.accounts.length > 0 && (
                      <ul className="acct-list" aria-label="Accounts to freeze" data-tour="plan">
                        {plan.chokepoint.accounts.map((a) => (
                          <li key={a}>
                            <span className="mono">{a}</span>
                            <span className="acct-hop">{view?.nodes.find((n) => n.id === a)?.depth ?? '?'} hop{view?.nodes.find((n) => n.id === a)?.depth === 1 ? '' : 's'} out</span>
                            {plan.chokepoint.traced_in_usd_cents && <span className="acct-amt" title="Most this account received from the alerted account's downstream network in the window">{usd2(plan.chokepoint.traced_in_usd_cents[a] ?? 0)} received</span>}
                          </li>
                        ))}
                      </ul>
                    )}
                    {verifiedLine?.verified && plan.chokepoint.accounts.length > 0 && (
                      <details key={plan.plan_id} className="how-checked alts" onToggle={(e) => { if ((e.currentTarget as HTMLDetailsElement).open) loadAlts() }}>
                        <summary>Other plans, gentler on everyday activity</summary>
                        {altBusy && !alts && <p className="note">Working out the options…</p>}
                        {alts && alts.length === 0 && <p className="note">No other plan differs here: every weighting picks these same accounts.</p>}
                        {alts && alts.length > 0 && (
                          <>
                            <p className="note">Each extra account can buy less disruption to the accounts' ordinary transfers elsewhere. Every option is checked on a copy before you can approve it.</p>
                            <ul className="alt-list">
                              <li><span>This plan</span><span className="mono">{plan.chokepoint.count} accounts · {plan.chokepoint.baseline_activity_transfers} everyday transfers</span><span /></li>
                              {alts.map((g) => (
                                <li key={g.lambda}>
                                  <span>Gentler{g.lambda >= 8 ? ' still' : ''}</span>
                                  <span className="mono">{g.r.chokepoint_plan.count} accounts · {g.r.chokepoint_plan.baseline_activity_transfers} everyday transfers</span>
                                  <button className="btn small" disabled={altBusy} onClick={() => switchToPlan(g)}>Use this plan</button>
                                </li>
                              ))}
                            </ul>
                          </>
                        )}
                      </details>
                    )}
                    {verifiedLine?.verified && plan.chokepoint.accounts.length > 0 && (
                      <div className="order">
                        {myOrders.map((o) => (
                          <p key={o.order_id} className="note order-done" role="status">
                            <strong>Freeze order {o.order_id} {o.status === 'withdrawn' ? 'withdrawn' : 'recorded'}</strong> · approved by {o.approver} · bound to plan hash {o.plan_hash.slice(0, 12)} · data version {o.data_version}. No bank is called.{' '}
                            {o.status !== 'withdrawn' && o.dispatch && (o.dispatch.status === 'sent'
                              ? <><strong>Dispatched</strong>: <a href={o.dispatch.url} target="_blank" rel="noreferrer">freeze request filed</a> (synthetic), {o.dispatch.holds_placed} hold{o.dispatch.holds_placed === 1 ? '' : 's'} placed.{' '}</>
                              : <><strong>Queued, not sent.</strong> {o.dispatch.holds_placed} hold{o.dispatch.holds_placed === 1 ? '' : 's'} placed, the freeze request has not been filed{o.dispatch.note ? ` (${o.dispatch.note})` : ''}.{' '}
                                {!o.dispatch.note?.startsWith('no OPS_GITHUB_TOKEN') && <><button className="link-btn" onClick={() => orderAction(o, 'retry')}>Retry dispatch</button>{' '}</>}</>)}
                            <button className="link-btn" onClick={() => navigator.clipboard?.writeText(orderText(o))}>Copy freeze order</button>
                            {o.status !== 'withdrawn' && <>{' '}<button className="link-btn" onClick={() => orderAction(o, 'withdraw')}>Withdraw</button></>}
                          </p>
                        ))}
                        {info?.has_newer_data ? (
                          <p className="note">Newer transfers exist, so this plan can no longer be approved. Re-run the affected findings first.</p>
                        ) : orderOpen ? (
                          <form className="order-form" onSubmit={(e) => { e.preventDefault(); submitOrder() }}>
                            <ul className="gate" aria-label="Checks before approval">
                              {(gate ?? []).map((c) => (
                                <li key={c.check} className={c.passed ? 'ok' : 'no'}><span aria-hidden>{c.passed ? '✓' : '✕'}</span> {c.detail}</li>
                              ))}
                              {verifiedLine?.source_recheck && (
                                <li className={verifiedLine.source_recheck.perimeter_reachable_after === 0 ? 'ok' : 'no'}>
                                  <span aria-hidden>{verifiedLine.source_recheck.perimeter_reachable_after === 0 ? '✓' : '✕'}</span> the second check on the source graph reaches {verifiedLine.source_recheck.perimeter_reachable_after} perimeter accounts
                                </li>
                              )}
                              {gate === null && <li className="note">Running the checks…</li>}
                            </ul>
                            <label>Your name<input className="input" value={approver} onChange={(e) => setApprover(e.target.value)} maxLength={60} autoComplete="name" required /></label>
                            <label>Reason (optional)<input className="input" value={orderReason} onChange={(e) => setOrderReason(e.target.value)} maxLength={300} /></label>
                            <div className="order-actions">
                              <button className="btn small primary" type="submit" disabled={orderBusy || approver.trim().length < 2}>{orderBusy ? 'Recording…' : 'Record freeze order'}</button>
                              <button className="btn small" type="button" onClick={() => setOrderOpen(false)}>Cancel</button>
                            </div>
                            <p className="note">A person decides; the agent cannot approve. The order is tied to this checked plan and records your name. After you record it, a freeze request is filed in a public ops repo (synthetic) and holds are placed in the graph. Nothing is sent to a bank.</p>
                            {orderError && <p className="error" role="alert">{orderError}</p>}
                          </form>
                        ) : (
                          <button className="btn small" onClick={() => { setOrderOpen(true); setGate(null); if (plan) api.tool('audit_checks', { plan_id: plan.plan_id }).then((r) => setGate(r.checks ?? null)).catch(() => setGate(null)) }}>{myOrders.length ? 'Approve again…' : 'Approve a freeze order…'}</button>
                        )}
                      </div>
                    )}
                    {plan.time_ordered && (
                      <p className="note timing-line" data-tour="timing">
                        <strong>Timing check:</strong>{' '}
                        {plan.time_ordered.has_time_ordered_route
                          ? sameSet
                            ? `${plan.time_ordered.time_ordered_pairs} of ${plan.time_ordered.transfer_pairs} links work in time order, and the same ${plan.time_ordered.count} account${plan.time_ordered.count === 1 ? '' : 's'} cut them.`
                            : `${plan.time_ordered.time_ordered_pairs} of ${plan.time_ordered.transfer_pairs} links work in time order; the freeze needs ${plan.time_ordered.count} account${plan.time_ordered.count === 1 ? '' : 's'} (${plan.chokepoint.count} above; ${plan.time_ordered.accounts.length === 0 ? 'none needed' : plan.time_ordered.accounts.every((a) => plan.chokepoint.accounts.includes(a)) ? 'a subset' : 'a different set'}).`
                          : 'none of the routes works in time order here, so the recorded timing would not have let this money leave. Details in Compare.'}
                      </p>
                    )}
                    {!plan.actionable && (
                      <div className="banner" role="note"><span className="ico" aria-hidden>!</span><span>This cut needs {plan.chokepoint.count} accounts, more than a team can act on. Try a smaller perimeter or a shorter window.</span></div>
                    )}
                  </>
                )}
              </>
            ) : running ? (
              <div aria-busy="true">
                <span className="skeleton hero-sk" />
                <span className="skeleton line-sk" />
                <span className="skeleton chip-sk" />
              </div>
            ) : noRoute ? (
              <div className="banner" role="note"><span className="ico" aria-hidden>!</span><span>No route to the perimeter in this window. Widen the window or check the account.</span></div>
            ) : (
              <div>
                <h2 className="how-title">What you get</h2>
                <ol className="how">
                  <li><strong>The money at risk.</strong> How much could still move out of reach, from past transfers.</li>
                  <li><strong>A freeze plan.</strong> A small set of accounts that cuts every route to the perimeter in past transfers, compared with a simple rule of thumb.</li>
                  <li><strong>A check and a receipt.</strong> The plan is tested on a copy of the graph, and every result records how it was made.</li>
                </ol>
              </div>
            )}
            {finalText && (
              <>
                {unmatched && !unmatched.faithful && (
                  <div className="banner" role="note"><span className="ico" aria-hidden>!</span><span>This summary mentions a figure that no tool returned ({[...unmatched.unmatched_amounts, ...unmatched.unmatched_accounts].join(', ')}). Rely on the cards and tables, not the text.</span></div>
                )}
                <p className="note">
                  {cachedRun ? 'Replay: a saved run, not a live response.' : 'Live run.'} Numbers come from tool runs; the model only writes the summary below.
                </p>
              </>
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
            <div className="tabs" role="tablist" aria-label="Case details" data-tour="tabs" onKeyDown={(e) => {
              if (e.key !== 'ArrowRight' && e.key !== 'ArrowLeft' && e.key !== 'Home' && e.key !== 'End') return
              const ids = [...e.currentTarget.querySelectorAll<HTMLButtonElement>('[role="tab"]')]
              const at = ids.findIndex((b) => b === document.activeElement)
              const next = e.key === 'Home' ? 0 : e.key === 'End' ? ids.length - 1 : (at + (e.key === 'ArrowRight' ? 1 : -1) + ids.length) % ids.length
              e.preventDefault()
              ids[next]?.focus()
              ids[next]?.click()
            }}>
              {(['steps', 'compare', 'receipt', ...(plan ? ['audit'] : []), ...(refresh ? ['changes'] : [])] as Tab[]).map((t) => (
                <button key={t} id={`tab-${t}`} role="tab" aria-selected={tab === t} aria-controls="tabpanel" tabIndex={tab === t ? 0 : -1} onClick={() => { setTab(t); window.requestAnimationFrame(() => document.getElementById('tabpanel')?.scrollIntoView({ block: 'nearest', behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth' })) }}>
                  {t === 'steps' ? 'Steps' : t === 'compare' ? 'Compare' : t === 'receipt' ? 'Receipt' : t === 'audit' ? 'Audit' : 'Changes'}
                </button>
              ))}
            </div>
            <div className="tabpanel" id="tabpanel" role="tabpanel" aria-labelledby={`tab-${tab}`}>
              {tab === 'steps' && (
                <div aria-live="polite">
                  {steps.length === 0 && <p className="note">Steps appear here as the agent works. Each one is a tool run stored in the graph.</p>}
                  <div className="timeline">
                  {steps.map((s, i) => (
                    <div key={i}>
                      {s.thought && <div className="decision"><strong>Agent note:</strong> {s.thought}</div>}
                      <details className="step">
                        <summary>
                          <span className={`mark ${s.ms === undefined ? 'run' : s.ok ? 'ok' : 'fail'}`} aria-hidden>{s.ms === undefined ? null : <Icon kind={s.ok ? 'check' : 'cross'} />}</span>
                          <span className="name">{STEP_NAMES[s.tool] ?? s.tool}</span>
                          <span className="ms">{s.ms === undefined ? 'running' : cachedRun && s.ms < 250 ? 'saved' : `${(s.ms / 1000).toFixed(1)} s`}</span>
                        </summary>
                        <pre>{JSON.stringify(s.args, null, 1)}{'\n'}{s.summary}</pre>
                      </details>
                    </div>
                  ))}
                  </div>
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
                      <p className="note">The rule of thumb freezes the accounts the alerted account paid most recently. Held-out evaluation on 124 synthetic case-times: the two block about the same share of the money that had already escaped; Chokepoint's frozen accounts were more often part of the laundering (+19 points of precision) and its plan is checked on a copy of the graph to leave no route in the recorded transfers.</p>
                      {plan.time_ordered && (
                        <div className="note" role="note">
                          <strong>Timing check.</strong> Money cannot go back in time, so a transfer that happens before the money reaches its sender is not a route.{' '}
                          {plan.time_ordered.has_time_ordered_route ? (
                            <>
                              {plan.time_ordered.time_ordered_pairs} of {plan.time_ordered.transfer_pairs} transfer links can be part of a time-ordered route. A plan for those alone needs {plan.time_ordered.count} account{plan.time_ordered.count === 1 ? '' : 's'} (the plan above: {plan.chokepoint.count}); up to {usd2(plan.time_ordered.money_movable_usd_cents)} could have moved in order, {usd2(plan.time_ordered.residual_movable_usd_cents)} after the freeze{plan.time_ordered.routes_left ? '' : ', with no time-ordered route left'}. Checked in code on the recorded transfers, not on the graph copy.
                              {plan.time_ordered.count > 0 && <> Accounts: <span className="mono">{plan.time_ordered.accounts.join(', ')}</span>.</>}
                            </>
                          ) : (
                            <>
                              Of {plan.time_ordered.transfer_pairs} transfer links, {plan.time_ordered.time_ordered_pairs} can be part of a time-ordered route, and none reaches the perimeter: in the recorded transfers the money could not have left by these routes in this order. The plan above ignores timing, so it is the cautious one.
                            </>
                          )}
                        </div>
                      )}
                      <table className="table-gap-lg">
                        <thead><tr><th>Chokepoint plan: accounts to freeze</th><th>Role</th>{plan.chokepoint.traced_in_usd_cents && <th className="num" title="Money this account received from the alerted account's downstream network in the window. An upper bound on what a hold must cover; the data has no balances.">Traced in (max)</th>}</tr></thead>
                        <tbody>
                          {plan.chokepoint.accounts.map((a) => (
                            <tr key={a}><td className="mono">{a}</td><td>{view?.nodes.find((n) => n.id === a)?.depth ?? '?'} hop(s) from the alerted account</td>{plan.chokepoint.traced_in_usd_cents && <td className="num">{usd2(plan.chokepoint.traced_in_usd_cents[a] ?? 0)}</td>}</tr>
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
                  <button className="btn small" disabled={audit?.running} onClick={askAuditor}>{audit?.running ? 'Auditing…' : runMode === 'tools' ? 'Run audit checks' : 'Ask the auditor'}</button>
                  {audit?.error && <p className="error" role="alert">{audit.error}</p>}
                  {audit?.checks && (
                    <table className="table-gap">
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
                    <div className="decision spaced">
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
                      <button className="btn small" onClick={loadReceipt}>Open receipt for this plan</button>{' '}
                      <button className="btn small" onClick={verifyChain} disabled={!caseId}>Verify chain</button>
                      {chain && <p className="note" role="status">{chain}</p>}
                      {receipt && (
                        <>
                          <table className="table-gap">
                            <tbody>
                              <tr><th>Finding</th><td className="mono">{receipt.finding_id}</td></tr>
                              <tr><th>Produced by</th><td>{STEP_NAMES[receipt.tool] ?? receipt.tool} · solver {receipt.solver}</td></tr>
                              <tr><th>Parameters</th><td className="mono">{JSON.stringify(receipt.parameters)}</td></tr>
                              <tr><th>Data version</th><td>{receipt.data_version}</td></tr>
                              <tr><th>Accounts read</th><td>{receipt.accounts_read}</td></tr>
                            </tbody>
                          </table>
                          <button className="btn small mt-8" onClick={() => navigator.clipboard?.writeText(JSON.stringify(receipt, null, 2))}>Copy receipt</button>
                        </>
                      )}
                      <QueriesPanel />
                    </>
                  )}
                </div>
              )}
            </div>
          </div>

          {finalText && (
            <details className="panel agent-summary">
              <summary>Agent summary</summary>
              <p>{finalText}</p>
              <p className="note">The model writes these words. Every figure on the cards comes from a tool run, and this text is checked against them.{events.filter((e) => e.type === 'tool_result' && e.tool === 'freeze_plan').length > 1 ? ' This summary describes the first plan, not the one you switched to.' : ''}</p>
            </details>
          )}
        </aside>
      </main>
      <footer className="footer">
        Synthetic data for demonstration. A freeze plan is a recommendation, not an instruction.
        <span className="footer-links">
          <details className="connect">
            <summary>Connect your agent (MCP)</summary>
            <div className="connect-pop">
              <p className="note">The same nine tools the built-in agent uses, over MCP streamable HTTP. There is no approve or dispatch tool: only a person approves, in this app. Synthetic data; calls share the tool rate limit.</p>
              <p className="note">Claude Code:</p>
              <pre className="mono">{`claude mcp add --transport http chokepoint ${window.location.origin}/mcp`}</pre>
              <p className="note">Claude Desktop, Cursor and other clients (JSON config):</p>
              <pre className="mono">{JSON.stringify({ mcpServers: { chokepoint: { url: `${window.location.origin}/mcp` } } }, null, 2)}</pre>
              <p className="note">Then ask it to open a case for an example alert, build the freeze plan and verify it.</p>
            </div>
          </details>
          <a href="/docs" target="_blank" rel="noreferrer">API</a>
          <a href="https://github.com/Hackmaass/Chokepoint" target="_blank" rel="noreferrer">Source</a>
          <span className="build">Build {__BUILD__}</span>
        </span>
      </footer>
      {tour === 'welcome' && <TourWelcome onStart={startTour} onSkip={endTour} />}
      {tour === 'steps' && <Tour steps={TOUR} ready={tourReady} onClose={endTour} onFinish={endTour} />}
    </div>
  )
}
