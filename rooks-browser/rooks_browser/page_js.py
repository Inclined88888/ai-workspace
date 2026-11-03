"""JavaScript that runs in the page. Kept apart so it reads as one piece."""

# Numbers every interactive element on the page (data-rb-id) and describes it the way an accessibility tree would:
# role, accessible name, state. Text models act on these numbers; vision models get the same list beside a screenshot.
SNAPSHOT = r"""
(maxElements) => {
  const old = document.querySelectorAll('[data-rb-id]');
  old.forEach(e => e.removeAttribute('data-rb-id'));
  const sel = 'a[href], button, input:not([type=hidden]), select, textarea, summary, [role=button], [role=link], ' +
    '[role=checkbox], [role=radio], [role=tab], [role=menuitem], [role=option], [role=switch], [role=combobox], ' +
    '[role=searchbox], [role=textbox], [contenteditable=""], [contenteditable=true], [onclick], [tabindex]:not([tabindex="-1"])';
  const vw = window.innerWidth, vh = window.innerHeight;
  const clean = s => (s || '').replace(/\s+/g, ' ').trim();
  const labelOf = el => {
    if (el.getAttribute('aria-label')) return el.getAttribute('aria-label');
    const by = el.getAttribute('aria-labelledby');
    if (by) { const t = by.split(/\s+/).map(i => document.getElementById(i)).filter(Boolean).map(e => e.innerText).join(' '); if (t) return t; }
    if (el.id) { const l = document.querySelector(`label[for="${CSS.escape(el.id)}"]`); if (l) return l.innerText; }
    const wrap = el.closest('label'); if (wrap && wrap !== el) return wrap.innerText;
    return '';
  };
  const roleOf = el => {
    const r = el.getAttribute('role'); if (r) return r;
    const t = el.tagName.toLowerCase();
    if (t === 'a') return 'link';
    if (t === 'button' || t === 'summary') return 'button';
    if (t === 'select') return 'combobox';
    if (t === 'textarea') return 'textbox';
    if (t === 'input') {
      const ty = (el.type || 'text').toLowerCase();
      if (['submit', 'button', 'reset', 'image'].includes(ty)) return 'button';
      if (ty === 'checkbox' || ty === 'radio') return ty;
      if (ty === 'search') return 'searchbox';
      return 'textbox';
    }
    if (el.isContentEditable) return 'textbox';
    return 'generic';
  };
  const out = [];
  let n = 0;
  for (const el of document.querySelectorAll(sel)) {
    if (out.length >= maxElements) break;
    const st = getComputedStyle(el);
    if (st.visibility === 'hidden' || st.display === 'none' || el.disabled && el.tagName !== 'BUTTON') continue;
    const r = el.getBoundingClientRect();
    if (r.width < 2 || r.height < 2) continue;
    const tag = el.tagName.toLowerCase();
    const type = (el.getAttribute('type') || '').toLowerCase();
    const form = el.form || el.closest('form');
    const text = clean(el.innerText || el.value && ['submit', 'button'].includes(type) && el.value || '');
    const name = clean(labelOf(el) || text || el.getAttribute('placeholder') || el.getAttribute('title') ||
      el.getAttribute('alt') || (el.querySelector && el.querySelector('img[alt]') ? el.querySelector('img[alt]').alt : '') ||
      el.getAttribute('name') || '').slice(0, 120);
    const id = ++n;
    el.setAttribute('data-rb-id', String(id));
    const d = { id, tag, role: roleOf(el), name, type,
      in_view: r.bottom > 0 && r.top < vh && r.right > 0 && r.left < vw,
      in_form: !!form, form_has_password: !!(form && form.querySelector('input[type=password]')),
      form_action: form ? clean(form.getAttribute('action') || '') : '',
      is_submit: tag === 'button' ? (type === '' || type === 'submit') && !!form : (tag === 'input' && ['submit', 'image'].includes(type)) };
    if (tag === 'a') { d.href = el.href; if (el.hasAttribute('download')) d.download = true; }
    if (type !== 'password' && ['input', 'textarea'].includes(tag) && !['submit', 'button', 'checkbox', 'radio'].includes(type)) d.value = clean(el.value).slice(0, 80);
    if (type === 'checkbox' || type === 'radio') d.checked = el.checked;
    if (tag === 'select') d.options = Array.from(el.options).slice(0, 15).map(o => clean(o.text));
    if (el.disabled) d.disabled = true;
    out.push(d);
  }
  let body = document.body ? document.body.innerText : '';
  const banner = document.getElementById('__rooks_banner');  // our own overlay is not page content
  if (banner && banner.innerText) body = body.replace(banner.innerText, '');
  return { url: location.href, title: document.title, elements: out, text: body.replace(/\n{3,}/g, '\n\n'),
    scroll_y: Math.round(window.scrollY), page_height: Math.round(document.documentElement.scrollHeight), viewport_h: vh };
}
"""

# A small banner and outline so a person watching (headed mode) sees every action before it happens.
OVERLAY = r"""
(() => {
  if (window.__rooks) return;
  window.__rooks = {
    show(text, id) {
      let b = document.getElementById('__rooks_banner');
      if (!b) {
        b = document.createElement('div'); b.id = '__rooks_banner';
        b.style.cssText = 'position:fixed;z-index:2147483647;left:50%;top:10px;transform:translateX(-50%);max-width:80%;' +
          'background:#fbf7ef;color:#1d1a16;border:1px solid #1d1a16;padding:7px 14px;font:13px/1.35 Georgia,serif;' +
          'box-shadow:3px 3px 0 #1d1a16;pointer-events:none;white-space:nowrap;overflow:hidden;text-overflow:ellipsis';
        (document.body || document.documentElement).appendChild(b);
      }
      b.textContent = 'Rooks Browser \u2014 ' + text;
      document.querySelectorAll('[data-rb-focus]').forEach(e => { e.style.outline = e.dataset.rbOutline || ''; e.removeAttribute('data-rb-focus'); });
      if (id) {
        const el = document.querySelector(`[data-rb-id="${id}"]`);
        if (el) { el.dataset.rbOutline = el.style.outline; el.setAttribute('data-rb-focus', '1'); el.style.outline = '3px solid #c2410c'; }
      }
    },
    hide() {
      const b = document.getElementById('__rooks_banner'); if (b) b.remove();
      document.querySelectorAll('[data-rb-focus]').forEach(e => { e.style.outline = e.dataset.rbOutline || ''; e.removeAttribute('data-rb-focus'); });
    },
  };
})();
"""
