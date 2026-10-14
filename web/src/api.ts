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
  cost?: number
  solver?: string
  rule?: string
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

async function j<T>(r: Response): Promise<T> {
  if (!r.ok) throw new Error(`${r.status} ${await r.text()}`)
  return r.json() as Promise<T>
}

export const api = {
  health: () => fetch('/api/health').then((r) => j<{ ok: boolean; latest_data_version: number; demo: boolean }>(r)),
  post: (path: string) => fetch(path, { method: 'POST' }).then((r) => j<Record<string, any>>(r)),
  examples: () => fetch('/api/examples').then((r) => j<Example[]>(r)),
  cases: () => fetch('/api/cases').then((r) => j<CaseRow[]>(r)),
  tool: (tool: string, args: Record<string, unknown>) =>
    fetch(`/api/tools/${tool}`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(args) }).then((r) =>
      j<Record<string, any>>(r),
    ),
  graph: (caseId: string, planId?: string) =>
    fetch(`/api/cases/${caseId}/graph${planId ? `?plan_id=${planId}` : ''}`).then((r) => j<GraphView>(r)),
  startRun: (task: string, mode: 'live' | 'replay') =>
    fetch('/api/runs', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ task, mode }) }).then((r) =>
      j<{ run_id: string }>(r),
    ),
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
