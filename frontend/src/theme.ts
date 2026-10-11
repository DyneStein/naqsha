export type Theme = 'dark' | 'light'

const KEY = 'naqsha-theme'

/** The viewer's last choice, else the system setting. Storage can be unavailable (private windows). */
export function initialTheme(): Theme {
  try {
    const t = localStorage.getItem(KEY)
    if (t === 'dark' || t === 'light') return t
  } catch { /* ignore */ }
  return matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark'
}

export function applyTheme(t: Theme) {
  document.documentElement.dataset.theme = t
  document.querySelector('meta[name="theme-color"]')?.setAttribute('content', t === 'light' ? '#f2f5f8' : '#071425')
}

export function saveTheme(t: Theme) {
  applyTheme(t)
  try { localStorage.setItem(KEY, t) } catch { /* ignore */ }
}
