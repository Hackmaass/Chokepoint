import { useEffect, useMemo, useRef, useState } from 'react'
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
          const w = route ? 1.6 + 3.6 * Math.sqrt(f / maxFlow) : 1.2
          return <path key={i} d={path(a, b)} className={`e${route ? ' route' : ''}${route && cut ? ' cut' : ''}`} style={{ strokeWidth: route && !cut ? w : route ? 1.6 : 1.2 }} />
        })}
        {!cut && !reduced && LANDING_EDGES.filter((e) => e[2] > 0).map(([a, b], i) => (
          <circle key={`p${i}`} r="3" className="pulse">
            <animateMotion dur={`${2 + (i % 4) * 0.35}s`} begin={`${(i % 5) * 0.3}s`} repeatCount="indefinite" path={path(a, b)} />
          </circle>
        ))}
        {LANDING_NODES.map(([id, d, role]) => {
          const { x, y } = pos[id]
          if (role === 'origin') return <path key={id} d={`M${x},${y - 15} L${x + 15},${y} L${x},${y + 15} L${x - 15},${y} Z`} className="n origin" />
          if (role === 'frozen') {
            return (
              <g key={id} transform={`translate(${x} ${y})`}>
                <g className={`frozen${cut ? ' on' : ''}`}>
                  <circle r="13" />
                  <path d="M-5 -5l10 10M5 -5l-10 10" />
                </g>
              </g>
            )
          }
          return d === 3 ? <rect key={id} x={x - 5.5} y={y - 5.5} width="11" height="11" className="n exit" /> : <circle key={id} cx={x} cy={y} r="5" className="n" />
        })}
      </svg>
      <figcaption>
        <p className="fig-state">
          <span className="fig-num">{cut ? '$0' : LANDING_FACTS.movable}</span>
          <span>{cut ? 'can still reach the perimeter after freezing 3 accounts, checked on a copy of the graph in the recorded transfers' : 'could reach the perimeter along these routes, a ceiling from past transfers'}</span>
        </p>
        <p className="fig-meta">
          <span>Alert {LANDING_FACTS.alert} in IBM’s synthetic transfers, 7 days before the alert, 3 hops out</span>
          <button className="lp-link" onClick={() => setPhase('flow')}>Replay</button>
        </p>
      </figcaption>
    </figure>
  )
}

const STEPS = [
  { img: 's1-alert', alt: 'The alert form: an example alert, the alerted account and the alert time, with an Investigate button', t: 'Open the alert', d: 'Start from one account and a time. Fixed queries in FalkorDB read the 7 days of transfers before the alert, three hops out. The agent never writes a query.' },
  { img: 's2-poster', video: '/shots/s2-routes.mp4', alt: 'A graph of the routes from the alerted account to the perimeter, with the frozen accounts marked', t: 'See every route', d: 'The routes the money could have taken to the perimeter, and the most it could move along them: a ceiling worked out from past transfers, not a forecast.' },
  { img: 's3-plan', alt: 'The decision card: $22,410 at risk, freeze 3 accounts, a verified chip and the accounts with amounts received', t: 'Cut them', d: 'A minimum-cost vertex cut: the cheapest set of accounts that every route passes through. FalkorDB’s max-flow runs first, and its answer is used only if it passes a certificate.' },
  { img: 's4-check', alt: 'A green check reading verified: no route left to the 3-hop perimeter, and $0 after the freeze', t: 'Check on a copy', d: 'The frozen accounts are deleted from a copy of the graph and the search runs again. It is a consistency check on the same data, not proof about the real world.' },
  { img: 's5-receipt', alt: 'A receipt: the finding, the tool run that produced it, its parameters, the data version and the accounts it read', t: 'Keep the receipt', d: 'Each result is a node tied to the tool run, its parameters, the data version and the accounts it read. When late transfers arrive, only findings that read those accounts are re-run.' },
]

