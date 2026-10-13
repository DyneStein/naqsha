export type Json = Record<string, any>

export interface System {
  docker: { ok: boolean; detail: string }
  odm_image: { name: string; present: boolean }
  gpu: boolean
  docker_mem_gb: number | null
  disk_free_gb: number
  data_dir: string
  presets: Record<string, Json>
  selection_defaults: Json
}

export interface Video {
  id: number
  name: string
  path: string
  status: string
  error: string | null
  telemetry_source: string | null
  info: Json | null
  stats: Json | null
}

export interface Project {
  id: number
  name: string
  video_dir: string
  status: string
  error: string | null
  created_at: number
  summary: Json | null
  videos?: Video[]
  flights?: Json[]
}

export interface Job {
  id: number
  project_id: number | null
  kind: string
  ref_id: number | null
  status: 'queued' | 'running' | 'done' | 'failed' | 'cancelled'
  progress: number
  stage: string | null
  message: string | null
  error: string | null
  created_at: number
  started_at: number | null
  finished_at: number | null
}

export interface Selection {
  id: number
  project_id: number
  params: Json
  status: string
  n_images: number
  summary: Json | null
  created_at: number
  error: string | null
  images?: FrameImage[]
  links?: Json[]
  planned_wgs84?: Json | null
}

export interface FrameImage {
  name: string
  clip: number
  frame: number
  t: number
  lat: number
  lon: number
  abs_alt: number
  hag: number
  pitch: number
  heading: number
  xy_sigma: number
}

export interface Run {
  id: number
  project_id: number
  selection_id: number
  parent_run_id: number | null
  preset: string
  options: Json
  excluded: string[] | null
  status: string
  qa: Json | null
  created_at: number
  finished_at: number | null
  error: string | null
  note: string | null
  layers?: Record<string, { bounds: number[]; legend?: Json }>
}

async function req<T>(method: string, url: string, body?: unknown): Promise<T> {
  const r = await fetch(url, {
    method,
    headers: body ? { 'content-type': 'application/json' } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  })
  if (!r.ok) {
    let msg = `${r.status}`
    try {
      const j = await r.json()
      msg = j.detail ?? msg
    } catch {
      /* not json */
    }
    throw new Error(msg)
  }
  return r.json() as Promise<T>
}

export interface TreePlot {
  id: number
  role: 'test' | 'teach'
  origin: 'random' | 'manual' | 'active'
  stratum?: string | null
  round?: number | null
  uncertainty?: number | null
  size_m: number
  center: [number, number]
  poly: number[][]
  marks: number[][]
  done: boolean
  used_for_training: boolean
}

export interface TreeStatus {
  summary: Json | null
  plots: TreePlot[]
  job: { id: number; kind: string; status: string; progress: number; stage: string } | null
  site_model: Json | null
  models: Json[]
  settings?: { min_height_m: number; imagery?: 'map' | 'sharp' }
  sharp?: Json | null
  base_model?: Json | null
  other_models?: Json[]
}

