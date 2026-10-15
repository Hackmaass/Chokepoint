import { useEffect, useRef } from 'react'
import cytoscape, { type Core, type StylesheetJson } from 'cytoscape'
import { usd, type GraphView } from './api'

function css(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim()
}

const X_ICON =
  "data:image/svg+xml;utf8," +
  encodeURIComponent(
    "<svg xmlns='http://www.w3.org/2000/svg' width='24' height='24'><path d='M7 7l10 10M17 7L7 17' stroke='white' stroke-width='3' stroke-linecap='round'/></svg>",
  )

function styles(): StylesheetJson {
  const ink = css('--ink-primary')
  const muted = css('--ink-muted')
  const flow = css('--data-flow')
  const frozen = css('--data-frozen')
  const exit = css('--data-exit')
  const hair = css('--line-baseline')
  const surface = css('--surface-1')
  return [
    { selector: 'node', style: { width: 10, height: 10, 'background-color': muted, 'border-width': 2, 'border-color': surface } },
    { selector: 'node[role="origin"]', style: { shape: 'diamond', width: 26, height: 26, 'background-color': ink, label: 'alerted', 'font-size': 11, color: ink, 'text-valign': 'bottom', 'text-margin-y': 6 } },
    { selector: 'node[role="perimeter"]', style: { shape: 'square', width: 11, height: 11, 'background-color': exit } },
    { selector: 'node[role="frozen"]', style: { shape: 'ellipse', width: 24, height: 24, 'background-color': frozen, 'background-image': X_ICON, 'background-fit': 'contain', 'border-color': surface, 'border-width': 2, label: 'data(short)', 'font-size': 10, color: ink, 'text-valign': 'bottom', 'text-margin-y': 5, 'text-background-color': surface, 'text-background-opacity': 0.85, 'text-background-padding': '2px' } },
    { selector: 'node[role="rule"]', style: { shape: 'ellipse', width: 20, height: 20, 'background-color': surface, 'border-color': frozen, 'border-width': 2, 'border-style': 'dashed' } },
    { selector: 'node:selected', style: { 'border-color': ink, 'border-width': 3 } },
    { selector: 'edge', style: { width: 1, 'line-color': hair, 'target-arrow-color': hair, 'target-arrow-shape': 'triangle', 'arrow-scale': 0.7, 'curve-style': 'bezier', opacity: 0.8 } },
    { selector: 'edge[flow > 0]', style: { 'line-color': flow, 'target-arrow-color': flow, width: 'data(w)', opacity: 1 } },
    { selector: 'edge.cut', style: { 'line-color': hair, 'target-arrow-color': hair, width: 1, opacity: 0.5 } },
  ] as unknown as StylesheetJson
}

type Props = { view: GraphView | null; showRule: boolean; verified: boolean; theme: string }

export default function Graph({ view, showRule, verified, theme }: Props) {
  const host = useRef<HTMLDivElement>(null)
  const cy = useRef<Core | null>(null)

  useEffect(() => {
    if (!host.current) return
    cy.current?.destroy()
    if (!view) return
    const maxFlow = Math.max(1, ...view.edges.map((e) => e.flow_usd_cents))
    const origin = view.nodes.find((n) => n.role === 'origin')?.id
    cy.current = cytoscape({
      container: host.current,
      elements: [
        ...view.nodes.map((n) => ({
          data: { id: n.id, label: n.label, short: n.id.length > 12 ? `…${n.id.slice(-9)}` : n.id, role: !showRule && n.role === 'rule' ? 'other' : n.role, depth: n.depth },
        })),
        ...view.edges.map((e, i) => ({
          data: {
            id: `e${i}`,
            source: e.from,
            target: e.to,
            flow: e.flow_usd_cents,
            usd: e.usd_cents,
            w: 2 + 3 * Math.sqrt(e.flow_usd_cents / maxFlow),
          },
        })),
      ],
      style: styles(),
      layout: { name: 'breadthfirst', directed: true, roots: origin ? [origin] : undefined, spacingFactor: 1.15, padding: 24, animate: false } as cytoscape.LayoutOptions,
      minZoom: 0.2,
      maxZoom: 3,
    })
    const tip = document.getElementById('graph-tip')
    cy.current.on('mouseover', 'node', (ev) => {
      const n = ev.target
      if (tip) {
        tip.textContent = `${n.id()}  ·  ${n.data('role')}  ·  ${n.data('depth')} hop${n.data('depth') === 1 ? '' : 's'} from the alerted account`
        tip.style.opacity = '1'
      }
    })
    cy.current.on('mouseover', 'edge', (ev) => {
      const e = ev.target
      if (tip) {
        tip.textContent = `${e.source().id()} → ${e.target().id()}  ·  moved ${usd(e.data('usd'))}  ·  can carry ${usd(e.data('flow'))}`
        tip.style.opacity = '1'
      }
    })
    cy.current.on('mouseout', () => {
      if (tip) tip.style.opacity = '0'
    })
    return () => {
      cy.current?.destroy()
      cy.current = null
    }
  }, [view, showRule, theme])

  // The memorable moment: routes that carried money are shown first, then fade as the verified cut takes effect.
  useEffect(() => {
    const c = cy.current
    if (!c || !verified) return
    const edges = c.edges('[flow > 0]')
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
      edges.addClass('cut')
      return
    }
    const hair = css('--line-baseline')
    let inner: ReturnType<typeof setTimeout> | undefined
    const outer = setTimeout(() => {
      edges.forEach((e) => {
        e.animate({ style: { 'line-color': hair, 'target-arrow-color': hair, width: 1, opacity: 0.5 } }, { duration: 700, easing: 'ease-out' })
      })
      inner = setTimeout(() => edges.addClass('cut'), 760)
    }, 1400)
    return () => {
      clearTimeout(outer)
      if (inner) clearTimeout(inner)
    }
  }, [verified, view])

  return (
    <>
      <div ref={host} className="canvas" role="img" aria-label="Graph of accounts the money can reach. The accounts list in the Compare tab is the keyboard alternative." />
      <div className="toolbar">
        <button className="btn small" onClick={() => cy.current?.fit(undefined, 24)}>Fit</button>
        <button className="btn small" onClick={() => cy.current?.zoom(cy.current.zoom() * 1.25)}>Zoom in</button>
        <button className="btn small" onClick={() => cy.current?.zoom(cy.current.zoom() / 1.25)}>Zoom out</button>
      </div>
      <div id="graph-tip" className="mono" style={{ position: 'absolute', left: 12, bottom: 12, right: 12, opacity: 0, background: 'var(--surface-1)', border: '1px solid var(--border-ring)', borderRadius: 6, padding: '4px 8px', pointerEvents: 'none', boxShadow: '0 4px 16px rgba(0,0,0,.12)' }} />
    </>
  )
}
