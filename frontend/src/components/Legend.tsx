import { useState, type ReactNode } from 'react'
import type { Run, Video } from '../api'
import { clipColor } from '../format'
import { cameraUse } from '../words'
import { IconClose, IconLayers } from './icons'
import { Hint } from './ui'
import type { LayerState } from './MapView'

function Row({ swatch, title, desc, checked, onChange, disabled, children }: {
  swatch: ReactNode; title: string; desc?: string; checked?: boolean; onChange?: (v: boolean) => void
  disabled?: boolean; children?: ReactNode
}) {
  return (
    <div className={disabled ? 'opacity-40' : ''}>
      <div className="flex items-center gap-2">
        <label className="flex min-w-0 flex-1 cursor-pointer items-center gap-2">
          {onChange && <input type="checkbox" disabled={disabled} checked={!!checked} onChange={(e) => onChange(e.target.checked)} />}
          <span className="flex w-5 shrink-0 justify-center">{swatch}</span>
          <span className="truncate text-slate-100">{title}</span>
        </label>
        {desc && <Hint title={title} side="right" size={13}>{desc}</Hint>}
      </div>
      {children && <div className="ml-12 mt-1">{children}</div>}
    </div>
  )
}

const Group = ({ title, hint, children, right }: { title: string; hint?: string; children: ReactNode; right?: ReactNode }) => (
  <div>
    <div className="mb-1.5 flex items-center justify-between text-[11px] font-semibold text-slate-400">
      <span className="flex items-center gap-1">{title}{hint && <Hint title={title} side="right" size={12}>{hint}</Hint>}</span>{right}
    </div>
    <div className="space-y-1.5">{children}</div>
  </div>
)

const line = (color: string) => <span className="block h-1 w-5 rounded" style={{ background: color }} />
const dot = (color: string, ring = 'var(--color-slate-500)') => <span className="block h-3 w-3 rounded-full" style={{ background: color, boxShadow: `0 0 0 1.5px ${ring}` }} />
const patch = (fill: string, border: string) => <span className="block h-3.5 w-5 rounded-sm" style={{ background: fill, border: `1.5px solid ${border}` }} />

