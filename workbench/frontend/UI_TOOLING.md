# UI tooling

Stack: React 18 + Vite + TypeScript, plain CSS with theme variables (`src/styles.css`, `src/themes.css`), npm, exact version pins.
No Tailwind. Anything that needs Tailwind (shadcn/ui, DaisyUI, the ui-skills `baseline-ui` skill) is out until the owner decides to adopt it.

## Installed

| Tool | Category | License | Version | Import |
| --- | --- | --- | --- | --- |
| Radix UI Radio Group | Headless accessible primitive | MIT | 1.5.1 | `import * as RadioGroup from "@radix-ui/react-radio-group"` |
| Lucide | Icon set (already present) | ISC | 0.511.0 | `import { Play } from "lucide-react"` |
| ibelick/ui-skills (2 skills) | Agent skills | MIT | main @ ccf0dc5b00e9 | `.hermes/skills/` at repo root |

Radix primitives ship no styles. Style them with existing classes; they render real `button[role=radio][aria-checked]`
and `data-state="checked|unchecked"`, so selectors like `.ap-seg button[aria-checked="true"]` keep working.
Add other primitives one package at a time (`npm install --save-exact @radix-ui/react-<name>`), never the `radix-ui` meta package.

## Example

`src/pages/Motion.tsx`, the Full / Calm / Off motion picker:

```tsx
<RadioGroup.Root className="ap-seg" aria-label="Motion" orientation="horizontal" loop value={a.motion}
  onValueChange={(v) => set({ motion: v as typeof a.motion })}>
  <RadioGroup.Item value="full">Full</RadioGroup.Item>
</RadioGroup.Root>
```

Gain over the hand rolled version: one Tab stop for the group, arrow keys move between options (wrapping), Space selects.

## Agent skills (ibelick/ui-skills, MIT, license in `.hermes/skills/UI-SKILLS-LICENSE`)

- `fixing-accessibility`: `/fixing-accessibility <file>` audits keyboard, labels, focus, contrast.
- `fixing-motion-performance`: `/fixing-motion-performance <file>` audits animation cost. Fits `src/motion.css`.

Not copied: `baseline-ui` (mandates Tailwind and `motion/react`, conflicts with this stack). The rest of the registry is
reachable with `npx ui-skills list`.
