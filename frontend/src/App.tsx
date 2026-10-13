import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { api, type Job, type Json, type Project, type Run, type Selection, type System, type TreePlot, type TreeStatus } from './api'
import { JobRow } from './components/Jobs'
import { Legend } from './components/Legend'
import { DEFAULT_LAYERS, MapView, squareAround, type LayerState } from './components/MapView'
import { MarkingBar, TreesPanel } from './components/Trees'
import { NewProject } from './components/NewProject'
import { Overview, VideosTable } from './components/Overview'
import { NewSelection, SelectionCard } from './components/Process'
import { RunCard } from './components/Results'
import { IconCheck, IconMoon, IconPlus, IconSidebar, IconSun } from './components/icons'
import { initialTheme, saveTheme, type Theme } from './theme'
import { Badge, Button, Fold, Hint } from './components/ui'

type Tab = 'process' | 'results' | 'trees' | 'videos' | 'activity'

function SystemBar({ system, onPull }: { system: System | null; onPull: () => void }) {
  if (!system) return null
  const chip = (ok: boolean, text: string) => (
    <span className={`inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 ${ok ? 'border-slate-800 text-slate-300' : 'border-saffron-900 bg-saffron-950 text-saffron-300'}`}>
      <span className={`h-1.5 w-1.5 rounded-full ${ok ? 'bg-emerald-400' : 'bg-saffron-400'}`} />{text}
    </span>
  )
  return (
    <div className="flex flex-wrap items-center gap-1.5 text-[11px]">
      {chip(system.docker.ok, system.docker.ok ? 'Docker' : 'Docker off')}
      {system.docker.ok && (system.odm_image.present ? chip(true, 'Stitching engine')
        : <button onClick={onPull} className="rounded-full border border-saffron-700 px-2 py-0.5 text-saffron-300 hover:bg-saffron-950">Download stitching engine</button>)}
      {chip(true, system.gpu ? 'GPU' : 'CPU only')}
      {system.docker_mem_gb != null && chip(system.docker_mem_gb >= 8, `${system.docker_mem_gb} GB memory`)}
      {chip(system.disk_free_gb > 20, `${system.disk_free_gb} GB free`)}
      <Hint title="This computer" side="bottom">
        <div className="space-y-1">
          <div><b>Docker</b> runs the stitching engine (OpenDroneMap). {system.docker.ok ? 'Running.' : `Not running: ${system.docker.detail}. Start Docker Desktop.`}</div>
          <div><b>GPU</b>: {system.gpu ? 'an NVIDIA card was found and will be used.' : 'none found, so stitching runs on the processor. Fine, just slower.'}</div>
          {system.docker_mem_gb != null && <div><b>Memory</b>: {system.docker_mem_gb} GB is given to Docker. Big maps are split into parts that fit.{system.docker_mem_gb < 8 && ' Raise it in Docker Desktop, Settings, Resources for larger parts.'}</div>}
          <div><b>Disk</b>: a large map needs about 20 GB free while it is built.</div>
        </div>
      </Hint>
    </div>
  )
}

