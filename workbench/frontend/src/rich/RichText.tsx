import DOMPurify from "dompurify";
import { marked } from "marked";
import { useEffect, useMemo, useRef } from "react";

/* Markdown for answers: sanitised by DOMPurify first, then improved by working on the resulting DOM with text nodes
   only (never by building HTML strings from the model's text). Code blocks get a copy button and light syntax
   colouring, tables scroll inside their own box, external links open safely in a new tab, and [n] citations link to
   the sources this turn actually read (they stay plain text when there is no such source). Answers show no images or
   media: an image the model writes would load by itself, and its address could carry conversation text to anyone. */

marked.setOptions({ gfm: true, breaks: true });

const KEYWORDS = new Set(("const let var function return if else for while do switch case break continue new class extends import from export default " +
  "async await try catch finally throw typeof instanceof in of def lambda pass yield with as elif not and or is None True False self print " +
  "select from where join left right inner outer group by order having insert into values update set delete create table limit and or not null " +
  "true false null undefined public private static void int string bool fn let mut impl struct enum match use pub echo then fi done esac").split(" "));
const TOKEN = /(\/\/[^\n]*|#[^\n]*|\/\*[\s\S]*?\*\/|--[^\n]*)|("(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|`(?:\\.|[^`\\])*`)|(\b\d+(?:\.\d+)?\b)|([A-Za-z_][\w]*)/g;

function highlight(code: HTMLElement) {
  const lang = (code.className.match(/language-(\w+)/)?.[1] ?? "").toLowerCase();
  if (!lang || ["text", "txt", "plain", "markdown", "md"].includes(lang)) return;
  const src = code.textContent ?? "";
  if (src.length > 20000) return;
  const hashComments = ["py", "python", "sh", "bash", "shell", "zsh", "yaml", "yml", "toml", "rb", "ruby", "r"].includes(lang);
  const dashComments = ["sql"].includes(lang);
  const frag = document.createDocumentFragment();
  let last = 0;
  for (const m of src.matchAll(TOKEN)) {
    const [text, comment, str, num, word] = m;
    let cls = "";
    if (comment) {
      if (comment.startsWith("#") && !hashComments) cls = "";
      else if (comment.startsWith("--") && !dashComments) cls = "";
      else cls = "tok-comment";
    } else if (str) cls = "tok-string";
    else if (num) cls = "tok-number";
    else if (word && KEYWORDS.has(lang === "sql" ? word.toLowerCase() : word)) cls = "tok-keyword";
    if (!cls) continue;
    frag.append(document.createTextNode(src.slice(last, m.index)));
    const span = document.createElement("span");
    span.className = cls;
    span.textContent = text;
    frag.append(span);
    last = (m.index ?? 0) + text.length;
  }
  frag.append(document.createTextNode(src.slice(last)));
  code.replaceChildren(frag);
}

function enhance(root: HTMLElement, sources: Set<number>) {
  root.querySelectorAll("pre").forEach((pre) => {
    if (pre.parentElement?.classList.contains("code-block")) return;
    const code = pre.querySelector("code");
    if (code) highlight(code);
    const wrap = document.createElement("div");
    wrap.className = "code-block";
    const bar = document.createElement("div");
    bar.className = "code-bar";
    const lang = document.createElement("span");
    lang.textContent = (code?.className.match(/language-(\w+)/)?.[1] ?? "").toLowerCase();
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "btn ghost sm";
    btn.textContent = "Copy";
    btn.setAttribute("aria-label", "Copy code");
    btn.addEventListener("click", () => {
      navigator.clipboard?.writeText(code?.textContent ?? pre.textContent ?? "").then(() => {
        btn.textContent = "Copied";
        setTimeout(() => { btn.textContent = "Copy"; }, 1500);
      }).catch(() => undefined);
    });
    bar.append(lang, btn);
    pre.replaceWith(wrap);
    wrap.append(bar, pre);
  });
  root.querySelectorAll("table").forEach((t) => {
    if (t.parentElement?.classList.contains("table-wrap")) return;
    const wrap = document.createElement("div");
    wrap.className = "table-wrap";
    wrap.tabIndex = 0;
    wrap.setAttribute("role", "region");
    wrap.setAttribute("aria-label", "Table");
    t.classList.add("table", "rtable");
    t.replaceWith(wrap);
    wrap.append(t);
  });
  root.querySelectorAll("a[href]").forEach((a) => {
    const href = a.getAttribute("href") ?? "";
    if (/^https?:/i.test(href)) {
      a.setAttribute("target", "_blank");
      a.setAttribute("rel", "noopener noreferrer nofollow");
    } else if (!href.startsWith("/") && !href.startsWith("#")) {
      a.removeAttribute("href");  // anything else (mailto, data, relative paths the model made up) is plain text
    }
  });
  if (!sources.size) return;
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  const nodes: Text[] = [];
  while (walker.nextNode()) {
    const n = walker.currentNode as Text;
    if (/\[\d+(?:\s*,\s*\d+)*\]/.test(n.data) && !n.parentElement?.closest("code, pre, a")) nodes.push(n);
  }
  for (const n of nodes) {
    const frag = document.createDocumentFragment();
    let last = 0;
    for (const m of n.data.matchAll(/\[(\d+(?:\s*,\s*\d+)*)\]/g)) {
      const nums = m[1].split(/\s*,\s*/).map(Number);
      if (!nums.every((x) => sources.has(x))) continue;  // a citation to a source that was not read stays as typed
      frag.append(document.createTextNode(n.data.slice(last, m.index)));
      nums.forEach((x) => {
        const a = document.createElement("a");
        a.className = "cite";
        a.href = `#src-${x}`;
        a.textContent = String(x);
        a.setAttribute("aria-label", `Source ${x}`);
        a.addEventListener("click", (e) => {
          const el = document.getElementById(`src-${x}`);
          if (!el) return;
          e.preventDefault();
          el.scrollIntoView({ block: "nearest", behavior: "smooth" });
          const d = el.querySelector("details");
          if (d) d.open = true;
        });
        frag.append(a);
      });
      last = (m.index ?? 0) + m[0].length;
    }
    if (last === 0) continue;
    frag.append(document.createTextNode(n.data.slice(last)));
    n.replaceWith(frag);
  }
}

export default function RichText({ text, sources = [], className = "" }: { text: string; sources?: number[]; className?: string }) {
  const ref = useRef<HTMLDivElement>(null);
  const html = useMemo(() => DOMPurify.sanitize(marked.parse(text || "", { async: false }) as string,
    { FORBID_TAGS: ["style", "form", "input", "button", "iframe", "object", "embed", "img", "picture", "source", "video", "audio", "svg", "math"],
      FORBID_ATTR: ["style", "srcset"] }), [text]);
  const key = sources.join(",");
  useEffect(() => { if (ref.current) enhance(ref.current, new Set(sources)); }, [html, key]); // eslint-disable-line react-hooks/exhaustive-deps
  return <div ref={ref} className={`prose rich ${className}`} dangerouslySetInnerHTML={{ __html: html }} />;
}
