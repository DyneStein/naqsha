import { useEffect, useState } from 'react'
import { api, treeFileUrl, type Run, type TreePlot, type TreeStatus } from '../api'
import { IconDownload, IconPlus } from './icons'
import { Badge, Button, Field, Fold, Hint, inputCls, Section, Warnings } from './ui'

const pct = (x?: number) => (x == null ? '–' : `${Math.round(x * 100)}%`)
const n = (x?: number | null) => (x == null ? '–' : x.toLocaleString())

const METHOD_TEXT: Record<string, string> = {
  image: 'Crowns found by the image model only.',
  'image+height': 'Crowns from the image model, checked against tree height: low ones dropped, boxes holding two treetops split, tall treetops it missed added.',
  height: 'Treetops found in the tree-height map only.',
}

export function TreesPanel({ run, status, busy, onChanged, onPlace, onEdit, onAnswer, onMinHeight }: {
  run: Run | null
  status: TreeStatus | null
  busy: boolean
  onChanged: () => void
  onPlace: (size: number, role: 'test' | 'teach') => void
  onEdit: (p: TreePlot) => void
  onAnswer: (queue: TreePlot[]) => void
  onMinHeight: (h: number) => void
}) {
  const [detH, setDetH] = useState(1)
  const [size, setSize] = useState(20)
  const [err, setErr] = useState<string | null>(null)
  const [budget, setBudget] = useState(8)
  const [cmp, setCmp] = useState<any>(null)
  const [saving, setSaving] = useState(false)
  const busyWhile = (p: Promise<unknown>) => { setSaving(true); return p.finally(() => setSaving(false)) }
  const ver = `${status?.summary?.updated ?? 0}:${status?.plots.filter((p) => p.done).length ?? 0}:${status?.models?.length ?? 0}`
  useEffect(() => { if (run?.id && status?.summary) api.compareTrees(run.id).then(setCmp).catch(() => setCmp(null)) }, [run?.id, ver])
  if (!run) return <p className="text-sm text-slate-400">Build a map first, then count its trees here.</p>
  if (run.status !== 'done' || !run.layers?.orthophoto)
    return <p className="text-sm text-slate-400">Show a finished map (Maps tab) to count its trees.</p>
  const s = status?.summary
  const plots = status?.plots ?? []
  const start = (body = {}) => { setErr(null); return api.countTrees(run.id, body).then(onChanged).catch((e) => setErr(e.message)) }
  const place = (role: 'test' | 'teach', count: number) => {
    setErr(null); return api.randomPlots(run.id, { n: count, size_m: role === 'test' ? 20 : size, role }).then(onChanged).catch((e) => setErr(e.message))
  }
  const minH = status?.settings?.min_height_m ?? 0
  const shown = s ? countAtLeast(s, minH) : 0
  const tests = plots.filter((p) => p.role === 'test')
  const teach = plots.filter((p) => p.role === 'teach' && p.origin !== 'active')
  const active = plots.filter((p) => p.origin === 'active')
  const lastRound = Math.max(0, ...active.map((p) => p.round ?? 0))
  const pending = active.filter((p) => !p.done)
  const activeDone = active.filter((p) => p.done)
  const testDone = tests.filter((p) => p.done).length
  const teachDone = teach.filter((p) => p.done).length

  return (
    <div className="space-y-5 text-sm">
      <Section title={`Trees on map #${run.id}`} hint={<>
        Naqsha finds tree crowns in the map image with <b>DeepForest</b>, a deep learning model trained on hand-labelled
        tree crowns, and compares them with the tree-height map. Nothing is guessed: every tree on the map can be clicked,
        and the count is checked against trees you mark by hand.</>}>
        {!s && (
          <div className="space-y-3">
            <p className="text-slate-300">Find every tree on this map, with its height and crown size. Takes about two minutes.</p>
            <Fold title="Settings" hint="Change only if shrubs are being counted as trees, or real trees are being missed.">
              <Field label="Smallest tree (m)" hint="Anything lower than this is treated as a bush or grass when heights are used.">
                <input type="number" min={0.5} max={10} step={0.5} className={inputCls} value={detH} onChange={(e) => setDetH(+e.target.value)} />
              </Field>
            </Fold>
            <Button kind="primary" disabled={busy} onClick={() => start({ min_height: detH })}>Count trees</Button>
          </div>
        )}
        {s && (
          <div className="space-y-3">
            <div className="rounded-xl bg-slate-900/70 p-3">
              <div className="flex items-start justify-between gap-2">
                <div>
                  {minH === 0 && s.estimate?.count != null ? (
                    <>
                      <div className="display num text-3xl font-bold text-white">≈ {n(s.estimate.count)} <span className="text-base font-medium text-slate-300">trees</span></div>
                      <div className="num text-xs text-slate-400">between {n(s.estimate.low)} and {n(s.estimate.high)} (95% range)</div>
                    </>
                  ) : (
                    <>
                      <div className="display num text-3xl font-bold text-white">{n(shown)} <span className="text-base font-medium text-slate-300">trees</span></div>
                      <div className="num text-xs text-slate-400">{minH > 0 ? `at least ${minH} m tall · ${n(s.count)} woody plants detected from 1 m` : 'everything woody from 1 m · accuracy not measured yet'}</div>
                    </>
                  )}
                </div>
                {s.checked ? <Badge value="good" label="checked by hand" /> : <Badge value="fair" label="not yet checked" />}
              </div>
              <div className="mt-3 flex items-center gap-1.5 text-xs">
                <span className="flex shrink-0 items-center gap-1 text-slate-400">Taller than
                  <Hint title="Height filter" size={11}>Only trees at least this tall are counted, drawn on the map and exported. Naqsha detects everything woody from 1 m; the filter decides what you call a tree. Saved for this map.</Hint></span>
                <div className="flex rounded-lg bg-slate-950/70 p-0.5" role="group" aria-label="Smallest tree counted">
                  {[0, 2, 3, 4, 5].map((h) => (
                    <button key={h} onClick={() => onMinHeight(h)} aria-pressed={minH === h}
                      className={`num rounded-md px-2 py-1 transition-colors ${minH === h ? 'bg-sky-600 text-on-accent' : 'text-slate-300 hover:text-white'}`}>{h === 0 ? 'All' : `${h} m`}</button>
                  ))}
                </div>
                <input aria-label="Custom height in metres" type="number" min={0} max={40} step={0.5} value={minH}
                  onChange={(e) => { const v = Math.max(0, Math.min(40, +e.target.value || 0)); onMinHeight(Math.round(v * 2) / 2) }}
                  className="num w-14 rounded-lg border border-slate-700 bg-slate-950 px-1.5 py-1 text-slate-100 outline-none focus:border-sky-500" />
                <span className="text-slate-500">m</span>
              </div>
              <div className="num mt-3 grid grid-cols-3 gap-2 text-xs">
                <div><div className="text-slate-400">Per hectare</div><div className="font-semibold text-slate-100">{s.area_m2 ? Math.round(shown * 1e4 / s.area_m2) : (s.per_ha ?? '–')}</div></div>
                <div><div className="flex items-center gap-1 text-slate-400">Typical height<Hint title="Typical height" size={11}>Measured on {n(s.height_measured)} of the trees. Under closed canopy the camera never sees the ground, so those heights cannot be measured and are left out.</Hint></div><div className="font-semibold text-slate-100">{heightAt(s, minH, 0.5) ?? (s.height_median_m != null ? `${s.height_median_m} m` : '–')}</div></div>
                <div><div className="flex items-center gap-1 text-slate-400">Tallest 10%<Hint title="Tallest 10%" size={11}>Height that one tree in ten exceeds.</Hint></div>
                  <div className="font-semibold text-slate-100">{(() => { const v = heightAt(s, minH, 0.9); return v ? `over ${v}` : s.height_p90_m != null ? `over ${s.height_p90_m} m` : '–' })()}</div></div>
              </div>
              {s.height_classes && (
                <div className="mt-3 space-y-1 text-xs">
                  <div className="flex items-center gap-1 text-slate-400">By height
                    <Hint title="By height" size={11}>Every tree Naqsha counted, split by its measured height. Read the total that fits your definition: for big trees only, add the first two rows; to include young trees and bushes, add the third.</Hint></div>
                  {s.height_classes.filter((c: any) => c.count > 0 || c.min_m !== null).map((c: any, i: number) => {
                    const max = Math.max(...s.height_classes.map((x: any) => x.count), 1)
                    const part = c.min_m != null && minH > c.min_m && (c.name.startsWith('3 to 5') ? minH < 5 : c.name.startsWith('1 to 3') ? minH < 3 : false)
                      ? countAtLeast(s, minH) - s.height_classes.filter((x: any) => x.min_m != null && x.min_m > c.min_m).reduce((a: number, x: any) => a + x.count, 0) : null
                    const counted = minH === 0 || (c.min_m != null && c.min_m >= minH) || (part != null && part > 0)
                    return (
                      <div key={c.name} className={`grid grid-cols-[1fr_auto] items-center gap-2 ${counted ? '' : 'opacity-45'}`} title={counted ? 'counted' : 'below the height filter, not counted'}>
                        <div className="relative h-5 overflow-hidden rounded bg-slate-950/60">
                          <div className="absolute inset-y-0 left-0 rounded" style={{ width: `${(100 * c.count) / max}%`, background: ['#f4a62a', '#1aa69d', '#74ddd3', '#8aa1b8'][i] , opacity: 0.35 }} />
                          <span className="relative pl-2 leading-5 text-slate-200">{c.name}</span>
                        </div>
                        <span className="num min-w-12 text-right font-semibold text-slate-100">{part != null && part > 0 ? <>{n(part)} <span className="font-normal text-slate-500">of {n(c.count)}</span></> : counted ? n(c.count) : <s className="font-normal text-slate-500">{n(c.count)}</s>}</span>
                      </div>
                    )
                  })}
                </div>
              )}
              {s.squares_estimate && (
                <div className="mt-2 flex items-center gap-1 rounded-lg bg-slate-950/60 px-2.5 py-1.5 text-xs text-slate-300">
                  <span>Your random squares alone: <b className="num text-white">≈ {n(s.squares_estimate.count)}</b> <span className="num text-slate-400">({n(s.squares_estimate.low)} to {n(s.squares_estimate.high)})</span></span>
                  <Hint title="From your squares alone" size={11}>No model involved: the average number of trees you marked per square, times the mapped area, from {s.squares_estimate.squares} randomly placed squares. More squares narrow the range.</Hint>
                </div>
              )}
              <div className="mt-2 flex items-center gap-1 text-xs text-slate-400">
                Method: {s.method_name}{s.method !== 'height' && <> at confidence {s.threshold}</>}
                <Hint title="Method" size={11}>{METHOD_TEXT[s.method]} {s.checked ? 'It was picked because it matched your hand counts best.' : 'Default until sample squares are marked; then the best-matching method is picked automatically.'}</Hint>
              </div>
            </div>
            {err && <p className="text-rose-300">{err}</p>}
          </div>
        )}
      </Section>

      {s && <ImagerySection run={run} status={status} busy={busy} onChanged={onChanged} setErr={setErr} />}

      {s && (
        <Section title="Test squares" hint={<>
          Naqsha drops these at random across sparse, medium and dense areas, and you mark <b>every</b> tree inside each.
          They only ever grade the count, never teach the model, so the accuracy below stays honest. A square with no
          trees in it is a valid answer: mark nothing and press Done.</>}>
          {s.accuracy ? (
            <div className="num mb-2 grid grid-cols-2 gap-2 text-xs">
              <div className="rounded-lg bg-slate-950/60 px-2.5 py-1.5">
                <div className="flex items-center gap-1 text-slate-400">Its trees that are real<Hint title="Precision" size={11}>Of the trees Naqsha placed inside the test squares, the share that matched a tree you marked.</Hint></div>
                <div className="text-sm font-semibold text-slate-50">{pct(s.accuracy.precision)}</div>
              </div>
              <div className="rounded-lg bg-slate-950/60 px-2.5 py-1.5">
                <div className="flex items-center gap-1 text-slate-400">Real trees it found<Hint title="Recall" size={11}>Of the trees you marked in the test squares, the share Naqsha also found.</Hint></div>
                <div className="text-sm font-semibold text-slate-50">{pct(s.accuracy.recall)}</div>
              </div>
            </div>
          ) : (
            <p className="mb-2 text-xs text-slate-400">{testDone ? 'Marked squares do not line up with any found tree yet.' : 'None marked yet. Place them, then mark each one.'}</p>
          )}
          {s.no_match && <Warnings items={['None of the trees you marked lines up with a tree Naqsha found. Check the marks sit on the crowns.']} />}
          {s.selected_on === 'test squares' && s.accuracy && <p className="mb-2 text-[11px] text-slate-500">Method picked on these same squares, so the accuracy is slightly optimistic until teaching squares exist.</p>}
          <SquareList plots={tests} onEdit={onEdit} onDelete={(id) => api.deletePlot(run.id, id).then(onChanged)} />
          <div className="mt-2 flex items-center gap-2">
            <Button onClick={() => place('test', 5)} disabled={busy}><IconPlus size={14} />Place 5 random test squares</Button>
            <span className="text-xs text-slate-500">{tests.filter((p) => p.done).length} of {tests.length} marked</span>
          </div>
          {s.method_table?.length > 0 && (
            <Fold title="Methods compared" hint="Every method and confidence cut-off, scored on the squares used to pick the method. F1 balances finding trees and not inventing them; 1.0 is perfect.">
              <table className="num w-full text-xs">
                <thead><tr className="text-left text-slate-400"><th className="py-1 font-medium">Method</th><th>Cut-off</th><th>F1</th><th>Trees</th></tr></thead>
                <tbody>
                  {bestRows(s.method_table).map((r: any) => (
                    <tr key={`${r.method}${r.threshold}`} className={r.method === s.method && r.threshold === s.threshold ? 'text-saffron-300' : 'text-slate-300'}>
                      <td className="py-0.5">{r.method}</td><td>{r.method === 'height' ? '–' : r.threshold}</td><td>{r.f1}</td><td>{n(r.n)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </Fold>
          )}
        </Section>
      )}

      {s && (
        <Section title="Teaching squares" hint={<>
          Squares whose marked trees teach the image model what trees look like on this site. Teaching learns from
          <b>everything</b> marked here so far: these squares and your active-learning answers together, so both
          approaches feed one model. Then it counts the map again. Test squares are never used for teaching.</>}>
          <SquareList plots={teach} onEdit={onEdit} onDelete={(id) => api.deletePlot(run.id, id).then(onChanged)} />
          <div className="mt-2 flex flex-wrap items-center gap-2">
            <Button onClick={() => place('teach', 3)} disabled={busy}><IconPlus size={14} />Place 3 random</Button>
            <Button kind="ghost" onClick={() => onPlace(size, 'teach')}>Draw my own</Button>
            <select aria-label="Square size" className="w-20 shrink-0 rounded-lg border border-slate-700 bg-slate-950 px-2 py-1.5 text-sm text-slate-100 outline-none focus:border-sky-500" value={size} onChange={(e) => setSize(+e.target.value)}>
              {[20, 30, 40].map((v) => <option key={v} value={v}>{v} m</option>)}
            </select>
          </div>
          <div className="mt-3 flex flex-wrap items-center gap-2">
            <Button kind="primary" disabled={busy || !teachDone} onClick={() => api.teachTrees(run.id, 'all').then(onChanged).catch((e) => setErr(e.message))}>Teach and count again</Button>
            <span className="text-xs text-slate-500">{teachDone ? `${teachDone} finished, ${teach.reduce((a, p) => a + (p.done ? p.marks.length : 0), 0)} trees` : 'Finish a teaching square first'}</span>
          </div>
        </Section>
      )}

      {s && (
        <Section title="Active learning" hint={<>
          Instead of random squares, Naqsha asks about the small patches where it is <b>least sure</b>: crowns whose
          confidence sits near the cut-off, and places where the image and the height map disagree. You mark every tree
          in each 12 m patch; then it learns from your answers, counts again and can ask a new, better-aimed round.
          The patches are spread out and limited to the number you choose, so a round takes a few minutes. Learning
          uses your answers together with any teaching squares, so both approaches feed one model.</>}>
          {active.length > 0 && (
            <p className="mb-2 text-xs text-slate-400">
              Round {lastRound}: {active.filter((p) => p.round === lastRound && p.done).length} of {active.filter((p) => p.round === lastRound).length} answered
              {' · '}{activeDone.length} patches, {activeDone.reduce((a, p) => a + p.marks.length, 0)} trees in total
            </p>
          )}
          <div className="flex flex-wrap items-center gap-2">
            {pending.length > 0 ? (
              <Button kind="primary" onClick={() => onAnswer(pending)}>Answer {pending.length} question{pending.length > 1 ? 's' : ''}</Button>
            ) : (
              <>
                <select aria-label="Questions per round" className="w-36 shrink-0 rounded-lg border border-slate-700 bg-slate-950 px-2 py-1.5 text-sm text-slate-100 outline-none focus:border-sky-500"
                  value={budget} onChange={(e) => setBudget(+e.target.value)}>
                  {[5, 8, 12, 15].map((v) => <option key={v} value={v}>{v} questions</option>)}
                </select>
                <Button kind={activeDone.length ? 'default' : 'primary'} disabled={busy}
                  onClick={() => { setErr(null); return api.askTrees(run.id, budget).then((st) => { onChanged(); onAnswer(st.plots.filter((p) => p.origin === 'active' && !p.done)) }).catch((e) => setErr(e.message)) }}>
                  {activeDone.length ? 'Ask a new round' : 'Ask me'}
                </Button>
              </>
            )}
          </div>
          {activeDone.length > 0 && !pending.length && (
            <div className="mt-2">
              <Button kind="primary" disabled={busy} onClick={() => api.teachTrees(run.id, 'all').then(onChanged).catch((e) => setErr(e.message))}>Learn from my answers and count again</Button>
            </div>
          )}
          {active.length > 0 && <Fold title="Patches asked" count={active.length}>
            <SquareList plots={active} onEdit={onEdit} onDelete={(id) => api.deletePlot(run.id, id).then(onChanged)} />
          </Fold>}
        </Section>
      )}

      {s && cmp?.rows?.length > 0 && (
        <Section title="Which approach is winning?" hint={<>
          Every model version scored on the same test squares ({cmp.test_trees} trees in {cmp.test_squares} squares),
          next to the labelling it took. F1 balances finding real trees and not inventing them; 1.0 is perfect. With
          this few test trees, differences under about 0.05 are noise.</>}>
          {!cmp.test_squares ? (
            <p className="text-xs text-slate-400">Scores appear once at least one test square is marked. {cmp.rows.length} model version{cmp.rows.length > 1 ? 's are' : ' is'} saved and ready to compare.</p>
          ) : (<>
          <table className="num w-full text-xs">
            <thead><tr className="text-left text-slate-400">
              <th className="py-1 font-medium">Model</th><th className="font-medium">Trees taught</th><th className="font-medium">Your time</th><th className="font-medium">F1</th><th className="font-medium">Found / real</th>
            </tr></thead>
            <tbody>
              {cmp.rows.map((r: any) => (
                <tr key={r.version} className={status?.site_model?.version === r.version || (!status?.site_model && r.version === 'pretrained') ? 'text-saffron-300' : 'text-slate-300'}>
                  <td className="py-0.5">{r.version === 'pretrained' ? 'pretrained' : `${r.version} · ${r.approach}`}</td>
                  <td>{r.trees_labelled || '–'}</td>
                  <td>{r.label_seconds ? `${Math.round(r.label_seconds / 6) / 10} min` : '–'}</td>
                  <td className="font-semibold">{r.best.f1.toFixed(2)}</td>
                  <td>{pct(r.best.precision)} / {pct(r.best.recall)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="mt-1 text-[11px] text-slate-500">Each row uses its best method and cut-off. Highlighted: the model in use.</p>
          </>)}
        </Section>
      )}

      {s && (
        <Section title="Model" hint={<>
          Every teaching run saves a new version of this site's model. Switch between them, or back to the pretrained
          model, then press Count again; each count is scored on the test squares and written to the experiment log.</>}>
          <div className="mb-2">
            <div className="mb-1 flex items-center gap-1 text-xs text-slate-400">Starting point for this site
              <Hint title="Starting point" size={11}>Every project starts from the plain DeepForest model, so another site's trees never steer this one. If a site with similar vegetation has already been taught, you can start from that model instead; your teaching here then builds on it.</Hint></div>
            <select aria-label="Starting point" className="w-full rounded-lg border border-slate-700 bg-slate-950 px-2 py-1.5 text-sm text-slate-100 outline-none focus:border-sky-500"
              value={status?.base_model ? `${status.base_model.project}:${status.base_model.source_version}` : ''}
              disabled={saving} onChange={(e) => { const [p, v] = e.target.value ? e.target.value.split(':') : [null, null]; busyWhile(api.setTreeStart(run.id, p ? +p : null, v).then(onChanged).catch((er) => setErr(er.message))) }}>
              <option value="">Fresh: plain DeepForest, taught only on this site</option>
              {(status?.other_models ?? []).map((m: any) => (
                <option key={`${m.project}:${m.version}`} value={`${m.project}:${m.version}`}>From {m.project_name} {m.version} ({m.trees_labelled ?? m.crowns} trees)</option>
              ))}
            </select>
            {!(status?.other_models ?? []).length && <p className="mt-1 text-[11px] text-slate-500">No other project has a taught model yet.</p>}
          </div>
          <div className="mb-1 text-xs text-slate-400">Model used for counting</div>
          <div className="flex items-center gap-2">
            <select aria-label="Tree model" className="min-w-0 flex-1 rounded-lg border border-slate-700 bg-slate-950 px-2 py-1.5 text-sm text-slate-100 outline-none focus:border-sky-500"
              value={status?.site_model ? (String(status.site_model.version).startsWith('base-') ? 'base' : status.site_model.version) : ''} disabled={saving} onChange={(e) => busyWhile(api.setTreeModel(run.id, e.target.value || null).then(onChanged))}>
              <option value="">Pretrained (DeepForest)</option>
              {status?.base_model && <option value="base">Starting model: {status.base_model.project_name} {status.base_model.source_version}</option>}
              {(status?.models ?? []).map((m: any) => <option key={m.version} value={m.version}>{m.version}: {m.approach}, {m.crowns} trees from {m.squares.length} squares</option>)}
            </select>
            <Button kind="ghost" disabled={busy} onClick={() => start({})}>Count again</Button>
          </div>
          <p className="mt-1 text-[11px] text-slate-500">Last count used: {s.model}</p>
        </Section>
      )}

      {s && (
        <Fold title={minH > 0 ? `Downloads (trees at least ${minH} m)` : 'Downloads'} count={4} hint="The trees as files: a table for Excel, GeoJSON for QGIS or ArcGIS, and KML for Google Earth. Each tree has its position, height and crown size.">
          <div className="space-y-1">
            {([['csv', 'Tree list (CSV)', 'Opens in Excel or Google Sheets.'], ['trees', 'Trees (GeoJSON)', 'Points for QGIS or ArcGIS.'],
              ['kml', 'Trees (KML)', 'Opens in Google Earth.'], ['log', 'Experiment log (JSON lines)', 'Every marking session, teaching run and test-square score, for comparing approaches.']] as const).map(([k, name, desc]) => (
              <a key={k} href={treeFileUrl(run.id, k, s.updated, true, k === 'log' ? 0 : minH)}
                className="flex items-center gap-2 rounded-lg border border-slate-800 px-2.5 py-1.5 transition-colors hover:border-slate-600 hover:bg-slate-800/50">
                <IconDownload size={14} className="shrink-0 text-saffron-400" />
                <span className="flex-1 text-slate-100">{name}</span>
                <Hint title={name} size={12}>{desc}</Hint>
              </a>
            ))}
          </div>
        </Fold>
      )}
      {s && <p className="text-[11px] text-slate-500">{s.model} · ground: {s.ground} · {s.candidates?.image_boxes} image crowns, {s.candidates?.treetops} treetops considered</p>}
    </div>
  )
}

function SquareList({ plots, onEdit, onDelete }: { plots: TreePlot[]; onEdit: (p: TreePlot) => void; onDelete: (id: number) => void }) {
  if (!plots.length) return null
  return (
    <div className="space-y-1">
      {plots.map((p) => (
        <div key={p.id} className="flex items-center gap-2 rounded-lg border border-slate-800 px-2.5 py-1.5 text-xs">
          <span className="font-medium text-slate-100">#{p.id}</span>
          <span className="num text-slate-400">{p.size_m} m{p.stratum ? ` · ${p.stratum}` : ''}</span>
          {p.done ? <Badge value="good" label={`${p.marks.length} trees`} /> : <Badge value="queued" label="to mark" />}
          {p.used_for_training && <Badge value="running" label="taught" />}
          <span className="ml-auto flex gap-1">
            <Button size="sm" kind={p.done ? 'ghost' : 'primary'} onClick={() => onEdit(p)}>{p.done ? 'Edit' : 'Mark'}</Button>
            <Button size="sm" kind="ghost" onClick={() => confirm(`Delete square #${p.id}?`) && onDelete(p.id)}>Delete</Button>
          </span>
        </div>
      ))}
    </div>
  )
}

/** Height below which a share q of the counted trees fall, read off the histogram. */
function heightAt(s: any, h: number, q: number): string | null {
  if (!s.height_hist) return null
  const { from_m, step_m, counts } = s.height_hist
  const rows = counts.map((c: number, i: number) => [from_m + i * step_m, c] as [number, number]).filter(([lo]: [number, number]) => lo >= h - 1e-9)
  const total = rows.reduce((a: number, [, c]: [number, number]) => a + c, 0)
  if (!total) return null
  let acc = 0
  for (const [lo, c] of rows) { acc += c; if (acc >= q * total) return `${(lo + step_m / 2).toFixed(1)} m` }
  return null
}

/** Trees at least h metres tall, from the saved height histogram (no request needed). */
function countAtLeast(s: any, h: number): number {
  if (!h || !s.height_hist) return s.count
  const { from_m, step_m, counts } = s.height_hist
  return counts.reduce((sum: number, c: number, i: number) => (from_m + i * step_m >= h - 1e-9 ? sum + c : sum), 0)
}

// best cut-off per method, plus the chosen row
function bestRows(table: any[]) {
  const best: Record<string, any> = {}
  for (const r of table) if (!best[r.method] || r.f1 > best[r.method].f1) best[r.method] = r
  return Object.values(best)
}

/** Instruction bar over the map while placing or marking a sample square. */
export function MarkingBar({ mode, count, role, question, onDone, onCancel, onUndo, onSkip }: {
  mode: 'place' | 'mark'; count: number; role: 'test' | 'teach'; question?: { at: number; of: number } | null
  onDone: () => unknown; onCancel: () => void; onUndo: () => void; onSkip?: () => void
}) {
  return (
    <div className="absolute left-1/2 top-3 z-20 flex -translate-x-1/2 items-center gap-3 rounded-xl border border-saffron-700 bg-slate-900/95 px-4 py-2 text-sm text-slate-100 shadow-[var(--shadow-md)]">
      {mode === 'place' ? (
        <span>Click the map where the sample square should go. Pick a spot where you can see the trees clearly.</span>
      ) : (
        <span>{question ? <b className="num">Question {question.at} of {question.of}:</b> : <b>{role === 'test' ? 'Test' : 'Teaching'} square:</b>} click <b>every</b> tree inside{question ? ' this patch' : ''}. Click a mark to remove it. <span className="num text-saffron-300">{count} marked</span></span>
      )}
      {mode === 'mark' && <Button size="sm" kind="ghost" disabled={!count} onClick={onUndo}>Undo</Button>}
      {mode === 'mark' && <Button size="sm" kind="primary" 
        onClick={() => ((count > 0 || confirm(question ? 'No trees in this patch?' : 'No trees in this square? That is recorded as a real answer.')) ? onDone() : undefined)}>{question && question.at < question.of ? 'Next' : 'Done'}</Button>}
      {onSkip && <Button size="sm" kind="ghost" onClick={onSkip}>Skip</Button>}
      <Button size="sm" kind="ghost" onClick={onCancel}>Cancel</Button>
    </div>
  )
}

function ImagerySection({ run, status, busy, onChanged, setErr }: {
  run: Run; status: TreeStatus | null; busy: boolean; onChanged: () => void; setErr: (e: string | null) => void
}) {
  const imagery = status?.settings?.imagery ?? 'map'
  const sh = status?.sharp
  const used = status?.summary?.imagery ?? 'map'
  const model = status?.site_model
  const choose = (v: 'map' | 'sharp') => api.saveTreeSettings(run.id, { imagery: v }).then(onChanged).catch((e) => setErr(e.message))
  return (
    <Section title="Image to count on" hint={<>
      The stitched map drapes the frames over a surface model. Over leafy ground that surface is noisy, so the
      texture smears and small plants vanish. The <b>sharp map</b> rebuilds the same ground straight from the raw
      frames: each spot from the frame that saw it most straight-down, so low plants come out sharp. Tall crowns keep
      the stitched map's pixels. Teach the model again on the image you choose, and mark new squares on it.</>}>
      {!sh ? (
        <div className="flex flex-wrap items-center gap-2">
          <Button disabled={busy} onClick={() => api.makeSharp(run.id).then(onChanged).catch((e) => setErr(e.message))}>Make the sharp map</Button>
          <span className="text-xs text-slate-500">About half an hour for 8 ha on a laptop.</span>
        </div>
      ) : (
        <div className="space-y-2 text-xs">
          <div className="flex rounded-lg bg-slate-950/70 p-0.5" role="group" aria-label="Image to count on">
            {([['map', 'Stitched map'], ['sharp', 'Sharp map']] as const).map(([v, label]) => (
              <button key={v} onClick={() => choose(v)} aria-pressed={imagery === v} disabled={busy}
                className={`flex-1 rounded-md px-2 py-1 transition-colors ${imagery === v ? 'bg-sky-600 text-on-accent' : 'text-slate-300 hover:text-white'}`}>{label}</button>
            ))}
          </div>
          <p className="text-slate-400">Sharp map from {n(sh.frames)} frames{sh.from_frames_m2 ? `, ${(sh.from_frames_m2 / 1e4).toFixed(1)} ha of it straight from the frames` : ''}.</p>
          {imagery !== used && <p className="text-saffron-300">The count shown was made on the {used === 'sharp' ? 'sharp' : 'stitched'} map. Teach and count again to use the {imagery === 'sharp' ? 'sharp' : 'stitched'} map.</p>}
          {imagery === used && model && (model.imagery ?? 'map') !== imagery && <p className="text-saffron-300">Model {model.version} was taught on the {model.imagery === 'sharp' ? 'sharp' : 'stitched'} map. Teach it again for its best on this image.</p>}
        </div>
      )}
    </Section>
  )
}
