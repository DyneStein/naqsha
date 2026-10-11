import type { SVGProps } from 'react'

// One stroke family for the whole app: 24-unit grid, 1.75 stroke, round joins.
function Svg({ children, size = 16, ...p }: SVGProps<SVGSVGElement> & { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.75}
      strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" {...p}>{children}</svg>
  )
}

type P = SVGProps<SVGSVGElement> & { size?: number }
export const IconInfo = (p: P) => <Svg {...p}><circle cx="12" cy="12" r="9" /><path d="M12 11v5.5M12 7.6v.1" /></Svg>
export const IconCheck = (p: P) => <Svg {...p}><path d="M5 12.5l4.5 4.5L19 7.5" /></Svg>
export const IconChevron = (p: P) => <Svg {...p}><path d="M9 6l6 6-6 6" /></Svg>
export const IconDownload = (p: P) => <Svg {...p}><path d="M12 4v11M7 10.5l5 5 5-5M5 20h14" /></Svg>
export const IconFolder = (p: P) => <Svg {...p}><path d="M3.5 7.5a2 2 0 012-2h4l2 2.5h7a2 2 0 012 2v7.5a2 2 0 01-2 2h-13a2 2 0 01-2-2z" /></Svg>
export const IconFilm = (p: P) => <Svg {...p}><rect x="3.5" y="5" width="17" height="14" rx="2" /><path d="M7.5 5v14M16.5 5v14M3.5 9.5h4M3.5 14.5h4M16.5 9.5h4M16.5 14.5h4" /></Svg>
export const IconUp = (p: P) => <Svg {...p}><path d="M12 19V6M6.5 11.5L12 6l5.5 5.5" /></Svg>
export const IconPlus = (p: P) => <Svg {...p}><path d="M12 5v14M5 12h14" /></Svg>
export const IconRuler = (p: P) => <Svg {...p}><path d="M4 16.5L16.5 4 20 7.5 7.5 20z" /><path d="M8 12.5l1.8 1.8M11 9.5l1.8 1.8M14 6.5l1.8 1.8" /></Svg>
export const IconLayers = (p: P) => <Svg {...p}><path d="M12 4l8.5 4.5L12 13 3.5 8.5z" /><path d="M3.5 12.5L12 17l8.5-4.5M3.5 16.5L12 21l8.5-4.5" /></Svg>
export const IconMore = (p: P) => <Svg {...p}><circle cx="5.5" cy="12" r="1" /><circle cx="12" cy="12" r="1" /><circle cx="18.5" cy="12" r="1" /></Svg>
export const IconSidebar = (p: P) => <Svg {...p}><rect x="3.5" y="4.5" width="17" height="15" rx="2" /><path d="M9.5 4.5v15" /></Svg>
export const IconClose = (p: P) => <Svg {...p}><path d="M6 6l12 12M18 6L6 18" /></Svg>
export const IconSun = (p: P) => <Svg {...p}><circle cx="12" cy="12" r="4" /><path d="M12 2.5v2M12 19.5v2M4.6 4.6l1.4 1.4M18 18l1.4 1.4M2.5 12h2M19.5 12h2M4.6 19.4L6 18M18 6l1.4-1.4" /></Svg>
export const IconMoon = (p: P) => <Svg {...p}><path d="M19.5 14.5A8 8 0 019.5 4.5a8 8 0 1010 10z" /></Svg>
export const IconSpinner = (p: P) => <Svg {...p} className={`animate-spin ${p.className ?? ''}`}><path d="M12 3.5a8.5 8.5 0 108.5 8.5" /></Svg>
