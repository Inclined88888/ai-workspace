// Unit tests for the markdown rendering pipeline in src/markdown.ts: highlight.js syntax highlighting,
// the language label + copy button header, and the mermaid placeholder that MermaidBlock swaps for a
// live diagram. Run with Node's built-in runner: `npm test`. esbuild compiles the module; katex warns
// about quirks mode under Node but works.
import { test, before } from "node:test";
import assert from "node:assert/strict";
import { build } from "esbuild";
import { mkdirSync, writeFileSync } from "node:fs";
import { fileURLToPath, pathToFileURL } from "node:url";

let renderMarkdownHtml, MERMAID_RE;
before(async () => {
  const out = await build({
    entryPoints: [fileURLToPath(new URL("../src/markdown.ts", import.meta.url))],
    bundle: true, format: "esm", platform: "node", write: false,
    loader: { ".css": "empty" },
  });
  const dir = fileURLToPath(new URL("../node_modules/.cache/wb-tests/", import.meta.url));
  mkdirSync(dir, { recursive: true });
  const file = dir + "markdown.mjs";
  writeFileSync(file, out.outputFiles[0].text);
  ({ renderMarkdownHtml, MERMAID_RE } = await import(pathToFileURL(file).href));
});

test("fenced code keeps the language label and copy button", () => {
  const html = renderMarkdownHtml("```python\nprint(1)\n```");
  assert.match(html, /<div class="code-block">/);
  assert.match(html, /<div class="code-head"><span>python<\/span>/);
  assert.match(html, /<button type="button" class="code-copy" aria-label="Copy code">Copy<\/button>/);
});

test("known languages are highlighted with hljs spans", () => {
  const html = renderMarkdownHtml('```python\ndef f():\n    return "hi"\n```');
  assert.match(html, /<code class="hljs language-python">/);
  assert.match(html, /<span class="hljs-keyword">def<\/span>/);
  assert.match(html, /hljs-string/);
});

test("unknown languages render escaped plain text", () => {
  const html = renderMarkdownHtml("```nosuchlang\na < b & c\n```");
  assert.match(html, /<span>nosuchlang<\/span>/);
  assert.doesNotMatch(html, /hljs-keyword|hljs-string/, "no token spans for unknown languages");
  assert.match(html, /a &lt; b &amp; c/);
});

test("highlight markup keeps the raw code escaped around the spans", () => {
  const html = renderMarkdownHtml("```js\nconst x = 1 < 2\n```");
  assert.match(html, /hljs-keyword">const</);
  assert.match(html, /<\/span> &lt; /, "the < stays escaped between the highlighted tokens");
});

test("a fence with no language is labelled 'code'", () => {
  const html = renderMarkdownHtml("```\nplain\n```");
  assert.match(html, /<span>code<\/span>/);
  assert.match(html, /<code class="hljs">/);
});

test("no fence means no code-block at all", () => {
  const html = renderMarkdownHtml("just a paragraph with `inline` code");
  assert.doesNotMatch(html, /code-block/);
  assert.match(html, /<code>inline<\/code>/);
});

test("mermaid fences become a placeholder with the source escaped", () => {
  const html = renderMarkdownHtml("```mermaid\ngraph TD\n  A --> B\n```");
  assert.match(html, /<pre class="mermaid">graph TD\n  A --&gt; B\n?<\/pre>/);
  assert.doesNotMatch(html, /code-block/, "mermaid is not a plain code block");
  assert.ok(MERMAID_RE.test(html), "the split regex in util.tsx matches this placeholder");
});

test("an unclosed trailing mermaid fence streams as a code block, not a half-rendered diagram", () => {
  const html = renderMarkdownHtml("before\n\n```mermaid\ngraph TD\n  A --> B");
  assert.doesNotMatch(html, /class="mermaid"/, "no placeholder until the fence closes");
  assert.match(html, /code-block/, "it renders as a plain code block while streaming");
  assert.match(html, /A --&gt; B/, "source stays escaped");
  assert.match(html, /before/);
});

test("an unclosed code fence streams safely (no unescaped raw HTML in the output)", () => {
  const html = renderMarkdownHtml("before\n\n```js\nconst a = 1");
  assert.doesNotMatch(html, /<script>/);
  assert.match(html, /before/);
});

test("a mermaid block inside ordinary prose leaves the rest intact", () => {
  const html = renderMarkdownHtml("intro text\n\n```mermaid\ngraph LR\n  X --> Y\n```\n\noutro text");
  assert.match(html, /intro text/);
  assert.match(html, /outro text/);
  assert.match(html, /<pre class="mermaid">/);
});

test("hostile code content cannot inject markup", () => {
  const html = renderMarkdownHtml('```html\n<script>alert(1)</script>\n```');
  assert.doesNotMatch(html, /<script>/);
  assert.match(html, /&lt;/, "angle brackets are escaped");
  assert.ok(!/<code[^>]*>[^<]*<script/.test(html), "no live script tag inside the code element");
});

test("maths still renders alongside code blocks", () => {
  const html = renderMarkdownHtml("$x^2$\n\n```py\nx = 1\n```");
  assert.match(html, /katex/);
  assert.match(html, /hljs language-py/);
});
