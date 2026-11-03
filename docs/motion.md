# Motion

One motion system for the desktop app and the phone. Bright, quiet, editorial: things settle rather than bounce,
and nothing moves without a reason (work is happening, something landed, you need to decide).

## Rules

- **Transform and opacity only** (plus `stroke-dashoffset` / `background-position` on tiny elements). Nothing animates
  layout, so it holds 60 fps on a laptop running an agent. A unit test fails the build if a keyframe animates anything else.
- **Three levels.** Full · **Calm** (Appearance › Motion, same moments as gentle fades, no travel, no overshoot) · Off.
  The OS "reduce motion" setting always wins. On the phone: Settings › Motion, and iOS/Android Reduce Motion.
- **Per-theme personality** from tokens (`workbench/frontend/src/motion/tokens.css`, `mobile/src/lib/motion.ts`):
  Sunshine warm and soft, Paper slower with ink-grey light, Cherry & Ink crisp with no overshoot, Neon quick with a lime
  glow, Dark coral, High contrast solid lines with no bounce.
- Springs are real spring physics (k/d) sampled into CSS `linear()` curves: `--spring-gentle` (glide),
  `--spring-settle` (~6% overshoot, cards and ticks), `--spring-snappy` (nodes, pills).

## The moments

| Moment | Where | How |
|---|---|---|
| Thinking shimmer | Chat, while the reply is coming | The assistant's name shimmers; after 6 s it shows the time and the tool in use; cut at the first word |
| Word-by-word reply | Chat | Only the newest ~10 words fade in (opacity), with one caret; the rest is plain selectable text |
| Self-drawing timeline | Chat steps, Computer, Browser | Each step draws its own line segment, its node settles; the live step breathes |
| Edge glow | Whole window, while Rooks Desk drives the computer | A conic gradient spun by `transform` inside a static masked band, plus a pill with Stop |
| Agent cursor | Computer viewer | A drawn cursor glides (spring) to each click/move parsed from the step results, leaves a trail, ripples on click; the real mouse never moves |
| Live screen | Computer, Browser | Frames cross-fade, a reading line sweeps while it looks |
| Page turns | Every page | View Transitions: the nav pill slides between items, the title and composer morph |
| Spring approvals | Chat, phone | Approval cards rise and settle, then lift away when answered |
| Done tick | Computer, Browser, phone approvals and chat | Ring draws, tick strokes in and pops; success haptic on the phone |
| Voice orb | Chat composer (desktop and phone) | Breathes while listening and swells with your voice level |
| Skeletons | Lists while loading | A soft light passes over placeholders |

See them all at **Appearance › See every animation** (`/appearance/motion`). Recordings: `screenshots/motion/`.
