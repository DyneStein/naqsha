import { useState } from 'react'
import { api, type Project, type Selection, type System } from '../api'
import { fmtDur, fmtTime } from '../format'
import { cameraUse } from '../words'
import { Badge, Button, Field, Fold, Hint, inputCls, Section, Warnings } from './ui'

const QUALITY = {
  preview: { name: 'Quick preview', tag: '5 cm/px, fast', text: 'A few minutes. 5 cm per pixel, no elevation map. Good for checking coverage.' },
  standard: { name: 'Standard', tag: '2.5 cm/px, recommended', text: 'Recommended. About 2.5 cm per pixel plus an elevation map.' },
  max: { name: 'Maximum', tag: '1.5 cm/px + 3D, slow', text: '1.5 cm per pixel and a 3D model. Slow; best on the GPU computer.' },
} as const

const short = (name: string) => name.replace(/^DJI_\d{14}_/, 'Clip ').replace(/(_D)?\.\w+$/, '')

export function NewSelection({ project, system, onStarted }: { project: Project; system: System | null; onStarted: () => void }) {
  const vids = (project.videos ?? []).filter((v) => v.status === 'ok')
  const usable = vids.filter((v) => (v.stats?.gimbal_pitch_median ?? 0) <= -30 && (v.stats?.hag_median ?? 0) >= 8)
  const [chosen, setChosen] = useState<number[]>((usable.length ? usable : vids).map((v) => v.id))
  const [oblique, setOblique] = useState(true)
  const [overlap, setOverlap] = useState(85)
  const [minHag, setMinHag] = useState(8)
  const [maxFrames, setMaxFrames] = useState(0)
  const [preset, setPreset] = useState<keyof typeof QUALITY>('standard')
  const [heal, setHeal] = useState(true)
  const [err, setErr] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const go = async (reconstruct: boolean) => {
    setBusy(true); setErr(null)
    try {
      await api.createSelection(project.id, { videos: chosen, include_oblique: oblique, forward_overlap: overlap / 100, min_hag: minHag, max_frames: maxFrames },
        reconstruct ? { preset, auto_heal: heal } : undefined)
      onStarted()
    } catch (e: any) { setErr(e.message) } finally { setBusy(false) }
  }
  const ready = system?.docker.ok && system?.odm_image.present
  const secs = vids.filter((v) => chosen.includes(v.id)).reduce((s, v) => s + (v.stats?.duration_s ?? 0), 0)
  return (
    <Section title="Build the map" hint={<>The ticked videos are turned into one top-down map. Photos are picked from the video automatically, stitched, checked for errors and repaired. On a laptop, big maps are built in parts that fit in memory.</>}>
      <div className="space-y-3 text-sm">
        <div>
          <div className="mb-1 flex items-center gap-1 text-xs text-slate-400">Videos to use
            <Hint title="Videos to use" size={12}>Naqsha ticks the useful clips for itself. <b>at horizon</b> clips filmed the skyline and cannot make a map; <b>angled</b> ones help with care; <b>straight down</b> is best.</Hint></div>
          <div className="overflow-hidden rounded-xl border border-slate-800">
            {vids.map((v) => {
              const use = cameraUse(v.stats?.gimbal_pitch_median)
              const on = chosen.includes(v.id)
              return (
                <label key={v.id} className={`flex cursor-pointer items-center gap-2 border-b border-slate-800 px-2.5 py-1.5 text-xs transition-colors last:border-0 hover:bg-slate-800/50 ${on ? '' : 'text-slate-500'}`}>
                  <input type="checkbox" checked={on} onChange={(e) => setChosen(e.target.checked ? [...chosen, v.id] : chosen.filter((x) => x !== v.id))} />
                  <span className={`w-16 shrink-0 font-medium ${on ? 'text-slate-100' : ''}`}>{short(v.name)}</span>
                  <span className="num w-[4.5rem] shrink-0 whitespace-nowrap text-slate-400">{fmtDur(v.stats?.duration_s)}</span>
                  <span className="shrink-0"><Badge value={use.kind} label={use.label} /></span>
                  <span className="ml-auto"><Hint title={short(v.name)} size={12}>{use.detail}</Hint></span>
                </label>
              )
            })}
          </div>
        </div>
        <div>
          <div className="mb-1 flex items-center gap-1 text-xs text-slate-400">Quality
            <Hint title="Quality" size={12}>{(Object.keys(QUALITY) as (keyof typeof QUALITY)[]).map((k) => <div key={k}><b>{QUALITY[k].name}</b>: {QUALITY[k].text}</div>)}</Hint></div>
          <div className="grid grid-cols-3 gap-1 rounded-xl bg-slate-950/60 p-1">
            {(Object.keys(QUALITY) as (keyof typeof QUALITY)[]).map((k) => (
              <button key={k} onClick={() => setPreset(k)} aria-pressed={preset === k}
                className={`rounded-lg px-2 py-1.5 text-left transition-colors ${preset === k ? 'bg-slate-800 ring-1 ring-sky-600' : 'hover:bg-slate-800/60'}`}>
                <div className="text-xs font-semibold text-slate-100">{QUALITY[k].name}</div>
                <div className="text-[11px] text-slate-400">{QUALITY[k].tag}</div>
              </button>
            ))}
          </div>
        </div>
        {system && !ready && <Warnings items={[system.docker.ok ? 'The stitching engine is not downloaded yet (top right: Download ODM).' : `Docker is not running: ${system.docker.detail}`]} />}
        {err && <p className="text-sm text-rose-300">{err}</p>}
        <div className="flex flex-wrap items-center gap-2.5">
          <Button kind="primary" disabled={busy || !chosen.length || !ready} onClick={() => go(true)}>Build the map</Button>
          <span className="num text-xs text-slate-400">{chosen.length} clip{chosen.length === 1 ? '' : 's'}, {fmtDur(secs)} of video</span>
        </div>

        <Fold title="Advanced settings" hint="Defaults suit most flights. Change these only if a map came out with gaps or misplaced photos.">
          <div className="space-y-3">
            <label className="flex items-center gap-2 text-xs">
              <input type="checkbox" checked={oblique} onChange={(e) => setOblique(e.target.checked)} />
              <span className="text-slate-200">Use angled frames too</span>
              <Hint title="Angled frames" size={12}>Frames with the camera 30 to 60 degrees below the horizon. Adds area; the sky is masked out.</Hint>
            </label>
            <div className="grid grid-cols-3 gap-2">
              <Field label={`Overlap ${overlap}%`} hint="Overlap between neighbouring photos. Trees need 85 to 90%.">
                <input type="range" min={65} max={95} value={overlap} onChange={(e) => setOverlap(+e.target.value)} className="mt-2 w-full" />
              </Field>
              <Field label="Lowest height (m)" hint="Frames below this height are skipped, which drops takeoff and landing.">
                <input type="number" className={inputCls} value={minHag} onChange={(e) => setMinHag(+e.target.value)} />
              </Field>
              <Field label="Max photos" hint="Upper limit on photos picked. 0 means no limit.">
                <input type="number" className={inputCls} value={maxFrames} onChange={(e) => setMaxFrames(+e.target.value)} />
              </Field>
            </div>
            <label className="flex items-center gap-2 text-xs">
              <input type="checkbox" checked={heal} onChange={(e) => setHeal(e.target.checked)} />
              <span className="text-slate-200">Auto-repair</span>
              <Hint title="Auto-repair" size={12}>If the quality check finds misplaced photos or a broken-up map, rebuild once with the fix.</Hint>
            </label>
            <Button size="sm" disabled={busy || !chosen.length} onClick={() => go(false)}>Only pick the photos (no stitching)</Button>
          </div>
        </Fold>
      </div>
    </Section>
  )
}

