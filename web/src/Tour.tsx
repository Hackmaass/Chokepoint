import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'

export type TourStep = {
  id: string
  /** value of the target's data-tour attribute; no target means a centred card */
  target?: string
  title: string
  body: string
  /** the step points at a result, so wait until the saved run has finished */
  needsResult?: boolean
}

type Rect = { left: number; top: number; width: number; height: number }
const PAD = 8
const GAP = 16
const POP_W = 348

function find(target?: string): HTMLElement | null {
  return target ? document.querySelector<HTMLElement>(`[data-tour="${target}"]`) : null
}

/** Where the card goes: beside the target when there is room, else below or above it, else docked at the bottom. */
function place(r: Rect | null, popH: number, vw: number, vh: number): { left: number; top: number } | null {
  if (!r) return null
  const w = Math.min(POP_W, vw - 24)
  const clampX = (x: number) => Math.max(12, Math.min(x, vw - w - 12))
  const clampY = (y: number) => Math.max(12, Math.min(y, vh - popH - 12))
  if (r.width > vw * 0.5 && r.height > popH + 80) return { left: clampX(r.left + r.width - w - 24), top: clampY(r.top + r.height - popH - 24) } // a wide target: sit in its empty corner
  if (r.left + r.width / 2 > vw / 2 && r.left - GAP - w >= 12) return { left: r.left - GAP - w, top: clampY(r.top) }
  if (r.left + r.width + GAP + w <= vw - 12) return { left: r.left + r.width + GAP, top: clampY(r.top) }
  if (r.top + r.height + GAP + popH <= vh - 12) return { left: clampX(r.left), top: r.top + r.height + GAP }
  if (r.top - GAP - popH >= 12) return { left: clampX(r.left), top: r.top - GAP - popH }
  return null
}

export function Tour({ steps, ready, onClose, onFinish }: { steps: TourStep[]; ready: boolean; onClose: () => void; onFinish: () => void }) {
  const [i, setI] = useState(0)
  const [rect, setRect] = useState<Rect | null>(null)
  const [popH, setPopH] = useState(180)
  const [vp, setVp] = useState({ w: window.innerWidth, h: window.innerHeight })
  const pop = useRef<HTMLDivElement>(null)
  const primary = useRef<HTMLButtonElement>(null)
  const step = steps[i]
  const last = i === steps.length - 1
  const waiting = !!step.needsResult && !ready
  const mobile = vp.w < 900

  const measure = useCallback(() => {
    const el = find(step.target)
    setVp({ w: window.innerWidth, h: window.innerHeight })
    if (!el) return setRect(null)
    const b = el.getBoundingClientRect()
    setRect({ left: b.left - PAD, top: b.top - PAD, width: b.width + PAD * 2, height: b.height + PAD * 2 })
  }, [step.target])

  // bring the target into view when the step changes (or its result arrives), then keep measuring while it settles
  useEffect(() => {
    const reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches
    const el = find(step.target)
    if (el && !waiting) el.scrollIntoView({ block: mobile ? 'start' : 'center', behavior: reduce ? 'auto' : 'smooth' })
    const first = window.requestAnimationFrame(measure)
    const id = window.setInterval(measure, 200)
    window.addEventListener('resize', measure)
    window.addEventListener('scroll', measure, true)
    return () => {
      window.cancelAnimationFrame(first)
      window.clearInterval(id)
      window.removeEventListener('resize', measure)
      window.removeEventListener('scroll', measure, true)
    }
  }, [step.target, waiting, mobile, measure])

  useLayoutEffect(() => {
    if (pop.current) setPopH(pop.current.offsetHeight)
  }, [i, waiting, vp.w])

  useEffect(() => {
    primary.current?.focus({ preventScroll: true })
  }, [i, waiting])

  const next = useCallback(() => (last ? onFinish() : setI((n) => n + 1)), [last, onFinish])
  const back = useCallback(() => setI((n) => Math.max(0, n - 1)), [])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
      else if (e.key === 'ArrowRight' && !waiting) next()
      else if (e.key === 'ArrowLeft') back()
      else if (e.key === 'Tab' && pop.current) {
        // keep focus inside the card while the tour is open
        const items = [...pop.current.querySelectorAll<HTMLElement>('button:not(:disabled)')]
        if (!items.length) return
        const first = items[0]
        const lastEl = items[items.length - 1]
        if (e.shiftKey && document.activeElement === first) { e.preventDefault(); lastEl.focus() }
        else if (!e.shiftKey && document.activeElement === lastEl) { e.preventDefault(); first.focus() }
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [next, back, onClose, waiting])

  const at = !mobile ? place(rect, popH, vp.w, vp.h) : null
  const style: React.CSSProperties = at ? { left: at.left, top: at.top, width: Math.min(POP_W, vp.w - 24) } : {}
  const docked = mobile || (!at && !!rect)

  return (
    <div className="tour" role="presentation">
      <div className="tour-block" aria-hidden />
      {rect && !waiting && <div className="tour-spot" style={{ left: rect.left, top: rect.top, width: rect.width, height: rect.height }} aria-hidden />}
      {!rect || waiting ? <div className="tour-dim" aria-hidden /> : null}
      <div
        ref={pop}
        className={`tour-card${docked ? ' docked' : ''}${rect ? '' : ' centred'}`}
        style={style}
        role="dialog"
        aria-modal="true"
        aria-labelledby="tour-title"
        aria-describedby="tour-body"
      >
        <div className="tour-progress" role="progressbar" aria-valuemin={1} aria-valuemax={steps.length} aria-valuenow={i + 1} aria-label={`Step ${i + 1} of ${steps.length}`}>
          {steps.map((s, n) => <span key={s.id} className={n < i ? 'done' : n === i ? 'now' : ''} />)}
        </div>
        <h2 id="tour-title" aria-live="polite">{step.title}</h2>
        <p id="tour-body">{waiting ? 'Playing the saved run…' : step.body}</p>
        <div className="tour-actions">
          <button className="btn small ghost" onClick={onClose}>{last ? 'Close' : 'Skip tour'}</button>
          <span className="tour-spacer" />
          {i > 0 && <button className="btn small" onClick={back}>Back</button>}
          <button ref={primary} className="btn small primary" disabled={waiting} onClick={next}>{last ? 'Explore on my own' : 'Next'}</button>
        </div>
        <p className="tour-keys" aria-hidden><kbd>←</kbd> <kbd>→</kbd> to move, <kbd>Esc</kbd> to leave</p>
      </div>
    </div>
  )
}

