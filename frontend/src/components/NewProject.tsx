import { useEffect, useState } from 'react'
import { api } from '../api'
import { IconFilm, IconFolder, IconUp } from './icons'
import { Button, Field, inputCls } from './ui'

export function NewProject({ onCreated, onClose }: { onCreated: (id: number) => void; onClose: () => void }) {
  const [name, setName] = useState('')
  const [path, setPath] = useState('')
  const [browse, setBrowse] = useState<Awaited<ReturnType<typeof api.fs>> | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const open = (p?: string) => api.fs(p).then(setBrowse).catch((e) => setErr(String(e.message ?? e)))
  useEffect(() => { open() }, [])

  const videos = browse?.entries.filter((e) => !e.dir) ?? []
  const submit = async () => {
    setBusy(true); setErr(null)
    try {
      const r = await api.createProject(name, path || browse?.path || '')
      onCreated(r.id)
    } catch (e: any) {
      setErr(e.message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/70 p-4 backdrop-blur-[2px]" onClick={onClose}>
      <div className="w-full max-w-xl rounded-2xl border border-slate-700 bg-slate-900 p-5 shadow-[var(--shadow-lg)]" onClick={(e) => e.stopPropagation()}>
        <h2 className="text-base font-semibold">New project</h2>
        <p className="mt-1 text-sm text-slate-400">Point at the folder holding the drone videos. Files are read in place, never copied.</p>
        <div className="mt-3 space-y-3">
          <Field label="Name"><input className={inputCls} value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. OGDCL site, Oct 2026" /></Field>
          <Field label="Video folder"><input className={inputCls} value={path || browse?.path || ''} onChange={(e) => setPath(e.target.value)} /></Field>
          {browse && (
            <div className="rounded-xl border border-slate-800 bg-slate-950">
              <div className="flex items-center justify-between border-b border-slate-800 px-2 py-1 text-xs text-slate-400">
                <button className="inline-flex items-center gap-1 hover:text-slate-200" onClick={() => { setPath(''); open(browse.parent) }}><IconUp size={13} />up</button>
                <span>{videos.length} video file{videos.length === 1 ? '' : 's'} here</span>
              </div>
              <ul className="max-h-56 overflow-auto py-1 text-sm">
                {browse.entries.map((e) => (
                  <li key={e.name}>
                    {e.dir ? (
                      <button className="w-full px-2 py-0.5 text-left hover:bg-slate-800" onClick={() => { setPath(''); open(`${browse.path}/${e.name}`) }}><span className="inline-flex items-center gap-1.5"><IconFolder size={14} className="text-saffron-400" />{e.name}</span></button>
                    ) : (
                      <div className="flex items-center gap-1.5 px-2 py-0.5 text-slate-400"><IconFilm size={14} className="text-sky-400" />{e.name} <span className="text-slate-600">{((e.size ?? 0) / 2 ** 30).toFixed(2)} GB</span></div>
                    )}
                  </li>
                ))}
              </ul>
            </div>
          )}
          {err && <p className="text-sm text-rose-300">{err}</p>}
        </div>
        <div className="mt-4 flex justify-end gap-2">
          <Button kind="ghost" onClick={onClose}>Cancel</Button>
          <Button kind="primary" disabled={busy || (!path && !videos.length)} onClick={submit}>Create and analyse</Button>
        </div>
      </div>
    </div>
  )
}
