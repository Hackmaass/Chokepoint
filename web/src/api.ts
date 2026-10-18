export type Example = {
  account: string
  alert_ts: string
  split: string
  frac: number
  accounts_in_region: number
  plan_accounts: number
  movable_usd_cents: number
  rule_leaves_usd_cents: number
}

export type PlanSide = {
  accounts: string[]
  count: number
  residual_movable_usd_cents: number
  routes_left: boolean
  on_a_route_to_perimeter: number
  baseline_activity_transfers: number
  traced_in_usd_cents?: Record<string, number>
  cost?: number
  solver?: string
  rule?: string
}

export type TimeOrdered = {
  transfer_pairs: number
  time_ordered_pairs: number
  has_time_ordered_route: boolean
  accounts: string[]
  count: number
  routes_left: boolean
  money_movable_usd_cents: number
  residual_movable_usd_cents: number
}

export type Card = {
  case?: {
    case_id: string
    alerted_account: string
    alert_ts: string
    window_days: number
    perimeter_hops: number
    accounts: number
    perimeter_accounts: number
    has_route_to_perimeter: boolean
    data_version: number
  }
  movable_usd_cents?: number
  plan?: {
    plan_id: string
    finding_id: string
    actionable: boolean
    perimeter_hops: number
    lambda_weight: number
    before_usd_cents: number
    chokepoint: PlanSide
    rule_of_thumb: PlanSide
    time_ordered?: TimeOrdered
  }
  verification?: {
    plan_id: string
    verified: boolean
    statement: string
    method: string
    perimeter_reachable_before: number
    perimeter_reachable_after: number
  }
}

export type TraceEvent = {
  type: 'start' | 'thought' | 'tool_call' | 'tool_result' | 'final' | 'error' | 'card'
  step?: number
  tool?: string
  args?: Record<string, unknown> | null
  ms?: number
  ok?: boolean
  summary?: string
  result?: Record<string, any>
  text?: string
  message?: string
  card?: Card
  cached?: boolean
  cached_calls?: number
  mode?: string
  stopped?: string
  faithful?: { unmatched_amounts: string[]; unmatched_accounts: string[]; faithful: boolean }
}

export type GraphView = {
  case_id: string
  plan_id: string | null
  verified: boolean | null
  perimeter_hops: number
  total_accounts: number
  shown_accounts: number
  truncated: boolean
  nodes: { id: string; label: string; depth: number; role: 'origin' | 'frozen' | 'rule' | 'perimeter' | 'other' }[]
  edges: { from: string; to: string; usd_cents: number; transfers: number; flow_usd_cents: number }[]
}

export type CaseRow = { case_id: string; alerted_account: string; alert_ts: string; perimeter_hops: number; findings: number }

export class ApiError extends Error {
  status: number
  code: string
  hint: string

  constructor(message: string, status: number, code: string, hint: string) {
    super(message)
    this.status = status
    this.code = code
    this.hint = hint
  }
}

/** Every API error is {"error": {code, message, hint?}}; turn a failed response into one readable ApiError. */
async function toApiError(r: Response): Promise<ApiError> {
  let code = 'ERROR'
  let message = `The server answered ${r.status}.`
  let hint = ''
  try {
    const e = (await r.json()).error ?? {}
    code = e.code ?? code
    message = e.message ?? message
    hint = e.hint ?? ''
  } catch {
    /* not JSON (a proxy error page, for example) */
  }
  if (r.status === 429) message = 'Too many requests. Wait a few seconds and try again.'
  if (r.status === 502 || r.status === 504) message = 'The server is not answering. It may be restarting.'
  return new ApiError(message, r.status, code, hint)
}

/** One sentence for the screen, for an ApiError, a network failure or anything else. */
export function errorText(e: unknown): string {
  if (e instanceof ApiError) return [e.message, e.hint].filter(Boolean).join(' ')
  if (e instanceof TypeError) return 'Cannot reach the server. Check your connection and try again.'
  return String(e instanceof Error ? e.message : e)
}

async function j<T>(r: Response): Promise<T> {
  if (!r.ok) throw await toApiError(r)
  return r.json() as Promise<T>
}

export type Ready = { ready: boolean; state: string; message?: string; latest_data_version?: number }
export type Health = { ok: boolean; version: string; demo: boolean; live_available: boolean }