export function Legend({ layers, setLayers, run, videos, hiddenClips, setHiddenClips, hasFrames, hasTrees = false }: {
  layers: LayerState; setLayers: (l: LayerState) => void; run: Run | null; videos: Video[]
  hiddenClips: number[]; setHiddenClips: (h: number[]) => void; hasFrames: boolean; hasTrees?: boolean
}) {
  const [open, setOpen] = useState(true)
  const set = (patch_: Partial<LayerState>) => setLayers({ ...layers, ...patch_ })
  const legend = run?.layers?.dsm?.legend
  if (!open) {
    return (
      <button onClick={() => setOpen(true)} className="absolute left-3 top-12 z-10 inline-flex items-center gap-1.5 rounded-lg border border-slate-700 bg-slate-900/95 px-3 py-1.5 text-xs font-medium text-slate-100 shadow-[var(--shadow-sm)] hover:bg-slate-800">
        <IconLayers size={14} /> Map key
      </button>
    )
  }
  return (
    <div className="absolute left-3 top-12 z-10 flex max-h-[calc(100%-5.5rem)] w-60 flex-col rounded-xl border border-slate-700 bg-slate-900/95 text-xs shadow-[var(--shadow-md)] backdrop-blur-sm">
      <div className="flex items-center justify-between px-3 pb-1 pt-2.5">
        <span className="flex items-center gap-1.5 text-[13px] font-semibold text-slate-100"><IconLayers size={14} className="text-saffron-400" />Map key
          <Hint title="Map key" side="right">What each line, colour and area on the map means. Tick a row to show it, untick to hide it. Hover the map itself to see what anything is.</Hint></span>
        <button onClick={() => setOpen(false)} aria-label="Hide map key" className="rounded p-0.5 text-slate-400 hover:bg-slate-800 hover:text-slate-100"><IconClose size={14} /></button>
      </div>
      <div className="min-h-0 flex-1 space-y-3 overflow-auto px-3 pb-3 pt-1">
        <Group title="Background">
          <div className="flex rounded-lg bg-slate-800 p-0.5">
            {([['osm', 'Street map'], ['imagery', 'Satellite (old)']] as const).map(([b, label]) => (
              <button key={b} onClick={() => set({ basemap: b })}
                className={`flex-1 rounded-md px-2 py-1 transition-colors ${layers.basemap === b ? 'bg-slate-600 text-white' : 'text-slate-300 hover:text-white'}`}>{label}</button>
            ))}
          </div>
          {layers.basemap === 'imagery' && <p className="text-[11px] text-slate-400">Esri imagery, several years old. For comparison only.</p>}
        </Group>

        <Group title="Your stitched map" hint="The layers Naqsha made from the drone video. They appear once a map is built.">
          <Row swatch={patch('linear-gradient(135deg,#2f6b2f,#8fbf6a)', '#94a3b8')} title="Top-down image"
            desc={run?.layers?.orthophoto ? 'The stitched map made from the drone video. Use the slider to fade it in and out against the background.' : 'Appears here after you build a map.'}
            checked={layers.ortho} onChange={(v) => set({ ortho: v })} disabled={!run?.layers?.orthophoto}>
            {run?.layers?.orthophoto && layers.ortho && (
              <input type="range" aria-label="Map opacity" min={0} max={1} step={0.05} value={layers.orthoOpacity} onChange={(e) => set({ orthoOpacity: +e.target.value })} className="w-full" />
            )}
          </Row>
          {run?.layers?.sharp && (
            <Row swatch={patch('linear-gradient(135deg,#1f5a1f,#b4e08a)', '#94a3b8')} title="Sharp map"
              desc="The same ground rebuilt straight from the raw drone frames, without the stitching smears. Small plants show up here. Tall crowns keep the stitched map's pixels."
              checked={layers.sharp} onChange={(v) => set({ sharp: v })} />
          )}
          <Row swatch={patch('linear-gradient(90deg,#333399,#00b3b3,#e6e680,#cc9933,#fff)', '#94a3b8')} title="Elevation"
            desc="Height of the surface, including trees and buildings. Blue is low, white is high." checked={layers.dsm} onChange={(v) => set({ dsm: v })} disabled={!run?.layers?.dsm}>
            {layers.dsm && legend && (
              <div>
                <div className="h-2 rounded" style={{ background: `linear-gradient(to right, ${legend.stops.map((s: any) => `rgb(${s[1].join(',')}) ${s[0] * 100}%`).join(',')})` }} />
                <div className="num flex justify-between text-[10px] text-slate-400"><span>{legend.min} m</span><span>{legend.max} m</span></div>
              </div>
            )}
          </Row>
          <Row swatch={patch('rgba(239,68,68,0.35)', '#f87171')} title="Gaps"
            desc="Ground the drone flew over that could not be stitched, usually water, dense canopy or the edges of a flight." checked={layers.holes} onChange={(v) => set({ holes: v })} disabled={!run?.qa?.holes} />
        </Group>

        {hasTrees && (
          <Group title="Trees" hint="From the Trees tab: every tree Naqsha counted on this map, and the squares you checked by hand.">
            <Row swatch={<span className="block h-3 w-5 rounded-full" style={{ background: 'linear-gradient(90deg,#74ddd3,#1aa69d,#f9b850,#ef4444)' }} />}
              title="Trees" desc="One dot per tree, coloured by height: teal is short, saffron tall, red very tall. Grey means the height could not be measured."
              checked={layers.trees} onChange={(v) => set({ trees: v })} />
            <Row swatch={patch('transparent', '#b6efe9')} title="Crown outlines" desc="Approximate width of each tree's crown."
              checked={layers.crowns} onChange={(v) => set({ crowns: v })} />
            <Row swatch={patch('linear-gradient(90deg,#0f8a82,#f4a62a)', '#94a3b8')} title="Tree density" desc="Trees per hectare in 20 m squares: teal sparse, saffron dense."
              checked={layers.density} onChange={(v) => set({ density: v })} />
            <Row swatch={<span className="flex gap-0.5">{patch('rgba(244,166,42,0.1)', '#f4a62a')}</span>} title="Sample squares" desc="Squares where every tree is marked by hand. Saffron test squares grade the count; teal teaching squares teach the model. Dashed means not marked yet."
              checked={layers.plots} onChange={(v) => set({ plots: v })} />
          </Group>
        )}

        <Group title="Flight paths" hint="One colour per video clip. Untick a clip to hide its path."
          right={<label className="flex items-center gap-1 font-normal"><input type="checkbox" checked={layers.track} onChange={(e) => set({ track: e.target.checked })} />all</label>}>
          {videos.map((v, i) => {
            const use = cameraUse(v.stats?.gimbal_pitch_median)
            return (
              <Row key={v.id} swatch={line(clipColor(i))} title={v.name.replace(/^DJI_\d{14}_/, 'Clip ').replace(/(_D)?\.\w+$/, '')}
                desc={`Camera ${use.label}, ${use.detail}.`} checked={layers.track && !hiddenClips.includes(v.id)}
                onChange={(on) => setHiddenClips(on ? hiddenClips.filter((x) => x !== v.id) : [...hiddenClips, v.id])} />
            )
          })}
          <Row swatch={dot('#0ea5e9', '#fff')} title="Takeoff points" desc="Where each flight started." />
        </Group>

        <Group title="Planning layers" hint="Estimates made before stitching: what the camera covered and which frames were picked as photos.">
          <Row swatch={patch('rgba(34,197,94,0.2)', '#4ade80')} title="Ground the camera saw" desc="Estimated from GPS, height and camera angle. Pick how strict to be in the drop-down under it."
            checked={layers.coverage !== 'off'} onChange={(v) => set({ coverage: v ? 'mappable' : 'off' })}>
            {layers.coverage !== 'off' && (
              <select value={layers.coverage} onChange={(e) => set({ coverage: e.target.value as any })} className="w-full rounded-md border border-slate-700 bg-slate-800 px-1 py-0.5">
                <option value="nadir">only straight-down views</option>
                <option value="mappable">views usable for mapping</option>
                <option value="all">everything the camera saw</option>
              </select>
            )}
          </Row>
          <Row swatch={dot('#f8fafc')} title="Photos used" desc="Each dot is one video frame chosen as a photo; click one to see it. After a build, red dots landed far from their GPS and were left out, purple ones could not be fitted in."
            checked={layers.frames} onChange={(v) => set({ frames: v })} disabled={!hasFrames} />
        </Group>
      </div>
    </div>
  )
}
