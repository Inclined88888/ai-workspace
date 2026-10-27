# ROOKS brand

**Name:** ROOKS, always in capitals, in the interface, titles, onboarding and copy ("ROOKS drafted the email"). The assistant's default name is ROOKS. Never ROOK or AI Workspace.

**Tagline:** Your move. ROOKS delivers.

**Positioning:** a lightweight, self hosted AI workspace that acts as a dependable right hand: it listens, understands instructions, coordinates tools and agents, and gets work finished. Private by default, model agnostic, one light container.

## Mark

A geometric chess rook: three battlements, a tapered tower and a base, on a rounded square.

* `docs/rook-mark.svg` and `workbench/frontend/public/favicon.svg`: the full mark (white rook on the accent colour).
* `workbench/frontend/src/components/RookMark.tsx`: the rook alone, drawn in the current text colour, for use inside the app.

Keep it flat and single colour. No gradients, shadows, outlines or animation. Minimum size 16 px.

## Colour

| Token | Light | Dark | Use |
| --- | --- | --- | --- |
| Accent | `#3f4bd1` | `#5a66ea` | Mark tile, primary buttons, focus |
| Accent text | `#3340b4` | `#aeb6ff` | Links and accent text |
| Accent soft | `#eceefd` | `#1f2449` | Selected and highlighted backgrounds |

White text on the accent measures 6.7:1 in light mode and 4.6:1 in dark mode (WCAG AA for normal text). Neutrals are unchanged from the existing theme in `workbench/frontend/src/styles.css`.

## Type

The system UI font stack already used by the app. The wordmark is ROOKS in capitals, weight 750, letter spacing 0.14em.

## Suggested GitHub metadata

Set these yourself under the repository's About settings; nothing here changes them.

* **Description:** ROOKS: a lightweight, self hosted AI workspace with memory, skills, research, automations and budget controlled agent teams. One light container, optional Docker add ons.
* **Topics:** ai-agent, self-hosted, ai-assistant, llm, fastapi, mcp, docker, automation, multi-agent, open-source
