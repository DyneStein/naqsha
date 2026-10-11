import type { Project, Video } from '../api'
import { clipColor, fmtArea, fmtDur, fmtM, pct } from '../format'
import { cameraUse } from '../words'
import { useState } from 'react'
import { IconChevron } from './icons'
import { Badge, Hint, Section, Stat } from './ui'

export function Overview({ project }: { project: Project }) {
  const s = project.summary
  if (!s) return null
  const flights = project.flights ?? []
  return (
    <div className="flex min-w-0 items-center gap-4">
      <div className="flex min-w-0 flex-1 items-center divide-x divide-slate-800 overflow-x-auto">
        <Stat label="Flight time" value={fmtDur(s.duration_s)} hint="Total recorded video time across all clips." />
        <Stat label="Path flown" value={fmtM(s.path_m)} hint="Length of the cleaned GPS track." />
        <Stat label="Max from takeoff" value={fmtM(s.max_dist_from_takeoff_m)} hint="Farthest the drone got from the takeoff spot of its own flight." />
        <Stat label="Covering radius" value={fmtM(s.enclosing_circle?.radius_m)}
          hint={`Smallest circle containing the whole flight, centred at ${s.enclosing_circle?.lat?.toFixed(5)}, ${s.enclosing_circle?.lon?.toFixed(5)}.`} />
        <Stat label="Ground seen" value={fmtArea(s.area_seen_m2)} hint="Everything any frame of video looked at, including far-off views." />
        <Stat label="Mappable" value={fmtArea(s.area_mappable_m2)} hint="Ground seen with the camera at least 30 degrees below the horizon. This is what can become a map." />
        <Stat label="Straight-down" value={fmtArea(s.area_nadir_m2)} hint="Ground seen with the camera at least 60 degrees down. Gives the sharpest, most accurate map." />
        <Stat label="Max height" value={fmtM(s.max_hag_m)} hint={`Highest point above the ground, measured against ${s.terrain_model}.`} />
      </div>
      <div className="flex shrink-0 items-center gap-1.5 text-xs text-slate-400">
        <span>{flights.length} flight{flights.length === 1 ? '' : 's'}</span>
        {s.warnings?.length ? <span className="rounded-full bg-saffron-950 px-2 py-px text-saffron-300">{s.warnings.length} note{s.warnings.length > 1 ? 's' : ''}</span> : null}
        <Hint title="Flights and notes" side="bottom">
          <div className="space-y-1.5">
            {flights.map((f: any) => (
              <div key={f.flight}>
                <b>Flight {f.flight}</b>: {f.clips.length} clip{f.clips.length > 1 ? 's' : ''}, takeoff {f.home ? `${f.home.lat.toFixed(5)}, ${f.home.lon.toFixed(5)}` : 'unknown'}
                {f.home && !f.home.observed && ' (assumed)'}, ground {f.ground_ref} m
              </div>
            ))}
            <div className="text-slate-400">Terrain: {s.terrain_model}</div>
            {(s.warnings ?? []).map((w: string, i: number) => <div key={i} className="text-saffron-200">{w}</div>)}
          </div>
        </Hint>
      </div>
    </div>
  )
}

function VideoRow({ v, i }: { v: Video; i: number }) {
  const [open, setOpen] = useState(false)
  const s = v.stats ?? {}
  const use = cameraUse(s.gimbal_pitch_median)
  return (
    <div className="rounded-xl border border-slate-800 bg-slate-900/60 text-xs">
      <button type="button" onClick={() => setOpen(!open)} aria-expanded={open} className="w-full px-3 py-2 text-left">
        <div className="flex items-center gap-2">
          <span className="inline-block h-2.5 w-2.5 shrink-0 rounded-full" style={{ background: clipColor(i) }} />
          <span className="font-medium text-slate-100">{v.name.replace(/^DJI_\d{14}_/, 'Clip ').replace(/(_D)?\.\w+$/, '')}</span>
          <span className="ml-auto">{v.status === 'ok' ? <Badge value={use.kind} label={use.label} /> : <Badge value="error" label={v.status} />}</span>
          <IconChevron size={14} className={`text-slate-500 transition-transform duration-200 ${open ? 'rotate-90' : ''}`} />
        </div>
        {v.status === 'ok' && (
          <div className="num mt-1 pl-[18px] text-slate-400">
            {fmtDur(s.duration_s)} · {s.hag_median != null ? `${s.hag_median} m high` : 'height unknown'} · {pct(s.nadir_fraction)} straight down
            {s.gps_jumps ? <span className="text-saffron-300"> · {s.gps_jumps} GPS jump{s.gps_jumps > 1 ? 's' : ''}</span> : null}
          </div>
        )}
      </button>
      {open && (
        <div className="border-t border-slate-800 px-3 py-2 pl-[30px]">
          {v.status === 'ok' ? (
            <div className="num grid grid-cols-2 gap-x-3 gap-y-1 text-slate-400">
              <div>Flown <b className="text-slate-200">{fmtM(s.path_m)}</b></div>
              <div>Flight <b className="text-slate-200">{s.flight ?? '–'}</b></div>
              <div>Camera <b className="text-slate-200">{s.gimbal_pitch_median}°</b></div>
              <div>GPS <b className="text-slate-200">{pct(s.gps_coverage)}</b>{s.xy_sigma_median_m != null && <> ±{s.xy_sigma_median_m} m</>}</div>
              <div className="col-span-2">{s.gps_jumps ? <span className="text-saffron-300">{s.gps_jumps} GPS jump{s.gps_jumps > 1 ? 's' : ''} (max {s.gps_max_jump_m} m), smoothed</span> : 'No GPS jumps'}</div>
              {v.info && <div className="col-span-2 text-slate-500">{v.name} · {v.info.width}×{v.info.height}, {v.info.fps?.toFixed(2)} fps, {v.info.codec}</div>}
              {(s.notes ?? []).length > 0 && <div className="col-span-2 text-slate-500">{s.notes.join('; ')}</div>}
            </div>
          ) : <p className="text-rose-300">{v.error}</p>}
        </div>
      )}
    </div>
  )
}

export function VideosTable({ project }: { project: Project }) {
  return (
    <Section title="Videos in this folder" hint="Every video, what the drone did in it, and whether it is useful for a map. Click a video for its full details.">
      <div className="space-y-1.5">{(project.videos ?? []).map((v, i) => <VideoRow key={v.id} v={v} i={i} />)}</div>
    </Section>
  )
}
