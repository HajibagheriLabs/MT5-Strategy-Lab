# StrategyLab design

## Intent

StrategyLab is an instrument for reading backtests: tables of deals, columns of figures, a
balance curve, a log. It should feel calm, dense and exact, closer to a well-made trading
terminal or audio tool than to a web product. Quality comes from type, alignment and spacing
rhythm. Surfaces are flat and separated by 1px hairlines and space, never by cards floating on a
grey page. Colour is scarce, so where it appears it means something: one accent marks what you
can act on or where you are, and two colours, used for nothing else, mark profit and loss. The
interface never decorates and never hides a number behind an effect.

## Tokens

Every colour, size, space, radius, shadow and duration lives in `src/styles/tokens.css` as a CSS
variable. Components use tokens only; a raw hex value or pixel size in a component is a bug.

### Colour

Both themes are designed, not derived: the dark theme is a cool graphite, the light theme a
cool paper with ink, and each value was chosen for its own background. Contrast was measured
(WCAG 2.2): every text colour below is at least 4.5:1 on every surface of its theme, and control
borders are at least 3:1.

| Token | Dark | Light | Use |
|---|---|---|---|
| `--bg` | `#101214` | `#f1f2f3` | Page |
| `--surface` | `#15181b` | `#f8f9f9` | Panels, table body |
| `--raised` | `#1b1f23` | `#fdfdfd` | Inputs, menus, dialogs |
| `--sunken` | `#0c0e10` | `#e8eaec` | Code and log wells |
| `--line` | `#262b31` | `#dcdfe2` | Hairlines between things |
| `--line-strong` | `#353b43` | `#c5cacf` | Table header rule, section rule |
| `--control-border` | `#6a727b` | `#7d858d` | Input and button outlines (3:1) |
| `--fg` | `#e4e7ea` | `#15181b` | Primary text, figures |
| `--fg-2` | `#a9b0b8` | `#4a525b` | Labels, secondary text |
| `--fg-3` | `#8b939c` | `#5f6770` | Hints, units, timestamps |
| `--accent` | `#6d93ff` | `#2b59c9` | Primary action, focus, current place |
| `--accent-text` | `#8aa8ff` | `#2450bb` | Accent used as text (links) |
| `--on-accent` | `#0b1020` | `#f8f9fb` | Text on the accent |
| `--profit` | `#3cc3a3` | `#0b7867` | Positive money only |
| `--loss` | `#f2734f` | `#b5401d` | Negative money only |
| `--warning` | `#e0ad4a` | `#875500` | Needs attention |
| `--danger` | `#f2734f` | `#b5401d` | Failed, destructive |
| `--accent-hover` | `#8aa8ff` | `#2450bb` | Primary button under the pointer |
| `--hover` | ink at 5% | ink at 5% | Anything under the pointer |
| `--selected` | accent at 14% | accent at 10% | Selected row, chosen segment |
| `--skeleton` | `#22272c` | `#e1e4e7` | Loading placeholders |
| `--backdrop` | ink at 60% | ink at 32% | Behind a dialog |

Profit is a blue-leaning teal and loss a vermillion. Under simulated deuteranopia and
protanopia (Machado 2009) profit reads as a blue grey and loss as an olive yellow, so the pair
still separates; figures also always carry their sign, so colour is never the only signal.
Profit and loss colours are for money and nothing else: not for success states, not for
decoration. `--danger` shares the loss hue because both mean "bad", but it is its own token.

### Type

Two faces of one family, self-hosted (no request leaves the machine): **IBM Plex Sans** for
words, **IBM Plex Mono** for every figure, so digits are tabular by construction and columns of
numbers line up to the decimal.

| Token | Size / line | Use |
|---|---|---|
| `--text-xs` | 11 / 16 | Column units, keyboard hints |
| `--text-sm` | 12 / 16 | Dense tables, captions, labels |
| `--text-md` | 13 / 20 | Body, controls (the default) |
| `--text-lg` | 15 / 22 | Section titles |
| `--text-xl` | 20 / 28 | Page titles |
| `--text-2xl` | 28 / 32 | Headline figures on a result |

Weights: 400 for text, 500 for labels that need to hold their own and for titles, 600 only for
the headline figure. No uppercase-tracked eyebrows: a section is named by its title. Numeric
columns are right-aligned, and every figure for one symbol uses that symbol's decimal places.

