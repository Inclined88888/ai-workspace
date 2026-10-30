import { parseInline, parseMarkdown } from '../markdown';

describe('markdown', () => {
  it('reads headings, lists, quotes and rules', () => {
    const b = parseMarkdown('# Title\n\nSome **bold** text\nwraps here.\n\n- one\n- two\n1. first\n> quoted\n\n---');
    expect(b.map((x) => x.t)).toEqual(['h', 'p', 'li', 'li', 'li', 'quote', 'hr']);
    expect(b[1]).toEqual({ t: 'p', inl: [{ t: 'text', v: 'Some ' }, { t: 'strong', v: 'bold' }, { t: 'text', v: ' text wraps here.' }] });
    expect(b[4]).toMatchObject({ t: 'li', ordered: true, n: 1 });
  });
  it('keeps code blocks verbatim, and an unclosed fence (still streaming) as code', () => {
    const closed = parseMarkdown('```ts\nconst a = 1;\n\n**not bold**\n```\nafter');
    expect(closed[0]).toEqual({ t: 'code', lang: 'ts', code: 'const a = 1;\n\n**not bold**', open: false });
    expect(closed[1].t).toBe('p');
    const open = parseMarkdown('Here:\n```py\nprint(1)');
    expect(open[1]).toEqual({ t: 'code', lang: 'py', code: 'print(1)', open: true });
  });
  it('parses inline code, emphasis and only http(s) links', () => {
    expect(parseInline('run `npm test` *now* see [docs](https://x.dev) or [bad](javascript:alert(1))')).toEqual([
      { t: 'text', v: 'run ' },
      { t: 'code', v: 'npm test' },
      { t: 'text', v: ' ' },
      { t: 'em', v: 'now' },
      { t: 'text', v: ' see ' },
      { t: 'link', v: 'docs', href: 'https://x.dev' },
      { t: 'text', v: ' or [bad](javascript:alert(1))' },
    ]);
  });
  it('survives empty input', () => {
    expect(parseMarkdown('')).toEqual([]);
  });
});
