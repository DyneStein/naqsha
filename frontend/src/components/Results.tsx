import { useEffect, useState } from 'react'
import { api, downloadUrl, type Run } from '../api'
import { fmtArea, fmtElapsed, fmtTime, pct } from '../format'
import { GRADE_TEXT, qualitySentence } from '../words'
import { IconDownload } from './icons'
import { Badge, Button, Fold, Hint, Warnings } from './ui'

const QUALITY_NAME: Record<string, string> = { preview: 'Quick preview', standard: 'Standard', max: 'Maximum detail' }

function Metric({ label, value, hint }: { label: string; value: React.ReactNode; hint: string }) {
  return (
    <div className="rounded-lg bg-slate-950/60 px-2.5 py-1.5">
      <div className="flex items-center gap-1 text-[11px] text-slate-400">{label}<Hint title={label} size={12}>{hint}</Hint></div>
      <div className="num text-sm font-semibold text-slate-50">{value}</div>
    </div>
  )
}

const DOWNLOADS: [string, string, string][] = [
  ['orthophoto', 'Map image (GeoTIFF)', 'The stitched top-down map with coordinates. Opens in QGIS, ArcGIS, Google Earth Pro.'],
  ['dsm', 'Elevation map (GeoTIFF)', 'Height of the surface (ground, trees, buildings) for every pixel.'],
  ['dtm', 'Bare-ground elevation', 'Height of the ground with trees and buildings removed.'],
  ['pointcloud', '3D point cloud (LAZ)', 'The 3D points the map was built from. Opens in CloudCompare.'],
  ['report', 'Technical report (PDF)', 'Detailed processing report from the stitching engine.'],
]