export function TourWelcome({ onStart, onSkip }: { onStart: () => void; onSkip: () => void }) {
  const go = useRef<HTMLButtonElement>(null)
  useEffect(() => {
    go.current?.focus()
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onSkip()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onSkip])
  return (
    <div className="tour" role="presentation">
      <div className="tour-block" aria-hidden />
      <div className="tour-dim" aria-hidden />
      <div className="tour-card welcome centred" role="dialog" aria-modal="true" aria-labelledby="welcome-title" aria-describedby="welcome-body">
        <svg className="welcome-glyph" viewBox="0 0 84 84" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" aria-hidden>
          <circle cx="14" cy="42" r="4" fill="currentColor" stroke="none" />
          <path d="M18 42h14M36 42l16-20M36 42h16M36 42l16 20" strokeDasharray="2 4" />
          <rect x="52" y="18" width="8" height="8" /><rect x="52" y="38" width="8" height="8" /><rect x="52" y="58" width="8" height="8" />
          <path d="M62 30l14 24M76 30L62 54" stroke="var(--data-frozen)" strokeWidth="2" />
        </svg>
        <h2 id="welcome-title">Where can stolen money still go?</h2>
        <p id="welcome-body">
          Take a one-minute walk through an example alert: the money at risk, the few accounts to freeze, and how the plan is checked.
          It plays a saved run on IBM's synthetic transfers, so it is instant and uses no model.
        </p>
        <div className="tour-actions">
          <button className="btn small ghost" onClick={onSkip}>Skip, I will explore</button>
          <span className="tour-spacer" />
          <button ref={go} className="btn primary" onClick={onStart}>Start the tour</button>
        </div>
        <p className="tour-keys">You can reopen it from the Tour button at the top.</p>
      </div>
    </div>
  )
}
