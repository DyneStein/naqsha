import { IconRuler } from './icons'
import * as maplibregl from 'maplibre-gl'
import type { GeoJSONSource, Map as MLMap, MapLayerMouseEvent, MapMouseEvent } from 'maplibre-gl'
// MapLibre 6 runs its worker as an ES module; let Vite bundle it and pass the URL
import workerUrl from 'maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url'

maplibregl.setWorkerUrl(workerUrl)
import { useEffect, useRef, useState } from 'react'
import { tileUrl, thumbUrl, type FrameImage, type Json, type Run, type Selection } from '../api'
import { clipColor, fmtArea, fmtM } from '../format'

export interface LayerState {
  basemap: 'osm' | 'imagery'
  coverage: 'off' | 'mappable' | 'nadir' | 'all'
  track: boolean
  frames: boolean
  ortho: boolean
  dsm: boolean
  holes: boolean
  orthoOpacity: number
  sharp: boolean
  trees: boolean
  crowns: boolean
  density: boolean
  plots: boolean
}

/** What the tree-counting tab wants drawn and whether map clicks belong to it. */
export interface TreeMapState {
  rid: number | null
  v: number                       // bump to reload the tree files
  has: boolean                    // a count exists for this map
  minH: number                    // show only trees at least this tall (0 = all)
  plots: { id: number; role: string; poly: number[][]; marks: number[][]; done: boolean; used_for_training: boolean }[]
  mode: 'off' | 'place' | 'mark'
  sizeM: number                   // square size while placing
  edit: { poly: number[][]; marks: number[][] } | null
}

export const NO_TREES: TreeMapState = { rid: null, v: 0, has: false, minH: 0, plots: [], mode: 'off', sizeM: 30, edit: null }

/** Square of side `size` metres centred on lng/lat, as a closed lng/lat ring. */
export function squareAround(lng: number, lat: number, size: number): number[][] {
  const h = size / 2, kx = 111320 * Math.cos((lat * Math.PI) / 180), ky = 110540
  const dx = h / kx, dy = h / ky
  return [[lng - dx, lat - dy], [lng + dx, lat - dy], [lng + dx, lat + dy], [lng - dx, lat + dy], [lng - dx, lat - dy]]
}

export const DEFAULT_LAYERS: LayerState = {
  basemap: 'osm', coverage: 'mappable', track: true, frames: true, ortho: true, dsm: false, holes: true, orthoOpacity: 1, sharp: false,
  trees: true, crowns: false, density: false, plots: true,
}

const EMPTY: any = { type: 'FeatureCollection', features: [] }

function haversine(a: number[], b: number[]) {
  const R = 6371008.8, r = Math.PI / 180
  const dp = (b[1] - a[1]) * r, dl = (b[0] - a[0]) * r
  const h = Math.sin(dp / 2) ** 2 + Math.cos(a[1] * r) * Math.cos(b[1] * r) * Math.sin(dl / 2) ** 2
  return 2 * R * Math.asin(Math.sqrt(h))
}

function polygonArea(pts: number[][]) {
  // local equirectangular projection is accurate at this scale (< few km)
  const lat0 = pts.reduce((s, p) => s + p[1], 0) / pts.length
  const kx = 111320 * Math.cos((lat0 * Math.PI) / 180), ky = 110540
  let a = 0
  for (let i = 0; i < pts.length; i++) {
    const [x1, y1] = pts[i], [x2, y2] = pts[(i + 1) % pts.length]
    a += x1 * kx * (y2 * ky) - x2 * kx * (y1 * ky)
  }
  return Math.abs(a) / 2
}

