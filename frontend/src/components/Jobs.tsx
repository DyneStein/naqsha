import { api, type Job } from '../api'
import { fmtElapsed } from '../format'
import { friendlyStage, JOB_TITLE } from '../words'
import { Button, Progress } from './ui'

function eta(job: Job): string {
  if (!job.started_at || job.progress < 0.04) return ''
  const elapsed = Date.now() / 1000 - job.started_at
  const left = elapsed * (1 - job.progress) / job.progress
  if (left < 60) return 'under a minute left'
  const m = Math.round(left / 60)
  return m < 90 ? `about ${m} min left` : `about ${(m / 60).toFixed(1)} h left`
}

export function JobRow({ job, onChange }: { job: Job; onChange: () => void }) {
  const active = job.status === 'running' || job.status === 'queued'
  const title = JOB_TITLE[job.kind] ?? job.kind
  const color = job.status === 'failed' ? 'text-rose-300' : job.status === 'done' ? 'text-emerald-300' : 'text-slate-300'
  return (
    <div className="rounded-xl border border-slate-800 bg-slate-900/60 p-2.5">
      <div className="flex items-center justify-between gap-2 text-sm">
        <div className="min-w-0">
          <span className="font-medium text-slate-100">{title}</span>
          <span className={`ml-2 text-xs ${color}`}>
            {job.status === 'queued' ? 'waiting to start' : job.status === 'running' ? 'in progress' : job.status}
          </span>
        </div>
        <div className="flex items-center gap-2 text-xs text-slate-500">
          <span className="num">{fmtElapsed(job.started_at, job.finished_at)}</span>
          {active && <Button size="sm" kind="danger" onClick={() => api.cancelJob(job.id).then(onChange)}>Stop</Button>}
        </div>
      </div>
      {active && (
        <div className="mt-2 space-y-1">
          <Progress value={job.progress} status={job.status} />
          <div className="flex justify-between gap-2 text-xs">
            <span className="truncate text-slate-200">{friendlyStage(job.stage)}</span>
            <span className="num shrink-0 text-slate-500">{Math.round(job.progress * 100)}% {eta(job) && `· ${eta(job)}`}</span>
          </div>
          {job.message && <div className="truncate text-[11px] text-slate-500">{job.message}</div>}
        </div>
      )}
      {job.status === 'failed' && job.error && (
        <details className="mt-1 text-xs text-rose-300">
          <summary className="cursor-pointer">{job.error.split('\n')[0].slice(0, 300)}</summary>
          <pre className="mt-1 max-h-48 overflow-auto whitespace-pre-wrap text-[11px] text-rose-200/80">{job.error}</pre>
        </details>
      )}
    </div>
  )
}