/** Two results with their 95% intervals, drawn on one scale from the held-out evaluation. */
function Intervals() {
  const lo = -0.2
  const hi = 0.4
  const x = (v: number) => 24 + ((v - lo) / (hi - lo)) * 472
  const rows = [
    { y: 64, label: 'Blocked share, difference from the rule of thumb', est: -0.002, a: -0.09, b: 0.08, note: 'Straddles zero: about the same', fmt: '−0.002, interval −0.09 to +0.08' },
    { y: 188, label: 'Precision of the frozen accounts, difference', est: 0.19, a: 0.07, b: 0.32, note: 'Above zero: more often part of the laundering', fmt: '+0.19, interval +0.07 to +0.32' },
  ]
  return (
    <figure className="intervals">
      <svg viewBox="0 0 520 292" role="img" aria-label="Held-out evaluation: blocked share differs from the rule of thumb by minus 0.002, interval minus 0.09 to plus 0.08; precision is higher by 0.19, interval 0.07 to 0.32">
        {rows.map((r) => (
          <g key={r.label}>
            <text x="24" y={r.y - 30} className="t-label">{r.label}</text>
            <rect x={x(0) - 1.5} y={r.y - 16} width="3" height="32" className="zero" />
            <rect x={x(r.a)} y={r.y - 6} width={x(r.b) - x(r.a)} height="12" rx="6" className="ci" />
            <circle cx={x(r.est)} cy={r.y} r="10" className="pt" />
            <text x="24" y={r.y + 40} className="t-note">{r.note}</text>
            <text x="24" y={r.y + 62} className="t-tick">{r.fmt}</text>
          </g>
        ))}
        {[-0.2, 0, 0.2, 0.4].map((t) => (
          <text key={t} x={x(t)} y="284" textAnchor="middle" className="t-tick">{t > 0 ? '+' : t < 0 ? '−' : ''}{Math.abs(t).toFixed(1)}</text>
        ))}
      </svg>
      <figcaption>Held-out half of the cases, at equal budget, against the best simple rule of thumb.</figcaption>
    </figure>
  )
}

function go(hash: string) {
  window.location.hash = hash
}