export const api = {
  health: () => fetch('/api/health').then((r) => j<Health>(r)),
  /** Readiness never throws: "the server is down" is a state the UI shows, not an exception. */
  ready: (): Promise<Ready> =>
    fetch('/api/ready')
      .then((r) => r.json() as Promise<Ready>)
      .catch(() => ({ ready: false, state: 'unreachable', message: 'The server is not reachable.' })),
  post: (path: string) => fetch(path, { method: 'POST' }).then((r) => j<Record<string, any>>(r)),
  examples: () => fetch('/api/examples').then((r) => j<Example[]>(r)),
  cases: () => fetch('/api/cases').then((r) => j<CaseRow[]>(r)),
  tool: (tool: string, args: Record<string, unknown>) =>
    fetch(`/api/tools/${tool}`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(args) }).then((r) =>
      j<Record<string, any>>(r),
    ),
  graph: (caseId: string, planId?: string) =>
    fetch(`/api/cases/${caseId}/graph${planId ? `?plan_id=${planId}` : ''}`).then((r) => j<GraphView>(r)),
  startRun: (task: string, mode: 'live' | 'replay', role: 'investigator' | 'auditor' = 'investigator') =>
    fetch('/api/runs', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ task, mode, role }) }).then((r) =>
      j<{ run_id: string }>(r),
    ),
}

type StreamHandlers = {
  event: (e: TraceEvent) => void
  end: () => void
  /** The stream died for good (server gone, run unknown): the run's result cannot be trusted to be complete. */
  lost: (message: string) => void
  /** true while the browser is retrying a dropped connection (it resumes from the last event it saw). */
  reconnecting: (on: boolean) => void
}

const RECONNECT_GIVE_UP_MS = 20_000

/** Follow a run's event stream. The server numbers events, so a dropped connection resumes without repeats. */
export function openRunStream(runId: string, on: StreamHandlers): EventSource {
  const es = new EventSource(`/api/runs/${runId}/events`)
  let ended = false
  let giveUp: ReturnType<typeof setTimeout> | undefined
  const stop = () => {
    clearTimeout(giveUp)
    giveUp = undefined
  }
  es.onmessage = (m) => {
    stop()
    on.reconnecting(false)
    on.event(JSON.parse(m.data) as TraceEvent)
  }
  es.addEventListener('end', () => {
    ended = true
    stop()
    es.close()
    on.end()
  })
  es.onerror = () => {
    if (ended) return
    if (es.readyState === EventSource.CLOSED) {
      stop()
      on.lost('The connection to the server was lost before the run finished. Start it again.')
      return
    }
    on.reconnecting(true)
    giveUp ??= setTimeout(() => {
      es.close()
      on.reconnecting(false)
      on.lost('The server stopped answering before the run finished. Start it again.')
    }, RECONNECT_GIVE_UP_MS)
  }
  return es
}

export const usd = (cents: number) =>
  new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 0 }).format(cents / 100)
export const usd2 = (cents: number) => new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' }).format(cents / 100)

export const STEP_NAMES: Record<string, string> = {
  open_case: 'Opened case',
  flow: 'Computed money flow',
  freeze_plan: 'Built freeze plan',
  verify_plan: 'Verified plan',
  resume_case: 'Resumed case',
  explain_finding: 'Read receipt',
  refresh_case: 'Re-ran affected findings',
  audit_checks: 'Ran audit checks',
  submit_audit: 'Recorded audit verdict',
}

/** Mirrors the server's answer_card: numbers come from tool results only. */
export function buildCard(results: { tool: string; result: Record<string, any> }[]): Card {
  const card: Card = {}
  for (const { tool, result: r } of results) {
    if (r.error) continue
    if (tool === 'open_case')
      card.case = {
        case_id: r.case_id,
        alerted_account: r.alerted_account,
        alert_ts: r.alert_ts,
        window_days: r.window_days,
        perimeter_hops: r.perimeter_hops,
        accounts: r.accounts,
        perimeter_accounts: r.perimeter_accounts,
        has_route_to_perimeter: r.has_route_to_perimeter,
        data_version: r.data_version,
      }
    if (tool === 'flow') card.movable_usd_cents = r.max_movable_usd_cents
    if (tool === 'freeze_plan')
      card.plan = {
        plan_id: r.plan_id,
        finding_id: r.finding_id,
        actionable: r.actionable,
        perimeter_hops: r.perimeter_hops,
        lambda_weight: r.lambda_weight,
        before_usd_cents: r.money_movable_before_usd_cents,
        chokepoint: r.chokepoint_plan,
        rule_of_thumb: r.rule_of_thumb_plan,
        time_ordered: r.time_ordered,
      }
    if (tool === 'verify_plan')
      card.verification = {
        plan_id: r.plan_id,
        verified: r.verified,
        statement: r.statement,
        method: r.method,
        perimeter_reachable_before: r.perimeter_reachable_before,
        perimeter_reachable_after: r.perimeter_reachable_after,
      }
  }
  return card
}
