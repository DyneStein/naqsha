# Naqsha design system

Operate-mode tool UI for survey engineers. Dark "survey ink" surfaces so the map imagery carries the colour; brand
colours come from the logo (`../brand/`).

## Layout

- The window never scrolls. Header (48 px), project bar with the 3-step tracker, one stats strip, then the workspace:
  map (flex) plus a 25 rem side panel with tabs Build / Maps / Videos / Activity. Only panels scroll inside themselves.
- The project rail on the left can be hidden from the header.
- Explanations live in `Hint` bubbles (the small (i)), not in always-visible paragraphs. Rarely used groups
  (advanced settings, photo sets, downloads, per-video details) use `Fold` or expand on click.

## Tokens (`src/index.css`, `@theme`)

Tailwind's `slate` and `sky` scales are re-pointed, so existing classes follow the brand.

| Role | Token | Value |
|---|---|---|
| App ground | slate-950 | `#071425` |
| Panels | slate-900 | `#0c1d31` |
| Hairlines, raised controls | slate-800 / 700 | `#152c45` / `#243f5b` |
| Muted text | slate-400 | `#8aa1b8` |
| Body text | slate-200 | `#d2dde8` |
| Primary action, done state | sky-600 / 500 | `#0f8a82` / `#1aa69d` (teal) |
| Current step, progress, brand accent, focus ring | saffron-500 / 400 | `#f4a62a` / `#f9b850` |
| Warnings | saffron-950 ground, saffron-200 text | |
| Errors | rose-950 / rose-300 | |
| Good | emerald-950 / emerald-300 | |

Saffron means "now" (current step, running progress, the active tab underline). Teal means "act" or "done".

## Type

- Archivo Variable (self-hosted, `@fontsource-variable/archivo`, width axis). Body 14 px at width 100; headings at
  width 112, letter-spacing -0.015em. Tabular numerals (`.num`) for every figure.
- Wordmark: "Naqsha" Archivo bold at width 118, plus نقشہ in Noto Nastaliq Urdu 600 in saffron.

## Components (`src/components/ui.tsx`, `icons.tsx`)

- `Hint`: (i) button, tooltip via portal (escapes panel overflow), hover, focus or click.
- `Section`: heading plus optional hint, no box. `Fold`: collapsible group with count. `Card`: 12 px radius, used
  sparingly for list items (runs, photo sets, videos, jobs).
- `Button`: primary (teal), default, ghost, danger; `sm` and `md`. `Badge`: pill status chips.
- Icons: one authored stroke set (24 grid, 1.75 stroke). No emoji or Unicode glyphs as icons.
- Elevation: soft offset shadows `0 6-10px 18-30px rgb(2 8 18 / .5)` on floating map controls and popovers only.

## Map chrome

MapLibre controls, popups, attribution and scale bar are themed in `index.css`. Data colours on the map (clip
palette in `format.ts`, measure magenta, coverage green, gaps red) stay as they are: they are chosen for contrast on
both street and satellite backgrounds, not for brand.