function scrollTo(id: string) {
  document.getElementById(id)?.scrollIntoView({ behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth' })
}

const SECTIONS: [string, string][] = [['how', 'How it works'], ['different', 'What is different'], ['numbers', 'The numbers']]

export default function Landing() {
  const root = useRef<HTMLDivElement>(null)
  const [active, setActive] = useState('')
  useEffect(() => {
    document.title = 'Chokepoint: which mule accounts to freeze'
  }, [])
  // A looping clip is motion: show its poster frame instead when the visitor asks for less.
  useEffect(() => {
    if (!window.matchMedia('(prefers-reduced-motion: reduce)').matches) return
    root.current?.querySelectorAll('video').forEach((v) => { v.pause(); v.removeAttribute('autoplay') })
  }, [])
  // Entrances for the parts of each section, once, as they scroll in. Nothing is hidden unless this script has run.
  useEffect(() => {
    const el = root.current
    if (!el) return
    const reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches
    const items = [...el.querySelectorAll<HTMLElement>('[data-reveal]')]
    if (reduce || !('IntersectionObserver' in window)) { items.forEach((i) => i.classList.add('in')); return }
    el.classList.add('reveal-ready')
    const io = new IntersectionObserver((entries) => {
      for (const e of entries) if (e.isIntersecting || e.boundingClientRect.top < 0) { (e.target as HTMLElement).classList.add('in'); io.unobserve(e.target) } // also anything scrolled past in a jump
    }, { rootMargin: '0px 0px -8% 0px', threshold: 0.12 })
    items.forEach((i) => io.observe(i))
    return () => io.disconnect()
  }, [])
  // Which section is on screen, for the nav.
  useEffect(() => {
    const els = SECTIONS.map(([id]) => document.getElementById(id)).filter((e): e is HTMLElement => !!e)
    if (!('IntersectionObserver' in window)) return
    const io = new IntersectionObserver((entries) => {
      for (const e of entries) if (e.isIntersecting) setActive(e.target.id)
    }, { rootMargin: '-45% 0px -50% 0px' })
    els.forEach((e) => io.observe(e))
    return () => io.disconnect()
  }, [])
  return (
    <div className="lp" ref={root}>
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
          {SECTIONS.map(([id, label]) => (
            <button key={id} className={`lp-link${active === id ? ' current' : ''}`} aria-current={active === id ? 'true' : undefined} onClick={() => scrollTo(id)}>{label}</button>
          ))}
        </nav>
        <button className="btn small primary" onClick={() => go('#/app')}>Open the demo</button>
      </header>

      <main id="main">
        <section className="lp-hero">
          <div className="lp-hero-copy">
            <h1>Which accounts do you freeze?</h1>
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

        <section id="how" className="sec band">
          <h2 data-reveal>From an alert to a receipt</h2>
          <ol className="steps">
            {STEPS.map((s, i) => (
              <li key={s.t} className={i % 2 ? 'flip' : ''}>
                <div className="step-text" data-reveal>
                  <h3>{s.t}</h3>
                  <p>{s.d}</p>
                </div>
                {s.video ? (
                  <video className="step-img" data-reveal style={{ ['--i' as string]: 1 }} src={s.video} poster={`/shots/${s.img}.webp`} aria-label={s.alt} width="900" height="532" autoPlay muted loop playsInline preload="metadata" />
                ) : (
                  <img className="step-img" data-reveal style={{ ['--i' as string]: 1 }} src={`/shots/${s.img}.webp`} alt={s.alt} width="900" height="560" />
                )}
              </li>
            ))}
          </ol>
        </section>

        <section id="different" className="sec">
          <h2 data-reveal>What is different</h2>
          <div className="diff">
            <article className="diff-a" data-reveal>
              <h3>It plans the intervention</h3>
              <p>
                Most tools score accounts as suspicious and stop there. Chokepoint answers the next question: which few accounts, if frozen, leave no route to the perimeter.
                The Compare tab sets that plan beside a same-size rule of thumb on the same case.
              </p>
            </article>
            <article className="diff-b" data-reveal style={{ ['--i' as string]: 1 }}>
              <h3>It checked its own database</h3>
              <p>
                On about 17% of the node-split networks we generated, FalkorDB’s <code>algo.maxFlow</code> returned a flow that was not maximal. We wrote an 11-edge repro, and now accept
                its answer only with a certificate; otherwise an exact solver runs. In testing, 0 false accepts across 120 random networks.
              </p>
            </article>
            <article className="diff-c" data-reveal>
              <h3>It checks time order</h3>
              <p>
                Money cannot go back in time. A timing check keeps only routes whose transfers happen in order. On the 8 example alerts, 3 have no time-ordered route at all and 2 need fewer accounts.
              </p>
            </article>
            <article className="diff-d" data-reveal style={{ ['--i' as string]: 1 }}>
              <h3>It handles late data</h3>
              <p>
                When a late transfer arrives, only the findings that read the touched accounts are re-run. Old findings are kept and marked superseded, so you can see what changed.
              </p>
            </article>
          </div>
        </section>

        <section id="numbers" className="sec band">
          <h2 data-reveal>The numbers, stated plainly</h2>
          <div className="nums-grid">
            <dl className="nums" data-reveal>
              <div>
                <dt>Blocked share of laundering that had already escaped</dt>
                <dd>About the same as the best simple rule of thumb, at equal budget, on the held-out half.</dd>
              </div>
              <div>
                <dt>Precision of the frozen accounts</dt>
                <dd>Higher by 0.19: its accounts were more often part of the laundering.</dd>
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
            <div data-reveal style={{ ['--i' as string]: 1 }}><Intervals /></div>
          </div>
        </section>

        <section className="close">
          <h2 data-reveal>See one alert end to end</h2>
          <p data-reveal style={{ ['--i' as string]: 1 }}>Open the demo and press the button, or take the one-minute tour that points at each part.</p>
          <div className="lp-hero-cta" data-reveal style={{ ['--i' as string]: 2 }}>
            <button className="btn primary big" onClick={() => go('#/app')}>Open the demo</button>
            <button className="btn big" onClick={() => go('#/app/tour')}>Take the tour</button>
          </div>
        </section>
      </main>

      <footer className="lp-foot">
        <p className="lp-wordmark" aria-hidden>Chokepoint</p>
        <div className="lp-foot-row">
          <span>Built for Graph Hacks 2026 on FalkorDB. All data is synthetic; a freeze plan is a recommendation, not an instruction.</span>
          <span>
            <a href="https://github.com/Hackmaass/Chokepoint" target="_blank" rel="noreferrer">Source</a>
            <a href="https://github.com/Hackmaass/Chokepoint/blob/main/docs/ai-work-log.md" target="_blank" rel="noreferrer">How AI was used</a>
            <span className="build">Build {__BUILD__}</span>
          </span>
        </div>
      </footer>
    </div>
  )
}
