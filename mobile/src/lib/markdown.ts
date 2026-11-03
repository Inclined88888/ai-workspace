/**
 * A small Markdown reader for chat replies: enough for what models write (headings, paragraphs, lists, quotes,
 * fenced code, rules; bold, italic, inline code, links). Pure and tested; the renderer lives in components/.
 * Unclosed fences render as code, so a reply that is still streaming never flickers into raw backticks.
 */

export type Inline =
  | { t: 'text'; v: string }
  | { t: 'strong'; v: string }
  | { t: 'em'; v: string }
  | { t: 'code'; v: string }
  | { t: 'link'; v: string; href: string };

export type Block =
  | { t: 'p'; inl: Inline[] }
  | { t: 'h'; level: 1 | 2 | 3; inl: Inline[] }
  | { t: 'li'; ordered: boolean; n: number; inl: Inline[]; depth: number }
  | { t: 'quote'; inl: Inline[] }
  | { t: 'code'; lang: string; code: string; open: boolean }
  | { t: 'hr' };

const INLINE = /(`[^`]+`)|(\*\*[^*]+\*\*|__[^_]+__)|(\*[^*\s][^*]*\*|_[^_\s][^_]*_)|(\[[^\]]+\]\((https?:\/\/[^)\s]+)\))/g;

export function parseInline(s: string): Inline[] {
  const out: Inline[] = [];
  let last = 0;
  for (const m of s.matchAll(INLINE)) {
    const i = m.index ?? 0;
    if (i > last) out.push({ t: 'text', v: s.slice(last, i) });
    const tok = m[0];
    if (m[1]) out.push({ t: 'code', v: tok.slice(1, -1) });
    else if (m[2]) out.push({ t: 'strong', v: tok.slice(2, -2) });
    else if (m[3]) out.push({ t: 'em', v: tok.slice(1, -1) });
    else if (m[4]) out.push({ t: 'link', v: tok.slice(1, tok.indexOf('](')), href: m[5] });
    last = i + tok.length;
  }
  if (last < s.length) out.push({ t: 'text', v: s.slice(last) });
  return out.length ? out : [{ t: 'text', v: '' }];
}

export function parseMarkdown(src: string): Block[] {
  const lines = (src || '').replace(/\r\n/g, '\n').split('\n');
  const blocks: Block[] = [];
  let para: string[] = [];
  const flush = () => {
    if (para.length) blocks.push({ t: 'p', inl: parseInline(para.join(' ')) });
    para = [];
  };
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    const fence = /^\s*(```|~~~)\s*([\w+#.-]*)\s*$/.exec(line);
    if (fence) {
      flush();
      const body: string[] = [];
      let j = i + 1;
      while (j < lines.length && !new RegExp(`^\\s*${fence[1]}\\s*$`).test(lines[j])) body.push(lines[j++]);
      blocks.push({ t: 'code', lang: fence[2] || '', code: body.join('\n'), open: j >= lines.length });
      i = j;
      continue;
    }
    if (!line.trim()) {
      flush();
      continue;
    }
    const h = /^(#{1,6})\s+(.*)$/.exec(line);
    if (h) {
      flush();
      blocks.push({ t: 'h', level: Math.min(h[1].length, 3) as 1 | 2 | 3, inl: parseInline(h[2].replace(/\s+#+\s*$/, '')) });
      continue;
    }
    if (/^\s*([-*_])(\s*\1){2,}\s*$/.test(line)) {
      flush();
      blocks.push({ t: 'hr' });
      continue;
    }
    const li = /^(\s*)([-*+]|(\d+)[.)])\s+(.*)$/.exec(line);
    if (li) {
      flush();
      blocks.push({ t: 'li', ordered: !!li[3], n: li[3] ? Number(li[3]) : 0, inl: parseInline(li[4]), depth: Math.min(Math.floor(li[1].length / 2), 3) });
      continue;
    }
    const q = /^>\s?(.*)$/.exec(line);
    if (q) {
      flush();
      blocks.push({ t: 'quote', inl: parseInline(q[1]) });
      continue;
    }
    para.push(line.trim());
  }
  flush();
  return blocks;
}