export const api = {
  system: () => req<System>('GET', '/api/system'),
  pullOdm: () => req<{ job_id: number }>('POST', '/api/system/pull-odm'),
  fs: (path?: string) =>
    req<{ path: string; parent: string; entries: { name: string; dir: boolean; size?: number }[] }>(
      'GET', `/api/fs${path ? `?path=${encodeURIComponent(path)}` : ''}`),
  projects: () => req<Project[]>('GET', '/api/projects'),
  project: (id: number) => req<Project>('GET', `/api/projects/${id}`),
  createProject: (name: string, video_dir: string) =>
    req<{ id: number; job_id: number }>('POST', '/api/projects', { name, video_dir }),
  reanalyze: (id: number) => req<{ job_id: number }>('POST', `/api/projects/${id}/analyze`),
  deleteProject: (id: number) => req('DELETE', `/api/projects/${id}`),
  geo: (id: number, kind: 'track' | 'coverage') => req<Json>('GET', `/api/projects/${id}/geo/${kind}`),
  selections: (pid: number) => req<Selection[]>('GET', `/api/projects/${pid}/selections`),
  selection: (sid: number) => req<Selection>('GET', `/api/selections/${sid}`),
  createSelection: (pid: number, params: Json, reconstruct?: Json) =>
    req<{ id: number; job_id: number }>('POST', `/api/projects/${pid}/selections`, { params, reconstruct }),
  deleteSelection: (sid: number) => req('DELETE', `/api/selections/${sid}`),
  runs: (pid: number) => req<Run[]>('GET', `/api/projects/${pid}/runs`),
  run: (rid: number) => req<Run>('GET', `/api/runs/${rid}`),
  createRun: (sid: number, body: Json) => req<{ id: number; job_id: number }>('POST', `/api/selections/${sid}/runs`, body),
  deleteRun: (rid: number) => req('DELETE', `/api/runs/${rid}`),
  retryRun: (rid: number) => req<{ job_id: number }>('POST', `/api/runs/${rid}/retry`),
  runLog: async (rid: number, tail = 200) => (await fetch(`/api/runs/${rid}/log?tail=${tail}`)).text(),
  trees: (rid: number) => req<TreeStatus>('GET', `/api/runs/${rid}/trees`),
  countTrees: (rid: number, body: Json = {}) => req<{ job: number }>('POST', `/api/runs/${rid}/trees`, body),
  teachTrees: (rid: number, use: 'all' | 'random' | 'active' = 'all') => req<{ job: number }>('POST', `/api/runs/${rid}/trees/train`, { use }),
  askTrees: (rid: number, n: number) => req<TreeStatus>('POST', `/api/runs/${rid}/trees/ask`, { n, size_m: 12 }),
  setTreeStart: (rid: number, project: number | null, version: string | null) => req<TreeStatus>('POST', `/api/runs/${rid}/trees/start`, { project, version }),
  makeSharp: (rid: number) => req<{ job: number }>('POST', `/api/runs/${rid}/trees/sharp`),
  saveTreeSettings: (rid: number, body: { min_height_m?: number; imagery?: 'map' | 'sharp' }) => req<TreeStatus>('POST', `/api/runs/${rid}/trees/settings`, body),
  compareTrees: (rid: number) => req<Json>('GET', `/api/runs/${rid}/trees/compare`),
  savePlot: (rid: number, plot: Json) => req<TreeStatus & { plot: TreePlot }>('POST', `/api/runs/${rid}/trees/plots`, plot),
  randomPlots: (rid: number, body: { n: number; size_m: number; role: 'test' | 'teach' }) =>
    req<TreeStatus>('POST', `/api/runs/${rid}/trees/plots/random`, body),
  setTreeModel: (rid: number, version: string | null) => req<TreeStatus>('POST', `/api/runs/${rid}/trees/model`, { version }),
  deletePlot: (rid: number, id: number) => req<TreeStatus>('DELETE', `/api/runs/${rid}/trees/plots/${id}`),
  jobs: (pid?: number) => req<Job[]>('GET', `/api/jobs${pid ? `?project_id=${pid}` : ''}`),
  cancelJob: (jid: number) => req('POST', `/api/jobs/${jid}/cancel`),
}

export const thumbUrl = (sid: number, name: string, full = false) =>
  `/api/selections/${sid}/thumb/${name}${full ? '?full=true' : ''}`
export const tileUrl = (rid: number, layer: string) => `${location.origin}/api/runs/${rid}/tiles/${layer}/{z}/{x}/{y}.png`
export const downloadUrl = (rid: number, kind: string) => `/api/runs/${rid}/download/${kind}`
export const treeFileUrl = (rid: number, kind: string, v?: number, download = false, minH = 0) =>
  `/api/runs/${rid}/trees/file/${kind}?v=${v ?? 0}${download ? '&download=true' : ''}${minH > 0 ? `&min_h=${minH}` : ''}`