export function SelectionCard({ sel, active, onShow, onChange }: { sel: Selection; active: boolean; onShow: () => void; onChange: () => void }) {
  const s = sel.summary ?? {}
  const [preset, setPreset] = useState('standard')
  const links = s.links ?? {}
  return (
    <div className={`rounded-xl border p-2.5 ${active ? 'border-sky-700 bg-sky-950/40' : 'border-slate-800 bg-slate-900/60'}`}>
      <div className="flex items-center justify-between gap-2">
        <div className="text-sm">
          <span className="font-medium text-slate-100">Photo set #{sel.id}</span>
          <span className="ml-2 text-xs text-slate-500">{fmtTime(sel.created_at)}</span>
        </div>
        <div className="flex gap-1">
          {sel.status === 'done' && <Button kind="ghost" size="sm" onClick={onShow}>{active ? 'On map' : 'Show on map'}</Button>}
          {sel.status !== 'running' && sel.status !== 'queued' &&
            <Button kind="ghost" size="sm" onClick={() => confirm(`Delete photo set #${sel.id} and the maps made from it?`) && api.deleteSelection(sel.id).then(onChange)}>Delete</Button>}
        </div>
      </div>
      {sel.status === 'done' && (
        <div className="mt-1.5 space-y-1.5 text-xs text-slate-400">
          <div className="num">
            <b className="text-slate-100">{sel.n_images}</b> photos picked from the video ({s.disk_mb} MB), {Math.round((sel.params.forward_overlap ?? 0) * 100)}% overlap
          </div>
          {Object.keys(links).length > 0 && (
            <div>
              Neighbouring photos: {links.ok ?? 0} overlap well
              {links.weak ? `, ${links.weak} weakly` : ''}{links.inconsistent ? `, ${links.inconsistent} moved differently than GPS says (extra photos were added there)` : ''}.
            </div>
          )}
          <details>
            <summary className="cursor-pointer text-slate-500 hover:text-slate-300">Per clip</summary>
            {(s.per_clip ?? []).map((c: any) => (
              <div key={c.clip} className="mt-1 text-slate-500">
                {short(c.name)}: used {c.kept} photos{Object.keys(c.rejected ?? {}).length > 0 && <> · skipped frames: {Object.entries(c.rejected).map(([r, n]) => `${r} (${n})`).join(', ')}</>}
              </div>
            ))}
          </details>
          <div className="flex items-center gap-2 pt-1">
            <select className={`${inputCls} mt-0 w-40`} value={preset} onChange={(e) => setPreset(e.target.value)}>
              {Object.entries(QUALITY).map(([k, q]) => <option key={k} value={k}>{q.name}</option>)}
            </select>
            <Button size="sm" onClick={() => api.createRun(sel.id, { preset, auto_heal: true }).then(onChange)}>Make a map from these photos</Button>
          </div>
        </div>
      )}
      {sel.status === 'failed' && sel.error && <p className="mt-1 text-xs text-rose-300">{sel.error}</p>}
      {(sel.status === 'running' || sel.status === 'queued') && <p className="mt-1 text-xs text-sky-300">Being prepared…</p>}
    </div>
  )
}
