# ROOKS design system

Everything is in `workbench/frontend/src/styles.css` (plain CSS custom properties, no CSS framework) and themed by
`src/appearance.ts`. The v12 layer is the block marked `v12 design system` near the end of the file. No component
library is used; icons are `lucide-react`, the only visual dependency.

## Principles

1. **Quiet by default.** Text, borders and space do the work. One accent colour, used for the primary action, the
   selected item and focus. No gradients, glows or decorative shadows on content.
2. **Fewer boxes.** A card marks a separate object (a mission, a provider, a form). Related figures share one panel
   with dividers (Home's summary); groups of content get a quiet `section-label` heading instead of another box.
3. **One obvious primary action per area.** `btn primary` appears at most once per area. Secondary actions are plain
   `btn`; low-weight ones are `btn ghost`.
4. **Real state only.** A control appears only when it can do something now. A control that is relevant but not
   allowed (a paid model in Free Cloud AI) is shown disabled with its reason. A control that is irrelevant (Stop when
   Computer Use is off) is not shown. Nothing on screen is decorative data.
5. **Say what happened.** Every save shows its state next to the control (`save-state`): Saving, Saved, or Not saved
   with Retry. Toasts announce actions; errors use `role="alert"`, confirmations `role="status"`.
6. **One overlay at a time.** Opening search closes the phone menu; Escape closes the top overlay and returns focus to
   where it was.

## Tokens

### Colour

| Token | Light | Dark | Use |
|---|---|---|---|
| `--bg` | `#f7f7f8` | `#0f0f12` | page background |
| `--panel` | `#ffffff` | `#17171b` | cards, sidebar, inputs |
| `--panel-2` | `#f2f2f4` | `#1e1e23` | hover, quiet fills, code |
| `--border` | `#e4e4e8` | `#2a2a31` | dividers, card edges |
| `--border-strong` | `#d0d0d7` | `#3a3a43` | inputs, buttons |
| `--text` | `#18181b` | `#ececf1` | body text |
| `--muted` | `#6b6b76` | `#a0a0ab` | secondary text |
| `--faint` | `#63636e` | `#8c8c98` | meta text (meets AA on `--panel` and `--bg`) |
| `--accent` / `--accent-soft` / `--accent-text` | `#3f4bd1` / `#eceefd` / `#3340b4` | `#5a66ea` / `#1f2449` / `#aeb6ff` | primary action, selection, links |
| `--ok`, `--warn`, `--danger` (+ `-soft`) | `#166b39`, `#8a5300`, `#b3282d` | `#4cc47e`, `#f0b04c`, `#f06a6a` | status only, always with words or an icon |

Themes in Settings, Appearance (presets, custom themes, accent, high contrast) override these tokens; `appearance.ts`
checks that status colours keep AA contrast on their soft backgrounds. Dark mode follows the device unless a theme is
chosen (`data-theme`).

### Type

| Token | Size | Use |
|---|---|---|
| body (`--font-size`) | 14px, adjustable 12 to 18 | everything by default |
| `--t-xs` | 12px | meta, section labels, hints |
| `--t-sm` | 13px | secondary text, small buttons |
| `--t-lg` | 16px | `h2`, card titles |
| `--t-xl` | 18px | Settings section titles |
| `--t-2xl` / `h1` | 22px | page titles |

System font stack (`--font`), monospace (`--mono`) for code, ids and model names. Answers use `.prose` at a 1.65 line
height in a 760px reading column (`--content`).

### Space, radius, focus

- Spacing on a 4px grid: `--s-1` 4px to `--s-10` 40px. Pages pad 28px 32px (20px 16px on phones); cards 16px.
- Radius: `--radius` 10px (cards), `--radius-sm` 7px (buttons, inputs). Chat composer 18px.
- Focus: 2px accent outline offset 2px on links, buttons and rows; inputs show an accent border; switches use
  `--ring`. Focus is never removed without a replacement.

## Components

| Class | What it is | Notes |
|---|---|---|
| `.btn`, `.primary`, `.ghost`, `.danger`, `.sm`, `.lg`, `.icon` | buttons, 34px high (28 small, 42 large) | icon buttons need `aria-label` |
| `.input`, `select.input`, `textarea.input` | fields, 34px | every field has a visible label or `aria-label` |
| `.card`, `.card.flush` | a separate object | `button.card` when the whole card opens something |
| `.list-item` + `.list-link` | a row in a list | the row's main area is a real link or button stretched over the row; row buttons sit above it |
| `.section-label` | quiet group heading | instead of a box |
| `.glance` / `.glance-tile` | one panel of figures with dividers | 5 columns, 2 on tablets, 1 on phones |
| `.badge` (`ok`, `warn`, `danger`, `accent`) | short state | never colour alone |
| `.alert` (`err`, `ok`) | a message about this area | errors use `role="alert"` |
| `.tabs` / `.tab` | switch views in one area | |
| `.choice` | a large radio option | used in a `role="radiogroup"` with arrow keys |
| `.switch` | on or off | a real checkbox with `role="switch"` |
| `.save-state` (`ok`, `err`) | the save status next to a form | `role="status"`, `aria-live="polite"` |
| `.setting-row` | a setting: what it is left, control right | wraps on phones |
| `.empty` | an empty state | says what goes here and how to start |
| `.skeleton` | loading placeholder | no shimmer with reduced motion |
| `.job-card` (v13) | work started from a conversation: research, mission, coding task | accent edge; keeps itself current; one primary action (Approve plan and start) at most |
| `.conn-row`, `.cap-row` / `.cap` (v13) | a connection in the directory; one chip per capability test | chip states `passed`, `failed`, `not_tested`, each with an icon and words, never colour alone |
| `.area-tabs` / `.area-tab` (v13) | the pages of one area (Library, Planner, Work) | real links; the active one has `aria-current` |

## Layout

- **Navigation (v13):** seven areas in the sidebar (Assistant, Home, Needs you, Projects, Library, Planner, Work), then
  More (Mail, Memory & skills, What ROOKS can do, Compare models, Browser agent) and Settings. An area's pages are tabs
  at the top of the page. Every older address still opens the same page.
- **Shell:** sidebar (232px, collapsible to 64px) and one scrolling main column. The page itself never scrolls; the
  browser test `screens.py` checks this on every route.
- **Phones (900px and below):** a top bar with menu, search and chat; the sidebar becomes a drawer; two-pane pages
  show the list or the item, not both.
- **Widths:** pages up to 1180px; reading content up to 760px.

## Motion

Transitions are 120 to 200ms on colour, opacity and transform only. When the device asks for reduced motion,
transitions and the loading shimmer stop; spinners keep turning so loading stays visible.
