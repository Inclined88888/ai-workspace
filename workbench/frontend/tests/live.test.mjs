// Unit tests for live documents and the jobs board labels (src/v02/live.ts): `npm test`.
import { test } from "node:test";
import assert from "node:assert/strict";
import { build } from "esbuild";
import { mkdirSync, writeFileSync } from "node:fs";
import { fileURLToPath, pathToFileURL } from "node:url";

const out = await build({ entryPoints: [new URL("../src/v02/live.ts", import.meta.url).pathname], bundle: true, format: "esm", platform: "node", write: false });
const dir = fileURLToPath(new URL("../node_modules/.cache/wb-tests/", import.meta.url));
mkdirSync(dir, { recursive: true });
writeFileSync(dir + "live.mjs", out.outputFiles[0].text);
const t = await import(pathToFileURL(dir + "live.mjs").href);

const budget = { title: "Budget", columns: ["Item", "Qty", "Price", "Total"],
  rows: [["Paper", 2, 3.5, "=B2*C2"], ["Ink", "1", 12, "=B3*C3"], ["Sum", "", "", "=SUM(D2:D3)"], ["Avg", "", "", "=ROUND(AVERAGE(D2:D3),1)"]] };

test("formulas compute like a spreadsheet (row 1 is the header)", () => {
  const v = t.evaluate(budget);
  assert.equal(v[0][3], 7);
  assert.equal(v[1][3], 12);
  assert.equal(v[2][3], 19);
  assert.equal(v[3][3], 9.5);
});

test("operators, functions, text and errors", () => {
  const s = (f) => t.evaluate({ title: "", columns: ["A", "B"], rows: [[5, f], [10, ""]] })[0][1];
  assert.equal(s("=A2+A3*2"), 25);
  assert.equal(s("=(A2+A3)/3"), 5);
  assert.equal(s("=2^3-1"), 7);
  assert.equal(s("=-A2+1"), -4);
  assert.equal(s("=50%*A3"), 5);
  assert.equal(s('=IF(A2>3,"big","small")'), "big");
  assert.equal(s('=IF(A2>30,"big","small")'), "small");
  assert.equal(s('="Total: "&A3'), "Total: 10");
  assert.equal(s("=MAX(A2:A3)-MIN(A2:A3)"), 5);
  assert.equal(s("=COUNT(A1:A3)"), 2);
  assert.equal(s("=A2/0"), "#DIV/0!");
  assert.equal(s("=NOPE(1)"), "#NAME?");
  assert.equal(s("=B2"), "#CYCLE");
  assert.equal(s("=1+"), "#ERR");
});

test("replies split into Markdown and live blocks; broken blocks stay as code", () => {
  const text = "Here you go:\n\n```sheet\n" + JSON.stringify(budget) + "\n```\n\nAnd a chart:\n```chart\n" +
    JSON.stringify({ title: "Sales", labels: ["Jan", "Feb"], series: [{ name: "2026", values: [1, "2"] }] }) +
    "\n```\n```sheet\nnot json\n```\n```doc\n# Plan\n\nHello\n```";
  const p = t.splitLive(text);
  assert.deepEqual(p.map((x) => x.kind), ["md", "sheet", "md", "chart", "md", "doc"]);
  assert.equal(p[1].index, 0);
  assert.equal(p[3].data.type, "bar");
  assert.deepEqual(p[3].data.series[0].values, [1, 2]);
  assert.match(p[4].text, /not json/);
  assert.equal(p[5].data, "# Plan\n\nHello");
  assert.deepEqual(t.splitLive("just text"), [{ kind: "md", text: "just text" }]);
});

test("a sheet without columns uses its first row as the header", () => {
  const s = t.normaliseSheet({ rows: [["Name", "Age"], ["Ann", 3, "extra"]] });
  assert.deepEqual(s.columns, ["Name", "Age"]);
  assert.deepEqual(s.rows, [["Ann", 3]]);
});

test("csv exports use computed values", () => {
  const csv = t.sheetCsv(budget);
  assert.equal(csv.split("\r\n")[0], "Item,Qty,Price,Total");
  assert.match(csv, /Sum,,,19/);
  assert.doesNotMatch(csv, /=SUM/);
  assert.equal(t.chartCsv({ title: "", type: "bar", labels: ["a, b"], series: [{ name: "S", values: [3] }] }), 'Label,S\r\n"a, b",3\r\n');
});

test("chart helpers", () => {
  assert.deepEqual(t.niceScale(9), [0, 2.5, 5, 7.5, 10]);
  assert.deepEqual(t.niceScale(0), [0, 1]);
  const s = t.pieSlices([1, 1, 2]);
  assert.deepEqual(s.map((x) => x.share), [0.25, 0.25, 0.5]);
  assert.match(t.pieSlices([5])[0].d, /^M 80 0 A/);
  assert.equal(t.colName(0), "A");
  assert.equal(t.colName(27), "AB");
});

test("jobs read in plain English with the right buttons", () => {
  assert.equal(t.jobLabel("waiting", "approval"), "Needs your OK");
  assert.equal(t.jobLabel("waiting", "start"), "Asks before it starts");
  assert.equal(t.jobLabel("queued"), "In line");
  assert.deepEqual(t.jobControls("running"), ["pause", "stop"]);
  assert.deepEqual(t.jobControls("waiting", "start"), ["start", "deny"]);
  assert.deepEqual(t.jobControls("done"), ["retry"]);
});