export function MapView({ track, coverage, homes, selection, run, layers, clipIds, focusKey, hiddenClips, clipNames, trees = NO_TREES, onTreeClick, flyTo, theme = 'dark' }: {
  track: Json | null
  coverage: Json | null
  homes: { lat: number; lon: number; observed: boolean; flight?: number }[]
  selection: Selection | null
  run: Run | null
  layers: LayerState
  clipIds: number[]
  focusKey: string
  hiddenClips: number[]
  clipNames: Record<number, string>
  trees?: TreeMapState
  onTreeClick?: (lngLat: [number, number], pixelHit: number | null) => void
  flyTo?: { bounds: number[][]; key: number } | null
  theme?: 'dark' | 'light'
}) {
  const treesRef = useRef(trees)
  treesRef.current = trees
  const clickRef = useRef(onTreeClick)
  clickRef.current = onTreeClick
  const namesRef = useRef(clipNames)
  namesRef.current = clipNames
  const el = useRef<HTMLDivElement>(null)
  const map = useRef<MLMap | null>(null)
  const [ready, setReady] = useState(false)
  const [measuring, setMeasuring] = useState(false)
  const [measure, setMeasure] = useState<number[][]>([])
  const measuringRef = useRef(false)
  measuringRef.current = measuring

  useEffect(() => {
    const m = new maplibregl.Map({
      container: el.current!,
      style: {
        version: 8,
        sources: {
          osm: { type: 'raster', tiles: ['https://tile.openstreetmap.org/{z}/{x}/{y}.png'], tileSize: 256, maxzoom: 19,
            attribution: '© OpenStreetMap contributors' },
          imagery: { type: 'raster', tiles: ['https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}'],
            tileSize: 256, maxzoom: 19, attribution: 'Imagery © Esri (dated, for reference only)' },
        },
        layers: [
          { id: 'bg', type: 'background', paint: { 'background-color': '#0b1220' } },
          { id: 'osm', type: 'raster', source: 'osm', paint: { 'raster-saturation': -0.4, 'raster-brightness-max': 0.85 } },
          { id: 'imagery', type: 'raster', source: 'imagery', layout: { visibility: 'none' } },
        ],
      },
      center: [73.07, 33.69], zoom: 14, maxZoom: 24, attributionControl: { compact: true },
    })
    m.addControl(new maplibregl.NavigationControl({ visualizePitch: false }), 'top-right')
    m.addControl(new maplibregl.ScaleControl({ unit: 'metric' }), 'bottom-left')
    m.on('load', () => {
      for (const id of ['coverage', 'track', 'homes', 'frames', 'holes', 'measure', 'trees', 'crowns', 'density', 'plots', 'marks', 'edit']) {
        m.addSource(id, { type: 'geojson', data: EMPTY })
      }
      m.addLayer({ id: 'coverage-fill', type: 'fill', source: 'coverage', paint: { 'fill-color': '#22c55e', 'fill-opacity': 0.18 } })
      m.addLayer({ id: 'coverage-line', type: 'line', source: 'coverage', paint: { 'line-color': '#4ade80', 'line-width': 1 } })
      m.addLayer({ id: 'track-line', type: 'line', source: 'track', layout: { 'line-join': 'round', 'line-cap': 'round' },
        paint: { 'line-color': '#f59e0b', 'line-width': 2.5 } })
      m.addLayer({ id: 'holes-fill', type: 'fill', source: 'holes', paint: { 'fill-color': '#ef4444', 'fill-opacity': 0.35 } })
      m.addLayer({ id: 'holes-line', type: 'line', source: 'holes', paint: { 'line-color': '#f87171', 'line-width': 1 } })
      m.addLayer({ id: 'density-fill', type: 'fill', source: 'density', layout: { visibility: 'none' }, paint: {
        'fill-color': ['interpolate', ['linear'], ['get', 'per_ha'], 0, '#0c1d31', 150, '#0f8a82', 400, '#f4a62a', 800, '#fde68a'],
        'fill-opacity': 0.55, 'fill-outline-color': 'rgba(0,0,0,0)' } })
      m.addLayer({ id: 'crowns-line', type: 'line', source: 'crowns', layout: { visibility: 'none' },
        paint: { 'line-color': '#b6efe9', 'line-width': 1, 'line-opacity': 0.8 } })
      m.addLayer({ id: 'frames-pt', type: 'circle', source: 'frames', paint: {
        'circle-radius': ['interpolate', ['linear'], ['zoom'], 14, 2, 19, 5],
        'circle-color': ['match', ['get', 'state'], 'flagged', '#ef4444', 'dropped', '#a855f7', 'excluded', '#64748b', '#f8fafc'],
        'circle-stroke-color': '#0f172a', 'circle-stroke-width': 1,
      } })
      m.addLayer({ id: 'homes-pt', type: 'circle', source: 'homes', paint: {
        'circle-radius': 7, 'circle-color': '#0ea5e9', 'circle-stroke-color': '#fff', 'circle-stroke-width': 2 } })
      m.addLayer({ id: 'trees-pt', type: 'circle', source: 'trees', layout: { visibility: 'none' }, paint: {
        'circle-radius': ['interpolate', ['linear'], ['zoom'], 15, 1.5, 18, 3.5, 21, 7],
        'circle-color': ['case', ['==', ['get', 'height_m'], null], '#cbd5e1',
          ['interpolate', ['linear'], ['get', 'height_m'], 2, '#74ddd3', 8, '#1aa69d', 14, '#f9b850', 22, '#ef4444']],
        'circle-stroke-color': '#071425', 'circle-stroke-width': ['interpolate', ['linear'], ['zoom'], 15, 0.5, 19, 1.2] } })
      const roleColor: any = ['match', ['get', 'role'], 'teach', '#3cc3b9', '#f4a62a']
      m.addLayer({ id: 'plots-fill', type: 'fill', source: 'plots', paint: { 'fill-color': roleColor, 'fill-opacity': 0.08 } })
      m.addLayer({ id: 'plots-line', type: 'line', source: 'plots', filter: ['==', ['get', 'done'], true],
        paint: { 'line-color': roleColor, 'line-width': 2 } })
      m.addLayer({ id: 'plots-open', type: 'line', source: 'plots', filter: ['!=', ['get', 'done'], true],
        paint: { 'line-color': roleColor, 'line-width': 2, 'line-dasharray': [2, 1.5] } })
      m.addLayer({ id: 'marks-pt', type: 'circle', source: 'marks', paint: {
        'circle-radius': 4, 'circle-color': '#ffffff', 'circle-stroke-color': '#f4a62a', 'circle-stroke-width': 2 } })
      m.addLayer({ id: 'edit-fill', type: 'fill', source: 'edit', filter: ['==', '$type', 'Polygon'], paint: { 'fill-color': '#f4a62a', 'fill-opacity': 0.08 } })
      m.addLayer({ id: 'edit-line', type: 'line', source: 'edit', filter: ['==', '$type', 'Polygon'], paint: { 'line-color': '#ffd083', 'line-width': 2.5 } })
      m.addLayer({ id: 'edit-pt', type: 'circle', source: 'edit', filter: ['==', '$type', 'Point'], paint: {
        'circle-radius': 6, 'circle-color': '#f4a62a', 'circle-stroke-color': '#071425', 'circle-stroke-width': 2 } })
      m.addLayer({ id: 'measure-fill', type: 'fill', source: 'measure', filter: ['==', '$type', 'Polygon'],
        paint: { 'fill-color': '#e879f9', 'fill-opacity': 0.15 } })
      m.addLayer({ id: 'measure-line', type: 'line', source: 'measure', paint: { 'line-color': '#e879f9', 'line-width': 2, 'line-dasharray': [2, 1] } })
      m.addLayer({ id: 'measure-pt', type: 'circle', source: 'measure', filter: ['==', '$type', 'Point'],
        paint: { 'circle-radius': 4, 'circle-color': '#e879f9' } })
      setReady(true)
    })
    m.on('click', 'frames-pt', (e: MapLayerMouseEvent) => {
      if (measuringRef.current || treesRef.current.mode !== 'off') return
      const f = e.features?.[0]
      if (!f) return
      const p = f.properties as any
      new maplibregl.Popup({ maxWidth: '380px' })
        .setLngLat((f.geometry as any).coordinates)
        .setHTML(`<a href="${thumbUrl(p.sid, p.name, true)}" target="_blank"><img src="${thumbUrl(p.sid, p.name)}" style="width:360px;border-radius:4px"/></a>
          <div style="font-size:11px;margin-top:4px;line-height:1.5">
          <b>${p.name}</b>${p.state !== 'ok' ? ` · <span style="color:#f87171">${p.state}</span>` : ''}<br/>
          t=${Number(p.t).toFixed(2)} s · ${Number(p.hag).toFixed(0)} m above ground · pitch ${Number(p.pitch).toFixed(0)}° · heading ${Number(p.heading).toFixed(0)}°<br/>
          ${Number(p.lat).toFixed(6)}, ${Number(p.lon).toFixed(6)} · ±${Number(p.xy_sigma).toFixed(1)} m
          ${p.dxy != null ? `<br/>SfM vs GPS: ${p.dxy} m` : ''}</div>`)
        .addTo(m)
    })
    // hover explanations: one handler picks the most specific thing under the cursor,
    // so a flight line or gap wins over the coverage area beneath it
    const hover = new maplibregl.Popup({ closeButton: false, closeOnClick: false, maxWidth: '260px', offset: 12, className: 'hover-tip' })
    const COVER: Record<string, string> = {
      nadir: 'seen with the camera pointing almost straight down (best for the map)',
      mappable: 'seen with the camera at least 30° down, so usable for the map',
      all: 'seen at any camera angle, including far-off views that cannot be mapped',
    }
    const describe: [string, (p: any) => string][] = [
      ['edit-pt', () => `<b>Your mark</b><br/>Click it again to remove it.`],
      ['trees-pt', (p) => `<b>Tree ${p.id}</b>${p.height_m != null && p.height_m !== 'null' ? `, about ${Number(p.height_m).toFixed(0)} m tall` : ', height not measurable here'}${p.crown_m != null && p.crown_m !== 'null' ? `, crown ${Number(p.crown_m).toFixed(1)} m across` : ''}<br/>Found by: ${p.source}${p.score != null && p.score !== 'null' ? ` (confidence ${Number(p.score).toFixed(2)})` : ''}`],
      ['marks-pt', () => `<b>Tree marked by hand</b> in a sample square`],
      ['frames-pt', (p) => `<b>Photo</b> picked from the video, ${Number(p.hag).toFixed(0)} m above ground.${p.state && p.state !== 'ok' ? ` <span style="color:#fca5a5">${p.state}</span>.` : ''}<br/>Click to see it.`],
      ['homes-pt', (p) => `<b>Takeoff point</b>${p.flight ? ` of flight ${p.flight}` : ''}${p.observed === false ? ' (estimated)' : ''}`],
      ['track-line', (p) => `<b>Flight path</b> of <b>${namesRef.current[p.clip_id] ?? p.name}</b> (flight ${p.flight})<br/>The drone flew along this line while recording.`],
      ['holes-fill', (p) => `<b>Gap in the map</b>, about ${Math.round(p.area_m2)} m²<br/>Flown over but could not be stitched: often dense trees, water or the edge of a flight.`],
      ['plots-fill', (p) => `<b>${p.role === 'teach' ? 'Teaching' : 'Test'} square #${p.id}</b>, ${p.done ? `${p.n} trees marked by hand` : 'not marked yet'}<br/>${p.role === 'teach' ? 'Teaches the model what trees look like here' : 'Grades the count; never used for teaching'}`],
      ['density-fill', (p) => `<b>${p.trees} trees</b> in this 20 m square<br/>About ${p.per_ha} trees per hectare`],
      ['coverage-fill', (p) => `<b>Ground the camera saw</b><br/>Area ${COVER[p.kind] ?? 'covered by the camera'}.`],
    ]
    m.on('mousemove', (e: MapMouseEvent) => {
      if (measuringRef.current) { hover.remove(); return }
      const r = 5   // pixels of tolerance so thin lines and dots are easy to hit
      const box: [[number, number], [number, number]] = [[e.point.x - r, e.point.y - r], [e.point.x + r, e.point.y + r]]
      const marking = treesRef.current.mode !== 'off'
      const present = describe.filter(([id]) => m.getLayer(id) && m.getLayoutProperty(id, 'visibility') !== 'none' && (!marking || id === 'edit-pt'))
      for (const [id, html] of present) {
        // areas: exact point; dots: the dot itself; lines: a few pixels either side
        const f = m.queryRenderedFeatures(id === 'track-line' || id.endsWith('-pt') ? (id === 'track-line' ? box : [[e.point.x - 3, e.point.y - 3], [e.point.x + 3, e.point.y + 3]]) : e.point, { layers: [id] })[0]
        if (f) {
          hover.setLngLat(e.lngLat).setHTML(`<div style="font-size:12px;line-height:1.45">${html(f.properties)}</div>`).addTo(m)
          m.getCanvas().style.cursor = treesRef.current.mode !== 'off' ? 'crosshair' : id === 'frames-pt' ? 'pointer' : ''
          return
        }
      }
      hover.remove()
      m.getCanvas().style.cursor = treesRef.current.mode !== 'off' ? 'crosshair' : ''
    })
    m.on('mouseout', () => hover.remove())
    m.on('click', (e: MapMouseEvent) => {
      if (treesRef.current.mode !== 'off') {
        // a click on one of your own marks removes it; report which one
        const hit = m.queryRenderedFeatures([[e.point.x - 6, e.point.y - 6], [e.point.x + 6, e.point.y + 6]], { layers: ['edit-pt'] })[0]
        clickRef.current?.([e.lngLat.lng, e.lngLat.lat], hit ? Number(hit.properties.i) : null)
        return
      }
      if (!measuringRef.current) return
      setMeasure((pts) => [...pts, [e.lngLat.lng, e.lngLat.lat]])
    })
    map.current = m
    ;(window as any).__naqsha = m   // handy from the devtools console
    return () => m.remove()
  }, [])

  // street map toned to the theme: dimmed under the dark UI, natural under the light one
  useEffect(() => {
    const m = map.current
    if (!ready || !m) return
    const light = theme === 'light'
    m.setPaintProperty('bg', 'background-color', light ? '#e9eef3' : '#0b1220')
    m.setPaintProperty('osm', 'raster-brightness-max', light ? 1 : 0.85)
    m.setPaintProperty('osm', 'raster-saturation', light ? -0.15 : -0.4)
  }, [ready, theme])

  // basemap
  useEffect(() => {
    const m = map.current
    if (!ready || !m) return
    m.setLayoutProperty('osm', 'visibility', layers.basemap === 'osm' ? 'visible' : 'none')
    m.setLayoutProperty('imagery', 'visibility', layers.basemap === 'imagery' ? 'visible' : 'none')
  }, [ready, layers.basemap])

  // track + homes
  useEffect(() => {
    const m = map.current
    if (!ready || !m) return
    ;(m.getSource('track') as GeoJSONSource).setData((track as any) ?? EMPTY)
    const expr: any[] = ['match', ['get', 'clip_id']]
    clipIds.forEach((id, i) => expr.push(id, clipColor(i)))
    expr.push('#f59e0b')
    m.setPaintProperty('track-line', 'line-color', (clipIds.length ? expr : '#f59e0b') as any)
    m.setLayoutProperty('track-line', 'visibility', layers.track ? 'visible' : 'none')
    m.setFilter('track-line', hiddenClips.length ? ['!', ['in', ['get', 'clip_id'], ['literal', hiddenClips]]] : null)
    ;(m.getSource('homes') as GeoJSONSource).setData({
      type: 'FeatureCollection',
      features: homes.map((h) => ({ type: 'Feature', properties: { observed: h.observed, flight: h.flight ?? null }, geometry: { type: 'Point', coordinates: [h.lon, h.lat] } })),
    })
  }, [ready, track, homes, clipIds, layers.track, hiddenClips])

  // coverage
  useEffect(() => {
    const m = map.current
    if (!ready || !m) return
    ;(m.getSource('coverage') as GeoJSONSource).setData((coverage as any) ?? EMPTY)
    const vis = layers.coverage === 'off' ? 'none' : 'visible'
    m.setLayoutProperty('coverage-fill', 'visibility', vis)
    m.setLayoutProperty('coverage-line', 'visibility', vis)
    m.setFilter('coverage-fill', ['==', ['get', 'kind'], layers.coverage])
    m.setFilter('coverage-line', ['==', ['get', 'kind'], layers.coverage])
  }, [ready, coverage, layers.coverage])

  // frames, coloured by what the latest run thought of them
  useEffect(() => {
    const m = map.current
    if (!ready || !m) return
    const flagged = new Map<string, number>((run?.qa?.flagged_images ?? []).map((f: any) => [f.image, f.dxy]))
    const dropped = new Set<string>(run?.qa?.registration?.dropped ?? [])
    const excluded = new Set<string>(run?.excluded ?? [])
    const imgs: FrameImage[] = selection?.images ?? []
    ;(m.getSource('frames') as GeoJSONSource).setData({
      type: 'FeatureCollection',
      features: imgs.map((im) => ({
        type: 'Feature',
        properties: { ...im, sid: selection!.id, dxy: flagged.get(im.name) ?? null,
          state: excluded.has(im.name) ? 'excluded' : flagged.has(im.name) ? 'flagged' : dropped.has(im.name) ? 'dropped' : 'ok' },
        geometry: { type: 'Point', coordinates: [im.lon, im.lat] },
      })),
    })
    m.setLayoutProperty('frames-pt', 'visibility', layers.frames ? 'visible' : 'none')
  }, [ready, selection, run, layers.frames])

  // orthophoto / DSM / holes from the chosen run
  useEffect(() => {
    const m = map.current
    if (!ready || !m) return
    for (const id of ['ortho', 'sharp', 'dsm']) {
      if (m.getLayer(id)) m.removeLayer(id)
      if (m.getSource(id)) m.removeSource(id)
    }
    const before = 'coverage-fill'
    if (run?.layers?.orthophoto) {
      const b = run.layers.orthophoto.bounds
      m.addSource('ortho', { type: 'raster', tiles: [tileUrl(run.id, 'orthophoto')], tileSize: 256, maxzoom: 23, bounds: b as any })
      m.addLayer({ id: 'ortho', type: 'raster', source: 'ortho', paint: { 'raster-opacity': layers.orthoOpacity, 'raster-resampling': 'linear' },
        layout: { visibility: layers.ortho ? 'visible' : 'none' } }, before)
    }
    if (run?.layers?.sharp) {
      const b = run.layers.sharp.bounds
      m.addSource('sharp', { type: 'raster', tiles: [tileUrl(run.id, 'sharp')], tileSize: 256, maxzoom: 23, bounds: b as any })
      m.addLayer({ id: 'sharp', type: 'raster', source: 'sharp', paint: { 'raster-opacity': layers.orthoOpacity, 'raster-resampling': 'linear' },
        layout: { visibility: layers.sharp ? 'visible' : 'none' } }, before)
    }
    if (run?.layers?.dsm) {
      const b = run.layers.dsm.bounds
      m.addSource('dsm', { type: 'raster', tiles: [tileUrl(run.id, 'dsm')], tileSize: 256, maxzoom: 22, bounds: b as any })
      m.addLayer({ id: 'dsm', type: 'raster', source: 'dsm', paint: { 'raster-opacity': 0.85 },
        layout: { visibility: layers.dsm ? 'visible' : 'none' } }, before)
    }
    ;(m.getSource('holes') as GeoJSONSource).setData(run?.qa?.holes?.geojson ?? EMPTY)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ready, run?.id, run?.layers?.orthophoto?.bounds?.join(), run?.layers?.sharp?.bounds?.join(), run?.status])

  useEffect(() => {
    const m = map.current
    if (!ready || !m) return
    if (m.getLayer('ortho')) {
      m.setLayoutProperty('ortho', 'visibility', layers.ortho ? 'visible' : 'none')
      m.setPaintProperty('ortho', 'raster-opacity', layers.orthoOpacity)
    }
    if (m.getLayer('sharp')) {
      m.setLayoutProperty('sharp', 'visibility', layers.sharp ? 'visible' : 'none')
      m.setPaintProperty('sharp', 'raster-opacity', layers.orthoOpacity)
    }
    if (m.getLayer('dsm')) m.setLayoutProperty('dsm', 'visibility', layers.dsm ? 'visible' : 'none')
    const hv = layers.holes ? 'visible' : 'none'
    m.setLayoutProperty('holes-fill', 'visibility', hv)
    m.setLayoutProperty('holes-line', 'visibility', hv)
  }, [ready, layers.ortho, layers.sharp, layers.dsm, layers.orthoOpacity, layers.holes, run?.id, run?.layers?.sharp?.bounds?.join()])

  // fit to the flight once per project, as soon as its track is loaded
  const fitted = useRef('')
  useEffect(() => {
    const m = map.current
    if (!ready || !m || !track || fitted.current === focusKey) return
    const coords: number[][] = (track as any).features.flatMap((f: any) => f.geometry.coordinates)
    if (!coords.length) return
    fitted.current = focusKey
    const lons = coords.map((c) => c[0]), lats = coords.map((c) => c[1])
    const b: [number, number, number, number] = [Math.min(...lons), Math.min(...lats), Math.max(...lons), Math.max(...lats)]
    // the panels above the map finish laying out after the data arrives
    setTimeout(() => { m.resize(); m.fitBounds(b, { padding: { top: 50, bottom: 40, right: 50, left: 290 }, duration: 0 }) }, 80)
  }, [ready, focusKey, track])

  // trees: results (hidden while marking, so the count cannot steer the hand count)
  useEffect(() => {
    const m = map.current
    if (!ready || !m) return
    const t = trees
    const base = t.rid != null && t.has ? `/api/runs/${t.rid}/trees/file` : null
    const q = `v=${t.v}${t.minH > 0 ? `&min_h=${t.minH}` : ''}`
    ;(m.getSource('trees') as GeoJSONSource).setData(base ? `${base}/trees?${q}` : EMPTY)
    ;(m.getSource('crowns') as GeoJSONSource).setData(base ? `${base}/crowns?${q}` : EMPTY)
    ;(m.getSource('density') as GeoJSONSource).setData(base ? `${base}/density?${q}` : EMPTY)
  }, [ready, trees.rid, trees.v, trees.has, trees.minH])

  useEffect(() => {
    const m = map.current
    if (!ready || !m) return
    const marking = trees.mode !== 'off'
    const vis = (on: boolean) => (on && !marking && trees.has ? 'visible' : 'none')
    m.setLayoutProperty('trees-pt', 'visibility', vis(layers.trees))
    m.setLayoutProperty('crowns-line', 'visibility', vis(layers.crowns))
    m.setLayoutProperty('density-fill', 'visibility', vis(layers.density))
    const pv = layers.plots || marking ? 'visible' : 'none'
    for (const id of ['plots-fill', 'plots-line', 'plots-open', 'marks-pt']) m.setLayoutProperty(id, 'visibility', pv)
    for (const id of ['frames-pt']) m.setLayoutProperty(id, 'visibility', marking ? 'none' : (layers.frames ? 'visible' : 'none'))
    const editing = trees.edit
    ;(m.getSource('plots') as GeoJSONSource).setData({ type: 'FeatureCollection', features: trees.plots.map((p) => ({
      type: 'Feature', properties: { id: p.id, role: p.role, n: p.marks.length, done: p.done, used_for_training: p.used_for_training },
      geometry: { type: 'Polygon', coordinates: [[...p.poly, p.poly[0]]] } })) })
    ;(m.getSource('marks') as GeoJSONSource).setData({ type: 'FeatureCollection', features: marking ? [] : trees.plots.flatMap((p) =>
      p.marks.map((c) => ({ type: 'Feature', properties: {}, geometry: { type: 'Point', coordinates: c } }))) })
    ;(m.getSource('edit') as GeoJSONSource).setData({ type: 'FeatureCollection', features: editing ? [
      { type: 'Feature', properties: {}, geometry: { type: 'Polygon', coordinates: [editing.poly] } },
      ...editing.marks.map((c, i) => ({ type: 'Feature', properties: { i }, geometry: { type: 'Point', coordinates: c } })),
    ] : [] })
    m.getCanvas().style.cursor = marking ? 'crosshair' : ''
  }, [ready, trees, layers.trees, layers.crowns, layers.density, layers.plots, layers.frames])

  // zoom to a square when the Trees tab asks for it
  useEffect(() => {
    const m = map.current
    if (!ready || !m || !flyTo) return
    const xs = flyTo.bounds.map((c) => c[0]), ys = flyTo.bounds.map((c) => c[1])
    m.fitBounds([[Math.min(...xs), Math.min(...ys)], [Math.max(...xs), Math.max(...ys)]], { padding: { top: 120, bottom: 60, left: 60, right: 60 }, maxZoom: 21, duration: 600 })
  }, [ready, flyTo?.key])

  // measure overlay
  useEffect(() => {
    const m = map.current
    if (!ready || !m) return
    const feats: any[] = measure.map((c) => ({ type: 'Feature', properties: {}, geometry: { type: 'Point', coordinates: c } }))
    if (measure.length >= 2) feats.push({ type: 'Feature', properties: {}, geometry: { type: 'LineString', coordinates: measure } })
    if (measure.length >= 3) feats.push({ type: 'Feature', properties: {}, geometry: { type: 'Polygon', coordinates: [[...measure, measure[0]]] } })
    ;(m.getSource('measure') as GeoJSONSource).setData({ type: 'FeatureCollection', features: feats })
    m.getCanvas().style.cursor = measuring ? 'crosshair' : ''
  }, [ready, measure, measuring])

  const dist = measure.slice(1).reduce((s, p, i) => s + haversine(measure[i], p), 0)
  return (
    <div className="relative h-full w-full">
      <div className="absolute inset-0"><div ref={el} className="h-full w-full" /></div>
      <div className="absolute left-3 top-3 flex items-center gap-2">
        <button onClick={() => { setMeasuring(!measuring); setMeasure([]) }}
          className={`inline-flex items-center gap-1.5 rounded-lg border px-3 py-1.5 text-xs font-medium shadow-[var(--shadow-sm)] transition-colors ${measuring ? 'border-saffron-400 bg-saffron-500 text-on-saffron' : 'border-slate-700 bg-slate-900/95 text-slate-100 hover:bg-slate-800'}`}>
          <IconRuler size={14} />{measuring ? 'Stop measuring' : 'Measure'}
        </button>
        {measuring && (
          <div className="num rounded-lg border border-slate-700 bg-slate-900/95 px-3 py-1.5 text-xs text-slate-100 shadow-[var(--shadow-sm)]">
            {measure.length < 2 ? 'Click points on the map' : <>Distance {fmtM(dist)}{measure.length >= 3 && <> · Area {fmtArea(polygonArea(measure))}</>}</>}
          </div>
        )}
      </div>
    </div>
  )
}
