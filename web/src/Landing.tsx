import { useEffect, useMemo, useState } from 'react'
import './landing.css'
import { LANDING_EDGES, LANDING_FACTS, LANDING_NODES } from './landingData'

const W = 680
const H = 430
const ROWS = [46, 156, 272, 388]

/** A layered drawing of the real routes for one example alert, then the cut. */
function layout() {
  const byDepth = new Map<number, string[]>()
  for (const [id, d] of LANDING_NODES) byDepth.set(d, [...(byDepth.get(d) ?? []), id])
  const pos: Record<string, { x: number; y: number }> = {}
  const pad = 40
  for (const d of [...byDepth.keys()].sort()) {
    const ids = byDepth.get(d)!
    if (d >= 2) {
      const bary = (id: string) => {
        const xs = LANDING_EDGES.filter(([, to]) => to === id).map(([from]) => pos[from]?.x).filter((x): x is number => x !== undefined)
        return xs.length ? xs.reduce((a, b) => a + b, 0) / xs.length : W / 2
      }
      ids.sort((a, b) => bary(a) - bary(b))
    } else ids.sort()
    ids.forEach((id, i) => { pos[id] = { x: pad + ((i + 0.5) * (W - 2 * pad)) / ids.length, y: ROWS[d] } })
  }
  return pos
}

function CutFigure() {
  const reduced = typeof window !== 'undefined' && window.matchMedia('(prefers-reduced-motion: reduce)').matches
  const [phase, setPhase] = useState<'flow' | 'cut'>(reduced ? 'cut' : 'flow')
  const pos = useMemo(layout, [])
  const maxFlow = Math.max(...LANDING_EDGES.map((e) => e[2]))
  useEffect(() => {
    if (reduced) return
    const t = window.setTimeout(() => setPhase((p) => (p === 'flow' ? 'cut' : 'flow')), phase === 'flow' ? 4800 : 5600)
    return () => window.clearTimeout(t)
  }, [phase, reduced])
  const cut = phase === 'cut'
  const path = (a: string, b: string) => {
    const p = pos[a]
    const q = pos[b]
    const m = (p.y + q.y) / 2
    return `M${p.x},${p.y} C${p.x},${m} ${q.x},${m} ${q.x},${q.y}`
  }
  return (
    <figure className="fig">
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={`Routes from alerted account ${LANDING_FACTS.alert} to the perimeter, three hops out, and the three accounts whose freeze cuts them`}>
        {LANDING_EDGES.map(([a, b, f], i) => {
          const route = f > 0
          const w = route ? 1.4 + 3.4 * Math.sqrt(f / maxFlow) : 1
          return <path key={i} d={path(a, b)} className={`e${route ? ' route' : ''}${route && cut ? ' cut' : ''}`} style={{ strokeWidth: route && !cut ? w : route ? 1.4 : 1 }} />
        })}
        {!cut && !reduced && LANDING_EDGES.filter((e) => e[2] > 0).map(([a, b], i) => (
          <circle key={`p${i}`} r="2.8" className="pulse">
            <animateMotion dur={`${2 + (i % 4) * 0.35}s`} begin={`${(i % 5) * 0.3}s`} repeatCount="indefinite" path={path(a, b)} />
          </circle>
        ))}
        {LANDING_NODES.map(([id, d, role]) => {
          const { x, y } = pos[id]
          if (role === 'origin') return <path key={id} d={`M${x},${y - 14} L${x + 14},${y} L${x},${y + 14} L${x - 14},${y} Z`} className="n origin" />
          if (role === 'frozen') {
            return (
              <g key={id} transform={`translate(${x} ${y})`}>
                <g className={`frozen${cut ? ' on' : ''}`}>
                  <circle r="12" />
                  <path d="M-4.5 -4.5l9 9M4.5 -4.5l-9 9" />
                </g>
              </g>
            )
          }
          return d === 3 ? <rect key={id} x={x - 5} y={y - 5} width="10" height="10" className="n exit" /> : <circle key={id} cx={x} cy={y} r="4.5" className="n" />
        })}
      </svg>
      <figcaption>
        <div className="fig-state" aria-live="off">
          <span className="fig-num">{cut ? '$0' : LANDING_FACTS.movable}</span>
          <span>{cut ? 'can still reach the perimeter after freezing 3 accounts, checked on a copy of the graph in the recorded transfers' : 'could reach the perimeter along these routes, a ceiling from past transfers'}</span>
        </div>
        <div className="fig-meta">
          <span>Alert {LANDING_FACTS.alert}, IBM synthetic transfers, 7 days before the alert, 3 hops out</span>
          <button className="lp-link" onClick={() => setPhase('flow')}>Replay</button>
        </div>
      </figcaption>
    </figure>
  )
}