### Space

A 4px base: `--space-1` 2, `--space-2` 4, `--space-3` 6, `--space-4` 8, `--space-5` 12,
`--space-6` 16, `--space-7` 20, `--space-8` 24, `--space-9` 32, `--space-10` 48. Inside a
control, 6 to 12; between related controls, 8 to 12; between sections, 24 to 32. Table rows are
28px (compact) or 32px.

### Radius

One system: `--radius` is 2px, for buttons, inputs, badges, panels and dialogs alike. Nothing is
pill-shaped and nothing is very round; the shape says "tool", not "app".

### Elevation

Surfaces are flat. Only things that float over the page cast a shadow: menus and toasts use
`--shadow-1`, dialogs `--shadow-2`. Shadows are tinted with the theme's ink, never pure black.

### Motion

Motion only explains a change of state: a dialog appearing, a toast arriving, a row being
selected, a skeleton loading. `--duration-1` 100ms (hover, press, exit), `--duration-2` 160ms
(enter), easing `--ease` `cubic-bezier(0.2, 0, 0, 1)`; no bounce, no springs, no looping
animation except a skeleton's slow pulse and a running run's progress. Under
`prefers-reduced-motion` every duration becomes 0 and skeletons stop pulsing.

## Layout

- The shell is a 48px top bar (wordmark, the four sections, terminal status, theme switch) over
  a full-width content area. Nothing is centred in a narrow column: tables use the width.
- Content has a 24px gutter (16px under 768px) and sits on a 12-column grid with 16px gaps.
  A page is a title row (title left, actions right) followed by sections separated by a
  `--line-strong` rule and 24px of space.
- Wide tables scroll horizontally inside their own frame; the page never scrolls sideways.

## Components

| Component | Notes |
|---|---|
| Button | Primary (accent fill), secondary (outlined), quiet (text only), danger. Sizes sm 24px, md 28px. Busy state keeps the width and shows a spinner glyph. |
| Field | Label above, control, then help or error text below; the error replaces the help and is announced. |
| TextInput / NumberInput | Optional unit suffix; numbers in Plex Mono, right-aligned. |
| Select | The native select, restyled, so keyboard and screen readers work as everywhere else. |
| DateRange | Two date inputs and preset buttons (1M, 3M, 6M, 1Y, YTD). |
| SegmentedControl | A radio group drawn as joined buttons; arrow keys move the choice. |
| Tabs | WAI-ARIA tabs with arrow-key focus; the current tab is marked with a 2px accent rule. |
| Table | Sticky header, sortable columns (button in the header, `aria-sort`), right-aligned numbers, selectable rows, hairline row rules. |
| Dialog | The native `dialog` element in modal mode: focus is trapped and Escape closes it. |
| Toast | Polite live region at the bottom right; errors stay until dismissed, the rest leave after 5s. |
| StatusBadge | A run's state as an icon and a word; only running (accent) and failed (danger) carry colour. |
| Skeleton | Grey blocks in the shape of what is loading. |
| Money / Figure | Formats a value with its sign and fixed decimals; money takes the profit or loss colour. |
| TerminalStatus | In the top bar, fed by the health endpoint: ready, busy with a run, open elsewhere, not found. |

Icons come from Phosphor, at one weight (regular) and one size per context (16px in controls,
14px in tables). An icon that carries meaning has a text label or an `aria-label`.

## States

Every screen designs four states before it is done:

- **Empty**: says what will be here and gives the one action that fills it.
- **Loading**: skeletons in the shape of the content; no spinner in the middle of a page.
- **Error**: says what happened, in the terminal's or engine's own words when there are some,
  and what to do next. Errors that belong to a field sit under the field.
- **Long content**: tables virtualise and keep their header; long names truncate with the full
  text in a tooltip; logs scroll inside their well.

## Rules that are easy to break

- No gradients, glass, glow, blobs, emoji, illustrations, hero sections or marketing copy.
- No colour outside the tokens; no profit or loss colour outside money.
- No em dashes in interface text; use a full stop, a comma or a colon.
- Focus is always visible: a 2px accent outline offset by 2px.
- Everything works from the keyboard, in both themes, at 200% zoom.
