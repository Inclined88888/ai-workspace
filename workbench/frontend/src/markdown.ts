// The pure markdown → HTML rendering pipeline: GFM + KaTeX maths + highlight.js code blocks.
// Split from util.tsx so it can be unit-tested under Node without a DOM; the Markdown component
// sanitizes this output with DOMPurify before it ever touches the page.
import hljs from "highlight.js/lib/common";
import katex from "katex";
import { marked } from "marked";

marked.setOptions({ gfm: true, breaks: true });

export const esc = (s: string) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
export const unesc = (s: string) => s.replace(/&quot;/g, '"').replace(/&gt;/g, ">").replace(/&lt;/g, "<").replace(/&amp;/g, "&");

// Syntax highlighting via highlight.js (core + common grammars). Unknown languages stay plain.
function highlightCode(code: string, lang: string): string {
  if (lang && hljs.getLanguage(lang)) {
    try {
      return hljs.highlight(code, { language: lang, ignoreIllegals: true }).value;
    } catch { /* fall through to plain escaping */ }
  }
  return esc(code);
}

// Fenced code gets a header with the language and a copy button (handled by onCopyClick in util.tsx;
// no inline JS). ```mermaid fences become a placeholder <pre class="mermaid">; MermaidBlock renders
// it to SVG after sanitize.
// Streaming: an unclosed trailing fence still reaches the code renderer, so a half-typed ```mermaid block
// must stay a plain (highlighted) code block until the fence closes — otherwise a partial diagram source
// would flicker as a fallback. Marked strips the closing fence's trailing newline from `text`, so tell
// them apart by the raw source: a complete block's raw ends with the fence line.
const CLOSED_FENCE = /(?:^|\n)[ \t]*(`{3,}|~{3,})[ \t]*$/;
marked.use({
  renderer: {
    code({ text, lang, raw = "" }) {
      const language = (lang || "").split(/\s/)[0].toLowerCase();
      if (language === "mermaid" && CLOSED_FENCE.test(raw)) return `<pre class="mermaid">${esc(text)}</pre>`;
      return `<div class="code-block"><div class="code-head"><span>${esc(language || "code")}</span><button type="button" class="code-copy" aria-label="Copy code">Copy</button></div><pre><code class="hljs${language ? ` language-${esc(language)}` : ""}">${highlightCode(text, language === "mermaid" ? "" : language)}</code></pre></div>`;
    },
  },
});

// Maths: $$...$$ blocks and $...$ inline, rendered with KaTeX. "$5 and $10" stays text (no space inside the dollars,
// no digit right after the closing one).
const tex = (src: string, displayMode: boolean) => {
  try {
    return katex.renderToString(src, { displayMode, throwOnError: false, output: "htmlAndMathml", strict: "ignore" });
  } catch {
    return `<code>${esc(src)}</code>`;
  }
};
marked.use({
  extensions: [
    {
      name: "mathBlock", level: "block",
      start: (src: string) => src.match(/\$\$|\\\[/)?.index,
      tokenizer(src: string) {
        const m = /^\$\$([\s\S]+?)\$\$|^\\\[([\s\S]+?)\\\]/.exec(src);
        return m ? { type: "mathBlock", raw: m[0], text: (m[1] ?? m[2]).trim() } : undefined;
      },
      renderer: (t) => `<div class="math-block">${tex((t as unknown as { text: string }).text, true)}</div>`,
    },
    {
      name: "mathInline", level: "inline",
      start: (src: string) => src.match(/\$|\\\(/)?.index,
      tokenizer(src: string) {
        const m = /^\$(?!\s)((?:\\.|[^$\\\n])+?)(?<!\s)\$(?!\d)|^\\\((.+?)\\\)/.exec(src);
        return m ? { type: "mathInline", raw: m[0], text: m[1] ?? m[2] } : undefined;
      },
      renderer: (t) => tex((t as unknown as { text: string }).text, false),
    },
  ],
});

// Splits sanitized HTML on the mermaid placeholders so those blocks become live <MermaidBlock/> components.
// Marked keeps the raw text of an unclosed trailing fence out of the output until the fence closes, so a
// half-typed ```mermaid block mid-stream renders nothing until it is complete — then the diagram appears.
export const MERMAID_RE = /<pre class="mermaid">([\s\S]*?)<\/pre>/;

/** Markdown source → raw (unsanitized) HTML. Callers must sanitize before injecting into the DOM. */
export function renderMarkdownHtml(src: string): string {
  return marked.parse(src || "", { async: false }) as string;
}