export function RunCard({ run, active, onShow, onChange }: { run: Run; active: boolean; onShow: () => void; onChange: () => void }) {
  const qa = run.qa ?? {}
  const reg = qa.registration ?? {}
  const sfm = qa.sfm ?? {}
  const [log, setLog] = useState<string | null>(null)
  const running = run.status === 'running'
  useEffect(() => {
    if (!running || log === null) return
    const t = setInterval(() => api.runLog(run.id, 60).then(setLog), 3000)
    return () => clearInterval(t)
  }, [running, log === null, run.id])
  const outputs = qa.outputs ?? {}
  const parts = run.options?.chunks?.filter((c: any) => String(c.result).startsWith('ok')).length ?? 0
  const between = qa.registration_between_parts ?? run.options?.registration
  return (
    <div className={`rounded-xl border p-3 ${active ? 'border-sky-700 bg-sky-950/40' : 'border-slate-800 bg-slate-900/60'}`}>
      <div className="flex items-start justify-between gap-2">
        <div>
          <div className="text-sm font-semibold text-slate-100">Map #{run.id} <span className="font-normal text-slate-400">· {QUALITY_NAME[run.preset] ?? run.preset}</span></div>
          <div className="text-xs text-slate-500">
            {fmtTime(run.created_at)}{run.finished_at && ` · took ${fmtElapsed(run.created_at, run.finished_at)}`}
            {run.options?.images && ` · ${run.options.images} photos`}{parts > 1 && ` · built in ${parts} parts`}{run.options?.gpu ? ' · GPU' : ''}
          </div>
        </div>
        <div className="flex shrink-0 gap-1">
          {run.status === 'done' && outputs.orthophoto && <Button size="sm" kind={active ? 'default' : 'primary'} onClick={onShow}>{active ? 'On map' : 'Show on map'}</Button>}
          <Button size="sm" kind="ghost" onClick={() => (log === null ? api.runLog(run.id, 200).then(setLog) : setLog(null))}>{log === null ? 'Log' : 'Hide log'}</Button>
          {!running && run.status !== 'queued' &&
            <Button size="sm" kind="ghost" onClick={() => confirm(`Delete map #${run.id}?`) && api.deleteRun(run.id).then(onChange)}>Delete</Button>}
        </div>
      </div>
      {run.status === 'queued' && <p className="mt-2 text-xs text-slate-400">Waiting to start…</p>}
      {running && <p className="mt-2 text-xs text-sky-300">Being built. Progress is shown at the top of this panel.</p>}
      {run.note && <div className="mt-2 text-xs text-sky-300">{run.note}</div>}
      {run.error && (
        <div className="mt-2 space-y-1.5">
          <p className="whitespace-pre-wrap text-xs text-rose-300">{run.error.split('\n')[0]}</p>
          {run.status === 'failed' && (
            <Button kind="primary" onClick={() => api.retryRun(run.id).then(onChange)}>Retry (keeps the parts that finished)</Button>
          )}
        </div>
      )}
      {run.status === 'done' && (
        <div className="mt-2 space-y-2 text-xs">
          <div className="flex items-start gap-2">
            {qa.grade && <Badge value={qa.grade} label={GRADE_TEXT[qa.grade] ?? qa.grade} />}
          </div>
          <p className="text-slate-300">{qualitySentence(qa)}</p>
          <div className="grid grid-cols-2 gap-1.5">
            <Metric label="Photos fitted" value={`${reg.registered ?? '–'} / ${reg.images_in ?? '–'}`} hint="Photos the engine could place. Ideally all." />
            <Metric label="Position vs GPS" value={sfm.gps_error_m != null ? `${Number(sfm.gps_error_m).toFixed(1)} m` : '–'} hint="Typical difference between where the map puts each photo and the drone's GPS." />
            <Metric label="Detail" value={qa.ortho?.resolution_m ? `${(qa.ortho.resolution_m[0] * 100).toFixed(1)} cm/pixel` : '–'} hint="Size of one map pixel on the ground." />
            <Metric label="Area mapped" value={fmtArea(qa.ortho?.valid_area_m2)} hint={qa.holes ? `${pct(1 - qa.holes.hole_fraction)} of the area the drone flew over.` : 'Area covered by the map image.'} />
            <Metric label="Map pieces" value={reg.pieces ?? 1} hint="1 means everything is joined into one consistent map." />
            <Metric label="Misplaced photos" value={qa.flagged_images?.length ?? 0} hint="Photos that landed far from their GPS (e.g. confused by look-alike trees)." />
          </div>
          {between && parts > 1 && (
            <p className="text-slate-400">Parts were aligned to each other using {Object.values(between.shared_cameras ?? {}).reduce((a: number, b: any) => a + b, 0)} shared photos:
              typical offset {between.before_median_m} m before, {between.after_median_m ?? between.after_rms_m} m after alignment.</p>
          )}
          <Warnings items={qa.warnings} />
          <Fold title="Downloads" count={DOWNLOADS.filter(([k]) => outputs[k]).length} defaultOpen={active}
            hint="Files you can open in GIS software. The map image and elevation map carry coordinates, so they line up with other maps.">
            <div className="space-y-1">
              {DOWNLOADS.filter(([k]) => outputs[k]).map(([k, name, desc]) => (
                <a key={k} href={downloadUrl(run.id, k)} target={k === 'report' ? '_blank' : undefined}
                  className="flex items-center gap-2 rounded-lg border border-slate-800 px-2.5 py-1.5 transition-colors hover:border-slate-600 hover:bg-slate-800/50">
                  <IconDownload size={14} className="shrink-0 text-saffron-400" />
                  <span className="flex-1 text-slate-100">{name}</span>
                  <Hint title={name} size={12}>{desc}</Hint>
                </a>
              ))}
            </div>
          </Fold>
        </div>
      )}
      {log !== null && <pre className="mt-2 max-h-72 overflow-auto rounded-lg bg-slate-950 p-2 text-[10.5px] leading-snug text-slate-300">{log || '(no log yet)'}</pre>}
    </div>
  )
}