function Steps({ project, runs, jobs }: { project: Project; runs: Run[]; jobs: Job[] }) {
  const analysed = project.status === 'analyzed'
  const hasMap = runs.some((r) => r.status === 'done' && r.qa?.outputs?.orthophoto)
  const busy = (kinds: string[]) => jobs.some((j) => (j.status === 'running' || j.status === 'queued') && kinds.includes(j.kind))
  const reading = busy(['analyze'])
  const building = busy(['select', 'reconstruct', 'pipeline'])
  const steps = [
    { n: 1, title: 'Read videos', text: 'GPS, height and camera angle are read from every video, and the flights are drawn on the map. Takes about a minute.', done: analysed && !reading, now: !analysed || reading, working: reading },
    { n: 2, title: 'Build map', text: 'Photos are picked from the videos and stitched into one top-down image, then checked and repaired. Use the Build tab on the right.', done: hasMap && !building, now: analysed && (!hasMap || building), working: building },
    { n: 3, title: 'Look and download', text: 'See the result on the map, check its quality grade and download the files from the Maps tab.', done: false, now: hasMap && !building, working: false },
  ]
  return (
    <ol className="flex items-center gap-1">
      {steps.map((st, i) => (
        <li key={st.n} className="flex items-center gap-1">
          {i > 0 && <span className={`h-px w-5 ${st.done || st.now ? 'bg-slate-500' : 'bg-slate-800'}`} />}
          <span className={`flex items-center gap-1.5 rounded-full py-1 pl-1 pr-2.5 text-xs ${st.now ? 'bg-saffron-950 text-saffron-200 ring-1 ring-saffron-900' : st.done ? 'text-slate-200' : 'text-slate-500'}`}>
            <span className={`flex h-5 w-5 items-center justify-center rounded-full text-[11px] font-bold ${st.done ? 'bg-sky-600 text-on-accent' : st.now ? 'bg-saffron-500 text-on-saffron' : 'bg-slate-800 text-slate-400'}`}>
              {st.done ? <IconCheck size={12} strokeWidth={2.5} /> : st.n}
            </span>
            <span className="whitespace-nowrap font-medium max-xl:hidden">{st.title}</span>
            {st.working && <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-saffron-400" aria-label="working" />}
            <Hint title={`Step ${st.n}: ${st.title}`} size={12}>{st.text}</Hint>
          </span>
        </li>
      ))}
    </ol>
  )
}

function Brand() {
  return (
    <div className="flex items-center gap-2.5">
      <img src="/logo-64.png" alt="" className="h-7 w-7" />
      <div className="flex items-baseline gap-2">
        <span className="display text-[17px] font-bold text-white" style={{ fontStretch: '118%' }}>Naqsha</span>
        <span className="urdu text-[17px] text-saffron-400" lang="ur" dir="rtl">نقشہ</span>
      </div>
      <span className="hidden border-l border-slate-800 pl-2.5 text-[11px] text-slate-500 lg:inline">drone video to survey map · DenseFusion</span>
    </div>
  )
}

export default function App() {
  const [system, setSystem] = useState<System | null>(null)
  const [projects, setProjects] = useState<Project[]>([])
  const [pid, setPid] = useState<number | null>(() => Number(new URLSearchParams(location.search).get('p')) || null)
  const [project, setProject] = useState<Project | null>(null)
  const [track, setTrack] = useState<Json | null>(null)
  const [coverage, setCoverage] = useState<Json | null>(null)
  const [selections, setSelections] = useState<Selection[]>([])
  const [runs, setRuns] = useState<Run[]>([])
  const [jobs, setJobs] = useState<Job[]>([])
  const [shownSel, setShownSel] = useState<Selection | null>(null)
  const [shownRun, setShownRun] = useState<Run | null>(null)
  const [layers, setLayers] = useState<LayerState>(DEFAULT_LAYERS)
  const [tab, setTab] = useState<Tab>('process')
  const [rail, setRail] = useState(true)
  const [theme, setTheme] = useState<Theme>(initialTheme)
  const [treeStatus, setTreeStatus] = useState<TreeStatus | null>(null)
  const [treeMode, setTreeMode] = useState<'off' | 'place' | 'mark'>('off')
  const [plotSize, setPlotSize] = useState(30)
  const [edit, setEdit] = useState<{ id?: number; role: 'test' | 'teach'; center: number[]; poly: number[][]; marks: number[][];
    started: number; adds: number; removes: number } | null>(null)
  const [placeRole, setPlaceRole] = useState<'test' | 'teach'>('teach')
  const [flyTo, setFlyTo] = useState<{ bounds: number[][]; key: number } | null>(null)
  const [queue, setQueue] = useState<{ ids: number[]; pos: number } | null>(null)   // active-learning questions
  const [hiddenClips, setHiddenClips] = useState<number[]>([])
  const [creating, setCreating] = useState(false)
  const lastJobSig = useRef('')

  const loadSystem = () => api.system().then(setSystem).catch(() => setSystem(null))
  const loadProjects = () => api.projects().then(setProjects)

  const loadProject = useCallback(async (id: number) => {
    const p = await api.project(id)
    setProject(p)
    if (p.status === 'analyzed') {
      const [t, c] = await Promise.all([api.geo(id, 'track'), api.geo(id, 'coverage')])
      setTrack(t); setCoverage(c)
    } else { setTrack(null); setCoverage(null) }
    const [s, r] = await Promise.all([api.selections(id), api.runs(id)])
    setSelections(s); setRuns(r)
    return { s, r }
  }, [])

  useEffect(() => { loadSystem(); loadProjects() }, [])

  useEffect(() => {
    if (pid == null) return
    history.replaceState(null, '', `?p=${pid}`)
    setShownSel(null); setShownRun(null)
    loadProject(pid).then(({ s, r }) => {
      // show the newest finished map and its frames by default
      const run = r.find((x) => x.status === 'done' && x.qa?.outputs?.orthophoto)
      const sel = run ? s.find((x) => x.id === run.selection_id) : s.find((x) => x.status === 'done')
      if (run) api.run(run.id).then(setShownRun)
      if (sel) api.selection(sel.id).then(setShownSel)
    })
  }, [pid, loadProject])

  // poll jobs; refresh project data whenever a job changes state
  useEffect(() => {
    const tick = async () => {
      const js = await api.jobs(pid ?? undefined).catch(() => null)
      if (!js) return
      setJobs(js)
      const sig = js.map((j) => `${j.id}:${j.status}`).join(',')
      if (sig !== lastJobSig.current) {
        const first = lastJobSig.current === ''
        lastJobSig.current = sig
        if (!first) {
          loadProjects(); loadSystem()
          if (pid != null) {
            const { r } = await loadProject(pid)
            const newest = r.find((x) => x.status === 'done' && x.qa?.outputs?.orthophoto)
            loadTrees(shownRun?.id)
            if (newest && newest.id !== shownRun?.id) {
              api.run(newest.id).then(setShownRun)
              api.selection(newest.selection_id).then(setShownSel)
            } else if (shownRun) {
              api.run(shownRun.id).then(setShownRun)       // new layers (the sharp map) appear without a reload
            }
          }
        }
      }
    }
    tick()
    const active = jobs.some((j) => j.status === 'running' || j.status === 'queued')
    const t = setInterval(tick, active ? 1500 : 5000)
    return () => clearInterval(t)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pid, jobs.some((j) => j.status === 'running' || j.status === 'queued'), shownRun?.id])

  const loadTrees = useCallback((rid?: number | null) => {
    if (rid == null) { setTreeStatus(null); return }
    api.trees(rid).then(setTreeStatus).catch(() => setTreeStatus(null))
  }, [])
  useEffect(() => { loadTrees(shownRun?.id); setTreeMode('off'); setEdit(null) }, [shownRun?.id, loadTrees])

  const onTreeClick = (ll: [number, number], hit: number | null) => {
    if (treeMode === 'place') {
      setEdit({ role: placeRole, center: ll, poly: squareAround(ll[0], ll[1], plotSize), marks: [], started: Date.now(), adds: 0, removes: 0 })
      setTreeMode('mark')
      return
    }
    if (!edit) return
    if (hit != null) { setEdit({ ...edit, marks: edit.marks.filter((_, i) => i !== hit), removes: edit.removes + 1 }); return }
    const xs = edit.poly.map((c) => c[0]), ys = edit.poly.map((c) => c[1])
    if (ll[0] < Math.min(...xs) || ll[0] > Math.max(...xs) || ll[1] < Math.min(...ys) || ll[1] > Math.max(...ys)) return
    setEdit({ ...edit, marks: [...edit.marks, ll], adds: edit.adds + 1 })
  }
  const finishPlot = async () => {
    if (!edit || !shownRun) return
    const r = await api.savePlot(shownRun.id, { id: edit.id, role: edit.role, center: edit.center, size_m: plotSize, marks: edit.marks, done: true,
      session: { seconds: Math.round((Date.now() - edit.started) / 1000), adds: edit.adds, removes: edit.removes } })
    setTreeStatus(r)
    nextQuestion(r.plots, 1)
  }
  // move through the question queue; leave marking mode at its end
  const nextQuestion = (plots: TreePlot[], step: number) => {
    if (queue) {
      for (let pos = queue.pos + step; pos < queue.ids.length; pos++) {
        const p = plots.find((x) => x.id === queue.ids[pos])
        if (p && !p.done) { setQueue({ ...queue, pos }); editPlot(p); return }
      }
      setQueue(null)
    }
    setTreeMode('off'); setEdit(null)
  }
  const editPlot = (p: TreePlot) => {
    setPlotSize(p.size_m)
    setEdit({ id: p.id, role: p.role, center: p.center, poly: [...p.poly, p.poly[0]], marks: p.marks, started: Date.now(), adds: 0, removes: 0 })
    setTreeMode('mark')
    setFlyTo({ bounds: p.poly, key: Date.now() })
  }
  const treeMap = useMemo(() => ({
    rid: shownRun?.id ?? null, v: treeStatus?.summary?.updated ?? 0, has: !!treeStatus?.summary, minH: treeStatus?.settings?.min_height_m ?? 0,
    plots: treeStatus?.plots ?? [], mode: treeMode, sizeM: plotSize, edit,
  }), [shownRun?.id, treeStatus, treeMode, plotSize, edit])

  const clipIds = useMemo(() => (project?.videos ?? []).map((v) => v.id), [project])
  const homes = useMemo(() => (project?.flights ?? []).filter((f: any) => f.home).map((f: any) => ({ ...f.home, flight: f.flight })), [project])
  const clipNames = useMemo(() => Object.fromEntries((project?.videos ?? []).map((v) => [v.id, v.name.replace(/^DJI_\d{14}_/, 'Clip ').replace(/(_D)?\.\w+$/, '')])), [project])
  const activeJobs = jobs.filter((j) => j.status === 'running' || j.status === 'queued')
  const refresh = () => { if (pid != null) loadProject(pid) }

  const pastJobs = jobs.filter((j) => j.status !== 'running' && j.status !== 'queued')
  const TABS: [Tab, string, number?][] = [['process', 'Build'], ['results', 'Maps', runs.length], ['trees', 'Trees'], ['videos', 'Videos', project?.videos?.length], ['activity', 'Activity', pastJobs.length]]

  return (
    <div className="flex h-full w-full flex-col overflow-hidden">
      <header className="flex h-12 shrink-0 items-center justify-between gap-3 border-b border-slate-800 bg-slate-950 px-3">
        <div className="flex items-center gap-2">
          <button onClick={() => setRail(!rail)} aria-label={rail ? 'Hide projects' : 'Show projects'} className="rounded-md p-1 text-slate-400 hover:bg-slate-800 hover:text-white"><IconSidebar size={18} /></button>
          <Brand />
        </div>
        <div className="flex items-center gap-2">
          <SystemBar system={system} onPull={() => api.pullOdm().then(() => setTimeout(loadSystem, 1000))} />
          <button onClick={() => { const t = theme === 'dark' ? 'light' : 'dark'; setTheme(t); saveTheme(t) }}
            aria-label={theme === 'dark' ? 'Switch to light mode' : 'Switch to dark mode'} title={theme === 'dark' ? 'Light mode' : 'Dark mode'}
            className="rounded-md p-1.5 text-slate-400 transition-colors hover:bg-slate-800 hover:text-saffron-400">
            {theme === 'dark' ? <IconSun size={17} /> : <IconMoon size={17} />}
          </button>
        </div>
      </header>
      <div className="flex min-h-0 flex-1">
        {rail && (
          <aside className="flex w-56 shrink-0 flex-col border-r border-slate-800 bg-slate-950">
            <div className="flex items-center justify-between px-3 pb-1 pt-3">
              <span className="flex items-center gap-1 text-[11px] font-semibold text-slate-400">Projects
                <Hint title="Projects" side="right" size={12}>One project per folder of drone videos. Each keeps its flights, photo sets and every map built from them.</Hint></span>
            </div>
            <nav className="min-h-0 flex-1 overflow-auto px-2 pb-2">
              {projects.map((p) => (
                <button key={p.id} onClick={() => setPid(p.id)}
                  className={`mb-0.5 block w-full rounded-lg px-2.5 py-2 text-left text-sm transition-colors ${p.id === pid ? 'bg-slate-800 text-white' : 'text-slate-300 hover:bg-slate-900'}`}>
                  <div className="truncate font-medium">{p.name}</div>
                  <div className="mt-0.5 flex items-center gap-1.5 text-[11px] text-slate-400"><Badge value={p.status} />{p.summary && <span>{p.summary.clips} clips</span>}</div>
                </button>
              ))}
              {!projects.length && <p className="px-2.5 text-xs text-slate-500">No projects yet.</p>}
            </nav>
            <div className="border-t border-slate-800 p-2"><Button kind="primary" onClick={() => setCreating(true)}><IconPlus size={15} />New project</Button></div>
          </aside>
        )}

        {project ? (
          <main className="flex min-w-0 flex-1 flex-col">
            <div className="flex shrink-0 items-center justify-between gap-4 border-b border-slate-800 px-4 py-2">
              <div className="min-w-[9rem] flex-1">
                <h1 className="truncate text-[17px] font-semibold text-white">{project.name}</h1>
                <p className="truncate text-[11px] text-slate-500" title={project.video_dir}>{project.video_dir}</p>
              </div>
              <Steps project={project} runs={runs} jobs={jobs} />
              <div className="flex shrink-0 items-center gap-1">
                {project.status === 'analyzed' && <a className="rounded-lg px-2.5 py-1.5 text-[13px] text-slate-300 hover:bg-slate-800" href={`/api/projects/${project.id}/geo/kml`} title="Download the flight paths for Google Earth">Flight KML</a>}
                <Button kind="ghost" onClick={() => api.reanalyze(project.id).then(refresh)}>Re-analyse</Button>
                <Button kind="danger" size="sm" onClick={() => confirm(`Delete project "${project.name}" and all its maps? Videos are not touched.`) &&
                  api.deleteProject(project.id).then(() => { setPid(null); setProject(null); loadProjects() })}>Delete</Button>
              </div>
            </div>
            {project.error && <p className="shrink-0 border-b border-rose-900 bg-rose-950/40 px-4 py-1.5 text-sm text-rose-300">{project.error}</p>}
            {project.summary && <div className="shrink-0 border-b border-slate-800 bg-slate-950 px-4 py-2"><Overview project={project} /></div>}

            <div className="flex min-h-0 flex-1">
              <div className="relative min-w-0 flex-1">
                <MapView track={track} coverage={coverage} homes={homes} selection={shownSel} run={shownRun}
                  layers={layers} clipIds={clipIds} focusKey={`${project.id}:${project.status}`}
                  hiddenClips={hiddenClips} clipNames={clipNames} trees={treeMap} onTreeClick={onTreeClick} flyTo={flyTo} theme={theme} />
                {treeMode !== 'off' && <MarkingBar mode={treeMode} count={edit?.marks.length ?? 0} role={edit?.role ?? placeRole} onDone={finishPlot}
                  question={queue ? { at: queue.pos + 1, of: queue.ids.length } : null}
                  onSkip={queue ? () => nextQuestion(treeStatus?.plots ?? [], 1) : undefined}
                  onUndo={() => edit && setEdit({ ...edit, marks: edit.marks.slice(0, -1) })}
                  onCancel={() => { setTreeMode('off'); setEdit(null); setQueue(null) }} />}
                <Legend layers={layers} setLayers={setLayers} run={shownRun} videos={project.videos ?? []}
                  hiddenClips={hiddenClips} setHiddenClips={setHiddenClips} hasFrames={!!shownSel?.images?.length} hasTrees={!!treeStatus?.summary} />
              </div>
              <div className="flex w-[25rem] shrink-0 flex-col border-l border-slate-800 bg-slate-950">
                <div role="tablist" className="flex shrink-0 border-b border-slate-800 px-1.5 pt-2">
                  {TABS.map(([k, label, n]) => (
                    <button key={k} role="tab" aria-selected={tab === k} onClick={() => { setTab(k); if (k === 'trees') setLayers((l) => ({ ...l, frames: false, coverage: 'off' })) }}
                      className={`-mb-px flex min-w-0 flex-1 items-center justify-center gap-1 whitespace-nowrap border-b-2 px-1.5 py-2 text-[13px] font-medium transition-colors ${tab === k ? 'border-saffron-500 text-white' : 'border-transparent text-slate-400 hover:text-slate-200'}`}>
                      {label}{n != null && n > 0 && <span className="num rounded-full bg-slate-800 px-1.5 text-[11px] text-slate-300">{n > 99 ? '99+' : n}</span>}
                    </button>
                  ))}
                </div>
                <div className="min-h-0 flex-1 space-y-4 overflow-auto p-3">
                  {activeJobs.length > 0 && (
                    <div className="space-y-2">{activeJobs.map((j) => <JobRow key={j.id} job={j} onChange={refresh} />)}</div>
                  )}
                  {tab === 'process' && (
                    <>
                      {project.status === 'analyzed' ? <NewSelection project={project} system={system} onStarted={refresh} />
                        : <p className="text-sm text-slate-300">Reading the videos: GPS, height and camera angle from every frame. This takes about a minute; the flights then appear on the map.</p>}
                      {selections.length > 0 && (
                        <Fold title="Photo sets" count={selections.length}
                          hint="Each build first picks photos from the videos. A photo set can be shown on the map or turned into another map at a different quality without picking again.">
                          <div className="space-y-2">
                            {selections.map((s) => <SelectionCard key={s.id} sel={s} active={shownSel?.id === s.id}
                              onShow={() => api.selection(s.id).then(setShownSel)} onChange={refresh} />)}
                          </div>
                        </Fold>
                      )}
                    </>
                  )}
                  {tab === 'results' && (
                    <div className="space-y-2">
                      {runs.map((r) => <RunCard key={r.id} run={r} active={shownRun?.id === r.id} onChange={refresh}
                        onShow={() => Promise.all([api.run(r.id).then(setShownRun), api.selection(r.selection_id).then(setShownSel)])} />)}
                      {!runs.length && <p className="text-sm text-slate-400">No maps yet. Build one from the Build tab.</p>}
                    </div>
                  )}
                  {tab === 'trees' && <TreesPanel run={shownRun} status={treeStatus} onChanged={() => { loadTrees(shownRun?.id); refresh(); if (shownRun) api.run(shownRun.id).then(setShownRun) }}
                    onMinHeight={(h) => { if (!shownRun) return; setTreeStatus((st) => st && { ...st, settings: { ...st.settings, min_height_m: h } }); api.saveTreeSettings(shownRun.id, { min_height_m: h }).then(setTreeStatus) }}
                    busy={activeJobs.some((j) => j.kind === 'trees' || j.kind === 'trees_train' || j.kind === 'sharp')}
                    onPlace={(size, role) => { setPlotSize(size); setPlaceRole(role); setEdit(null); setTreeMode('place') }} onEdit={(p) => { setQueue(null); editPlot(p) }}
                    onAnswer={(qs) => { if (!qs.length) return; setQueue({ ids: qs.map((q) => q.id), pos: 0 }); editPlot(qs[0]) }} />}
                  {tab === 'videos' && <VideosTable project={project} />}
                  {tab === 'activity' && (
                    <div className="space-y-2">
                      {pastJobs.slice(0, 30).map((j) => <JobRow key={j.id} job={j} onChange={refresh} />)}
                      {!pastJobs.length && <p className="text-sm text-slate-400">Nothing has run yet.</p>}
                    </div>
                  )}
                </div>
              </div>
            </div>
          </main>
        ) : (
          <main className="flex flex-1 items-center justify-center p-8">
            <div className="max-w-md">
              <img src="/logo-192.png" alt="" className="h-16 w-16" />
              <h2 className="mt-5 text-2xl font-bold text-white">Turn drone videos into a map</h2>
              <ol className="mt-4 space-y-3 text-sm text-slate-300">
                <li className="flex gap-3"><span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-saffron-500 text-xs font-bold text-on-saffron">1</span><span>Click <b className="text-white">New project</b> and choose the folder with the drone's video files.</span></li>
                <li className="flex gap-3"><span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-slate-800 text-xs font-bold text-slate-300">2</span><span>The flights are read automatically: where the drone went, how high, and what the camera saw.</span></li>
                <li className="flex gap-3"><span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-slate-800 text-xs font-bold text-slate-300">3</span><span>Press <b className="text-white">Build the map</b>. Photos are picked from the video and stitched into one top-down image you can view here and download.</span></li>
              </ol>
              <div className="mt-6 flex items-center gap-3">
                <Button kind="primary" onClick={() => setCreating(true)}><IconPlus size={15} />New project</Button>
                {projects.length > 0 && <span className="text-xs text-slate-500">or pick a project on the left</span>}
              </div>
            </div>
          </main>
        )}
      </div>
      {creating && <NewProject onClose={() => setCreating(false)} onCreated={(id) => { setCreating(false); loadProjects(); setPid(id) }} />}
    </div>
  )
}
