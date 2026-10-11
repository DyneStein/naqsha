import { useRef, useState, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { IconChevron, IconInfo, IconSpinner } from './icons'

/** Small "i" bubble: explains a section on hover or focus, so the text is there when wanted and out of the way when not. */
export function Hint({ children, title, side = 'bottom', size = 14 }: { children: ReactNode; title?: string; side?: 'bottom' | 'top' | 'right'; size?: number }) {
  const ref = useRef<HTMLButtonElement>(null)
  const [pos, setPos] = useState<{ x: number; y: number } | null>(null)
  const show = () => {
    const r = ref.current?.getBoundingClientRect()
    if (!r) return
    const W = 280
    if (side === 'right') setPos({ x: Math.min(r.right + 8, innerWidth - W - 8), y: Math.max(8, r.top - 6) })
    else setPos({ x: Math.max(8, Math.min(r.left + r.width / 2 - W / 2, innerWidth - W - 8)), y: side === 'top' ? r.top - 8 : r.bottom + 8 })
  }
  return (
    <>
      <button ref={ref} type="button" aria-label={title ? `About ${title}` : 'More information'}
        onMouseEnter={show} onMouseLeave={() => setPos(null)} onFocus={show} onBlur={() => setPos(null)}
        onClick={(e) => { e.preventDefault(); e.stopPropagation(); pos ? setPos(null) : show() }}
        className="inline-flex shrink-0 cursor-help items-center text-slate-500 transition-colors hover:text-saffron-400 focus-visible:text-saffron-400">
        <IconInfo size={size} />
      </button>
      {pos && createPortal(
        <div role="tooltip" className="tip pointer-events-none fixed z-[100] w-[280px] rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-[12.5px] leading-snug text-slate-200"
          style={{ left: pos.x, top: pos.y, transform: side === 'top' ? 'translateY(-100%)' : undefined, boxShadow: 'var(--shadow-md)' }}>
          {title && <div className="mb-0.5 font-semibold text-white">{title}</div>}
          {children}
        </div>, document.body)}
    </>
  )
}

/** Section heading inside a panel: a title, an optional help bubble, optional actions. No box around it. */
export function Section({ title, hint, actions, children, className = '' }: { title: ReactNode; hint?: ReactNode; actions?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <section className={className}>
      <header className="mb-2 flex items-center justify-between gap-2">
        <h3 className="flex items-center gap-1.5 text-[13px] font-semibold text-slate-100">{title}{hint && <Hint title={typeof title === 'string' ? title : undefined}>{hint}</Hint>}</h3>
        {actions && <div className="flex items-center gap-1.5">{actions}</div>}
      </header>
      {children}
    </section>
  )
}

/** Collapsible group; closed by default so the panel stays short. */
export function Fold({ title, hint, count, defaultOpen = false, children }: { title: string; hint?: ReactNode; count?: number; defaultOpen?: boolean; children: ReactNode }) {
  const [open, setOpen] = useState(defaultOpen)
  return (
    <div className="border-t border-slate-800 pt-2">
      <div className="flex items-center gap-1.5">
        <button type="button" onClick={() => setOpen(!open)} aria-expanded={open}
          className="flex flex-1 items-center gap-1.5 py-1 text-left text-[13px] font-semibold text-slate-200 hover:text-white">
          <IconChevron size={14} className={`text-slate-500 transition-transform duration-200 ${open ? 'rotate-90' : ''}`} />
          {title}{count != null && <span className="num rounded-full bg-slate-800 px-1.5 text-[11px] font-medium text-slate-300">{count}</span>}
        </button>
        {hint && <Hint title={title}>{hint}</Hint>}
      </div>
      {open && <div className="mt-1.5">{children}</div>}
    </div>
  )
}

export function Card({ title, actions, children, className = '' }: { title?: ReactNode; actions?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <section className={`rounded-xl border border-slate-800 bg-slate-900/70 ${className}`}>
      {(title || actions) && (
        <header className="flex items-center justify-between gap-2 px-3 pt-2.5">
          <h3 className="text-[13px] font-semibold text-slate-100">{title}</h3>
          <div className="flex items-center gap-2">{actions}</div>
        </header>
      )}
      <div className="p-3">{children}</div>
    </section>
  )
}

/** A button that answers the click at once: if its action returns a promise it disables
 * itself and shows a spinner until the work is done, so a second click cannot repeat it. */
export function Button({ children, onClick, kind = 'default', disabled, title, type = 'button', size = 'md' }: {
  children: ReactNode; onClick?: () => unknown; kind?: 'default' | 'primary' | 'danger' | 'ghost'; disabled?: boolean; title?: string
  type?: 'button' | 'submit'; size?: 'sm' | 'md'
}) {
  const [pending, setPending] = useState(false)
  const styles = {
    default: 'bg-slate-800 hover:bg-slate-700 text-slate-100 border border-slate-700',
    primary: 'bg-sky-600 hover:bg-sky-500 text-on-accent border border-sky-500 shadow-[var(--shadow-accent)]',
    danger: 'bg-transparent hover:bg-rose-950/60 text-rose-300 border border-rose-900/80',
    ghost: 'bg-transparent hover:bg-slate-800 text-slate-300 border border-transparent',
  }[kind]
  const pad = size === 'sm' ? 'px-2 py-1 text-xs' : 'px-3 py-1.5 text-[13px]'
  const click = () => {
    if (pending || !onClick) return
    const r = onClick() as any
    if (r && typeof r.then === 'function') {
      setPending(true)
      Promise.resolve(r).catch(() => {}).finally(() => setPending(false))
    }
  }
  return (
    <button type={type} title={title} disabled={disabled || pending} onClick={click} aria-busy={pending || undefined}
      className={`inline-flex items-center gap-1.5 rounded-lg font-medium transition-colors duration-150 disabled:cursor-not-allowed ${pending ? 'cursor-wait' : 'disabled:opacity-40'} ${pad} ${styles}`}>
      {pending && <IconSpinner size={size === 'sm' ? 12 : 14} />}
      {children}
    </button>
  )
}

const BADGE: Record<string, string> = {
  good: 'bg-emerald-950/70 text-emerald-300 border-emerald-800/80',
  done: 'bg-emerald-950/70 text-emerald-300 border-emerald-800/80',
  analyzed: 'bg-emerald-950/70 text-emerald-300 border-emerald-800/80',
  ok: 'bg-emerald-950/70 text-emerald-300 border-emerald-800/80',
  fair: 'bg-saffron-950 text-saffron-300 border-saffron-900',
  weak: 'bg-saffron-950 text-saffron-300 border-saffron-900',
  running: 'bg-sky-950 text-sky-300 border-sky-800',
  analyzing: 'bg-sky-950 text-sky-300 border-sky-800',
  queued: 'bg-slate-800 text-slate-300 border-slate-700',
  new: 'bg-slate-800 text-slate-300 border-slate-700',
  poor: 'bg-rose-950/70 text-rose-300 border-rose-900',
  failed: 'bg-rose-950/70 text-rose-300 border-rose-900',
  error: 'bg-rose-950/70 text-rose-300 border-rose-900',
  inconsistent: 'bg-rose-950/70 text-rose-300 border-rose-900',
  cancelled: 'bg-slate-800 text-slate-400 border-slate-700',
}

export function Badge({ value, label }: { value: string; label?: string }) {
  return (
    <span className={`inline-flex items-center whitespace-nowrap rounded-full border px-2 py-px text-[11px] font-medium ${BADGE[value] ?? 'bg-slate-800 text-slate-300 border-slate-700'}`}>
      {label ?? value}
    </span>
  )
}

/** Compact stat for the project strip: label with its help bubble, value underneath. */
export function Stat({ label, value, hint }: { label: string; value: ReactNode; hint?: ReactNode }) {
  return (
    <div className="shrink-0 px-3 first:pl-0">
      <div className="flex items-center gap-1 whitespace-nowrap text-[11px] text-slate-400">{label}{hint && <Hint title={label} size={12}>{hint}</Hint>}</div>
      <div className="num display whitespace-nowrap text-[15px] font-semibold text-slate-50">{value}</div>
    </div>
  )
}

export function Progress({ value, status }: { value: number; status: string }) {
  const color = status === 'failed' ? 'bg-rose-500' : status === 'done' ? 'bg-emerald-500' : 'bg-saffron-500'
  return (
    <div className="h-1.5 w-full overflow-hidden rounded-full bg-slate-800">
      <div className={`h-full rounded-full ${color} transition-[width] duration-500`} style={{ width: `${Math.round(value * 100)}%` }} />
    </div>
  )
}

export function Field({ label, hint, children }: { label: string; hint?: string; children: ReactNode }) {
  return (
    <label className="block">
      <span className="flex items-center gap-1 text-xs font-medium text-slate-300">{label}{hint && <Hint title={label} size={12}>{hint}</Hint>}</span>
      {children}
    </label>
  )
}

export const inputCls = 'mt-1 w-full rounded-lg border border-slate-700 bg-slate-950 px-2 py-1.5 text-sm text-slate-100 outline-none transition-colors focus:border-sky-500'

export function Warnings({ items }: { items?: string[] }) {
  if (!items?.length) return null
  return (
    <ul className="space-y-1">
      {items.map((w, i) => (
        <li key={i} className="rounded-lg border border-saffron-900 bg-saffron-950/70 px-2.5 py-1.5 text-xs text-saffron-200">{w}</li>
      ))}
    </ul>
  )
}
