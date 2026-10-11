export const fmtM = (m?: number | null) =>
  m == null ? '–' : m >= 1000 ? `${(m / 1000).toFixed(2)} km` : `${Math.round(m)} m`

export const fmtArea = (m2?: number | null) =>
  m2 == null ? '–' : m2 >= 1e6 ? `${(m2 / 1e6).toFixed(2)} km²` : m2 >= 1e4 ? `${(m2 / 1e4).toFixed(1)} ha` : `${Math.round(m2)} m²`

export const fmtDur = (s?: number | null) => {
  if (s == null) return '–'
  const m = Math.floor(s / 60)
  const r = Math.round(s % 60)
  return m ? `${m} min ${r.toString().padStart(2, '0')} s` : `${r} s`
}

export const fmtTime = (epoch?: number | null) =>
  epoch ? new Date(epoch * 1000).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' }) : '–'

export const fmtElapsed = (from?: number | null, to?: number | null) => {
  if (!from) return ''
  const s = Math.max(0, (to ?? Date.now() / 1000) - from)
  const h = Math.floor(s / 3600)
  const m = Math.floor((s % 3600) / 60)
  return h ? `${h}h ${m}m` : m ? `${m}m ${Math.floor(s % 60)}s` : `${Math.floor(s)}s`
}

export const pct = (x?: number | null, digits = 0) => (x == null ? '–' : `${(x * 100).toFixed(digits)}%`)

// categorical palette for clips, chosen to stay distinct on both OSM and imagery
export const CLIP_COLORS = ['#f59e0b', '#38bdf8', '#a78bfa', '#34d399', '#f472b6', '#fb7185', '#facc15', '#22d3ee', '#c084fc', '#4ade80']
export const clipColor = (i: number) => CLIP_COLORS[i % CLIP_COLORS.length]