const STEPS = [
  { t: 'Open the alert', d: 'Start from one account and a time. Fixed queries in FalkorDB read the 7 days of transfers before the alert, three hops out. The agent never writes a query.' },
  { t: 'See every route', d: 'The routes the money could have taken to the perimeter, and the most it could move along them: a ceiling worked out from past transfers, not a forecast.' },
  { t: 'Cut them', d: 'A minimum-cost vertex cut: the cheapest set of accounts that every route passes through. FalkorDB’s max-flow runs first, and its answer is used only if it passes a certificate.' },
  { t: 'Check on a copy', d: 'The frozen accounts are deleted from a copy of the graph and the search runs again. It is a consistency check on the same data, not proof about the real world.' },
  { t: 'Keep the receipt', d: 'Each result is a node tied to the tool run, its parameters, the data version and the accounts it read. When late transfers arrive, only findings that read those accounts are re-run.' },
]

function go(hash: string) {
  window.location.hash = hash
}

function scrollTo(id: string) {
  document.getElementById(id)?.scrollIntoView({ behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth' })
}

export default function Landing() {
  useEffect(() => {
    document.title = 'Chokepoint: which mule accounts to freeze'
  }, [])
  return (
    <div className="lp">
      <a className="skip" href="#main" onClick={(e) => { e.preventDefault(); scrollTo('main') }}>Skip to content</a>
      <header className="lp-nav">
        <a className="lp-brand" href="#/" aria-label="Chokepoint home">
          <svg viewBox="0 0 26 26" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" aria-hidden>
            <path d="M3 5l9 8M3 21l9-8M14 13h9" />
            <circle cx="13" cy="13" r="3" fill="currentColor" stroke="none" />
            <path d="M18.5 8.5l5 9" stroke="var(--data-frozen)" />
          </svg>
          Chokepoint
        </a>
        <nav aria-label="Sections">
          <button className="lp-link" onClick={() => scrollTo('how')}>How it works</button>
          <button className="lp-link" onClick={() => scrollTo('different')}>What is different</button>
          <button className="lp-link" onClick={() => scrollTo('numbers')}>The numbers</button>
        </nav>
        <button className="btn small primary" onClick={() => go('#/app')}>Open the demo</button>
      </header>

      <main id="main">
        <section className="lp-hero">
          <div className="lp-hero-copy">
            <h1>Which accounts do you freeze to stop the money?</h1>
            <p className="lede">
              Stolen money does not sit still. Chokepoint takes one alert on a mule account and picks the few accounts whose freeze cuts every route the money could have taken through past transfers.
              <span className="more"> It checks the plan on a copy of the graph and keeps a receipt.</span>
            </p>
            <div className="lp-hero-cta">
              <button className="btn primary big" onClick={() => go('#/app')}>Open the demo</button>
              <button className="btn big" onClick={() => go('#/app/tour')}>Take the one-minute tour</button>
            </div>
            <p className="fine">Synthetic data only. The demo replays a saved agent run, so it is instant and needs no model key.</p>
          </div>
          <CutFigure />
        </section>

        <section id="how" className="sec">
          <h2>From an alert to a receipt</h2>
          <div className="lp-how">
            <ol className="rail">
              {STEPS.map((s) => (
                <li key={s.t}>
                  <h3>{s.t}</h3>
                  <p>{s.d}</p>
                </li>
              ))}
            </ol>
            <figure className="shot">
              <img src="/app-dark.webp" width="1440" height="900" alt="The Chokepoint app on the example alert: $22,410 could reach the perimeter, three accounts to freeze, a green check, and the routes drawn as a graph" />
              <figcaption>The app on the example alert, in Replay. Everything on the card comes from a tool run.</figcaption>
            </figure>
          </div>
        </section>

        <section id="different" className="sec">
          <h2>What is different</h2>
          <div className="diff">
            <article className="diff-a">
              <h3>It plans the intervention</h3>
              <p>
                Most tools score accounts as suspicious and stop there. Chokepoint answers the next question: which few accounts, if frozen, leave no route to the perimeter.
                The Compare tab sets that plan beside a same-size rule of thumb on the same case.
              </p>
            </article>
            <article className="diff-b">
              <h3>It checked its own database</h3>
              <p>
                On about 17% of the node-split networks we generated, FalkorDB’s <code>algo.maxFlow</code> returned a flow that was not maximal. We wrote an 11-edge repro, and now accept
                its answer only with a certificate; otherwise an exact solver runs. In testing, 0 false accepts across 120 random networks.
              </p>
            </article>
            <article className="diff-c">
              <h3>It checks time order</h3>
              <p>
                Money cannot go back in time. A timing check keeps only routes whose transfers happen in order. On the 8 example alerts, 3 have no time-ordered route at all and 2 need fewer accounts.
              </p>
            </article>
            <article className="diff-d">
              <h3>It handles late data</h3>
              <p>
                When a late transfer arrives, only the findings that read the touched accounts are re-run. Old findings are kept and marked superseded, so you can see what changed.
              </p>
            </article>
          </div>
        </section>

        <section id="numbers" className="sec">
          <h2>The numbers, stated plainly</h2>
          <dl className="nums">
            <div>
              <dt>Blocked share of laundering that had already escaped</dt>
              <dd>About the same as the best simple rule of thumb: difference −0.002, 95% interval [−0.09, +0.08], on the held-out half, at equal budget.</dd>
            </div>
            <div>
              <dt>Precision of the frozen accounts</dt>
              <dd>Higher by 0.19, interval [+0.07, +0.32]: its accounts were more often part of the laundering.</dd>
            </div>
            <div>
              <dt>The check</dt>
              <dd>The plan leaves no route to the perimeter in the recorded transfers, tested on a copy of the graph.</dd>
            </div>
            <div>
              <dt>What we do not claim</dt>
              <dd>That it blocks more than the best simple rule, that results carry over to real data, or that the demo alerts are typical: they were picked where the rule of thumb leaves money movable.</dd>
            </div>
          </dl>
        </section>

        <section className="sec close">
          <h2>See one alert end to end</h2>
          <p>Open the demo and press the button, or take the one-minute tour that points at each part.</p>
          <div className="lp-hero-cta">
            <button className="btn primary big" onClick={() => go('#/app')}>Open the demo</button>
            <button className="btn big" onClick={() => go('#/app/tour')}>Take the tour</button>
          </div>
        </section>
      </main>

      <footer className="lp-foot">
        <span>Built for Graph Hacks 2026 on FalkorDB. All data is synthetic; a freeze plan is a recommendation, not an instruction.</span>
        <span>
          <a href="https://github.com/Hackmaass/Chokepoint" target="_blank" rel="noreferrer">Source</a>
          <a href="https://github.com/Hackmaass/Chokepoint/blob/main/docs/ai-work-log.md" target="_blank" rel="noreferrer">How AI was used</a>
        </span>
      </footer>
    </div>
  )
}
