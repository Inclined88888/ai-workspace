// feat/team-lead: the Team pages stay on theme tokens, are routed, ask before sending, and draw the timeline right.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const read = (p) => readFileSync(new URL(p, import.meta.url), "utf8");

test("team styles use theme variables only and respect motion settings", () => {
  const css = read("../src/team/team.css").replace(/\/\*[\s\S]*?\*\//g, "");
  assert.doesNotMatch(css, /#[0-9a-f]{3,8}\b/i, "no hex colours");
  assert.doesNotMatch(css, /\b(rgb|rgba|hsl|hsla)\(/i, "no literal colours");
  assert.match(css, /prefers-reduced-motion: no-preference/);
  for (const m of css.matchAll(/animation:[^;]+;/g)) assert.ok(css.slice(0, m.index).lastIndexOf('data-motion="full"') > css.slice(0, m.index).lastIndexOf("}\n}"), "animation only under full motion");
});

test("Team is in the nav and routed, including the person page", () => {
  const app = read("../src/App.tsx");
  assert.ok(app.includes('{ to: "/team"'));
  assert.ok(app.includes('<Route path="/team"') && app.includes('<Route path="/team/people/:id"'));
});

test("every view is there and nothing is sent without a button press", () => {
  const src = read("../src/team/Team.tsx");
  for (const v of ["Board", "Workload", "Timeline", "TodayPlan", "PersonPage", "SuggestionCard", "MessageCard", "ReplyCard", "SettingsTab"]) assert.match(src, new RegExp(`function ${v}\\b`), v);
  // outbox approve / plan send only from onClick handlers, never from effects
  const effects = [...src.matchAll(/useEffect\(\(\) => \{([^]*?)\}, \[/g)].map((m) => m[1]).join("\n");
  assert.doesNotMatch(effects, /approve|plan\/send|outbox/);
  assert.ok(src.includes('feature="team_tasks"'), "upgrade card on Free");
  assert.ok(read("../src/v02/shared.tsx").includes("team_tasks:"), "upgrade text");
  assert.match(src, /privacy/i);
});

test("timeline bars: start to due, clipped to the window", async () => {
  let mod;
  try { mod = await import("../src/team/types.ts"); } catch { return; } // needs Node type stripping (22.6+)
  const { bar } = mod;
  const t = { start: "2026-11-02", due: "2026-11-05", created_at: "2026-11-01T00:00:00", assigned_at: null, estimate_hours: 2 };
  assert.deepEqual(bar(t, "2026-10-31", 21), { left: 2, width: 4 });
  assert.deepEqual(bar({ ...t, start: null, assigned_at: "2026-10-20T09:00:00" }, "2026-10-31", 21), { left: 0, width: 6 });
  assert.equal(bar({ ...t, start: "2026-12-20", due: "2026-12-24" }, "2026-10-31", 21), null);
  assert.deepEqual(bar({ ...t, due: null, start: "2026-11-02", estimate_hours: 20 }, "2026-10-31", 21), { left: 2, width: 3 });
});
