/**
 * The floating ask bar (feat/v02-anywhere-voice; Free). Opened over any app by the global shortcut (Option+Space on a
 * Mac, Alt+Space on Windows). It arrives with the text you had selected (or what you'd copied) and, if switched on, a
 * screenshot of the window. Ask about it or act on it, then paste the answer straight back.
 */
import { ArrowUp, Camera, ClipboardPaste, Copy, CornerDownLeft, FileText, Loader2, MessageSquare, X } from "lucide-react";
import { FormEvent, useCallback, useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, stream } from "../api";
import { inDesktopShell, invoke } from "../desktop";
import { Markdown, useToast } from "../util";
import { DictateButton } from "../voice/DictateButton";
import { handoffDraft } from "../voice/logic";
import "../voice/voice.css";

type Win = { id?: string | number; title?: string; app?: string } | null;
type Ctx = { text: string; source: "selection" | "clipboard" | "none"; window: Win; image: string };
type Turn = { role: "user" | "assistant"; content: string };
type Settings = { grab_selection: boolean; screenshot: boolean; shortcut: string; desk: boolean; desktop: boolean };

const QUICK: { id: string; label: string; needsText?: boolean }[] = [
  { id: "explain", label: "Explain" },
  { id: "summarise", label: "Summarise", needsText: true },
  { id: "reply", label: "Write a reply", needsText: true },
  { id: "fix", label: "Fix writing", needsText: true },
  { id: "translate", label: "Translate", needsText: true },
  { id: "next", label: "What next?" },
];

const EMPTY: Ctx = { text: "", source: "none", window: null, image: "" };

export default function AskBar() {
  const [ctx, setCtx] = useState<Ctx>(EMPTY);
  const [showText, setShowText] = useState(false);
  const [q, setQ] = useState("");
  const [history, setHistory] = useState<Turn[]>([]);
  const [answer, setAnswer] = useState("");
  const [asked, setAsked] = useState("");
  const [busy, setBusy] = useState(false);
  const [settings, setSettings] = useState<Settings | null>(null);
  const abort = useRef<AbortController | null>(null);
  const input = useRef<HTMLTextAreaElement>(null);
  const toast = useToast();
  const navigate = useNavigate();
  const desktop = inDesktopShell();

  const reset = useCallback(() => {
    abort.current?.abort();
    setAnswer(""); setAsked(""); setHistory([]); setQ(""); setShowText(false); setBusy(false);
    api<Ctx>("/anywhere/context").then((c) => setCtx(c.source === "none" && !c.image ? EMPTY : c)).catch(() => setCtx(EMPTY));
    setTimeout(() => input.current?.focus(), 30);
  }, []);

  useEffect(() => {
    document.title = "Ask";
    document.documentElement.classList.add("askbar-doc");
    reset();
    api<Settings>("/anywhere/settings").then(setSettings).catch(() => undefined);
    // The desktop shell calls this each time the shortcut shows the (already open) ask bar again
    (window as unknown as { __askBarOpen?: () => void }).__askBarOpen = reset;
    return () => document.documentElement.classList.remove("askbar-doc");
  }, [reset]);

  const close = useCallback((restore = false) => {
    abort.current?.abort();
    if (desktop) invoke("hide_ask_bar", { restoreFocus: restore }).catch(() => undefined);
    else navigate("/");
  }, [desktop, navigate]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") { e.preventDefault(); close(true); } };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [close]);

  const ask = async (quick = "", e?: FormEvent) => {
    e?.preventDefault();
    const question = q.trim();
    if (busy || (!question && !quick)) return;
    const label = [QUICK.find((x) => x.id === quick)?.label, question].filter(Boolean).join(": ");
    const prior = answer ? [...history, { role: "user" as const, content: asked }, { role: "assistant" as const, content: answer }] : history;
    setHistory(prior); setAsked(label); setAnswer(""); setQ(""); setBusy(true);
    abort.current = new AbortController();
    let out = "";
    await stream("/anywhere/ask", { question, quick, text: prior.length ? "" : ctx.text, image: prior.length ? "" : ctx.image, window: ctx.window, history: prior }, (ev) => {
      if (ev.delta) { out += ev.delta; setAnswer(out); }
      if (ev.error) toast.err(String(ev.error));
    }, abort.current.signal).catch((err) => { if ((err as Error).name !== "AbortError") toast.err(err); });
    setBusy(false);
  };

  const paste = async () => {
    if (!answer) return;
    try {
      if (settings?.desk && desktop) {
        // Rooks Desk types it into the app you came from: get out of the way first
        await invoke("hide_ask_bar", { restoreFocus: true });
        await new Promise((r) => setTimeout(r, 250));
        const r = await api<{ pasted?: boolean; detail?: string }>("/anywhere/paste", { body: { text: answer, window: ctx.window } });
        if (!r.pasted) await invoke("show_main_window", {}).catch(() => undefined);
        return;
      }
      const r = await api<{ copied: boolean; pasted: boolean; detail: string }>("/anywhere/paste", { body: { text: answer, window: ctx.window } });
      if (!r.copied) await navigator.clipboard.writeText(answer);
      toast.ok(r.pasted ? "Pasted." : `Copied. Press ${/Mac/.test(navigator.platform) ? "⌘V" : "Ctrl+V"} in your app to paste.`);
      setTimeout(() => close(true), 1400);
    } catch (e) { toast.err(e); }
  };

  const copy = async () => {
    try { await navigator.clipboard.writeText(answer); toast.ok("Copied"); } catch { toast.err("Couldn't copy. Select the text and copy it."); }
  };

  const toChat = () => {
    const draft = handoffDraft(asked || q, ctx.text, answer);
    if (desktop) invoke("show_main_window", { draft }).catch((e) => toast.err(e));
    else navigate("/assistant", { state: { draft } });
  };

  const useClipboard = async () => {
    try { setCtx(await api<Ctx>("/anywhere/grab", { body: { screenshot: false } })); } catch (e) { toast.err(e); }
  };

  const toggleShot = async () => {
    const next = !settings?.screenshot;
    try {
      setSettings(await api<Settings>("/anywhere/settings", { method: "PUT", body: { screenshot: next } }));
      toast.ok(next ? "Next time, I'll include a picture of the window." : "No screenshots from now on.");
    } catch (e) { toast.err(e); }
  };

  const from = ctx.window?.app || ctx.window?.title || "";
  const hasText = !!ctx.text.trim();
  return (
    <div className="askbar" role="dialog" aria-label="Ask about what's on screen">
      <div className="askbar-drag" data-tauri-drag-region>
        <span className="askbar-title" data-tauri-drag-region>Ask</span>
        <span className="faint small" data-tauri-drag-region>{from ? `over ${from}` : ""}</span>
        <div className="spacer" data-tauri-drag-region />
        <button className="btn ghost sm icon" onClick={() => close(true)} aria-label="Close (Esc)" title="Close (Esc)"><X size={15} /></button>
      </div>

      <div className="askbar-context">
        {hasText ? (
          <button type="button" className="chip" onClick={() => setShowText((s) => !s)} aria-expanded={showText}>
            <FileText size={13} /> {ctx.source === "selection" ? "Selected text" : "What you copied"} · {ctx.text.length > 999 ? `${Math.round(ctx.text.length / 100) / 10}k` : ctx.text.length} characters
          </button>
        ) : (
          <button type="button" className="chip ghost" onClick={useClipboard}><ClipboardPaste size={13} /> Nothing selected. Use what I copied</button>
        )}
        {hasText && <button className="chip-x" onClick={() => setCtx({ ...ctx, text: "", source: "none" })} aria-label="Leave out the text"><X size={11} /></button>}
        {ctx.image && (
          <span className="chip"><img src={ctx.image} alt="Screenshot of the window" className="askbar-thumb" /> Screenshot
            <button className="chip-x" onClick={() => setCtx({ ...ctx, image: "" })} aria-label="Leave out the screenshot"><X size={11} /></button></span>
        )}
        {settings?.desk && (
          <button type="button" className={`chip ghost ${settings.screenshot ? "on" : ""}`} onClick={toggleShot} aria-pressed={settings.screenshot}
            title="Include a picture of the window you were in each time you press the shortcut">
            <Camera size={13} /> {settings.screenshot ? "Screenshots on" : "Screenshots off"}
          </button>
        )}
      </div>
      {showText && hasText && <pre className="askbar-text">{ctx.text}</pre>}

      {!asked && (
        <div className="askbar-quick" role="group" aria-label="Quick actions">
          {QUICK.filter((x) => !x.needsText || hasText).map((x) => (
            <button key={x.id} type="button" className="chip" onClick={() => ask(x.id)} disabled={busy}>{x.label}</button>
          ))}
        </div>
      )}

      {asked && (
        <div className="askbar-answer" aria-live="polite">
          <div className="faint small askbar-asked">{asked}</div>
          {answer ? <Markdown text={answer} /> : <div className="typing" role="status"><span /><span /><span /><span className="typing-label">Thinking…</span></div>}
        </div>
      )}

      <form className="askbar-input" onSubmit={(e) => ask("", e)}>
        <textarea ref={input} className="input" rows={1} value={q} onChange={(e) => setQ(e.target.value)} aria-label="Your question"
          placeholder={asked ? "Ask a follow-up" : hasText ? "Ask about this, or tell me what to do with it" : "Ask anything"}
          onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); ask(""); } }} />
        <DictateButton onText={(t) => { setQ((v) => (v ? v + " " : "") + t); input.current?.focus(); }} />
        <button className="btn primary icon" type="submit" disabled={busy || !q.trim()} aria-label="Ask">{busy ? <Loader2 size={15} className="spin" /> : <ArrowUp size={15} />}</button>
      </form>

      {answer && !busy && (
        <div className="askbar-actions">
          <button className="btn primary sm" onClick={paste} title={settings?.desk ? "Put the answer into the app you came from" : "Copy it, ready to paste"}>
            <CornerDownLeft size={14} /> Paste back
          </button>
          <button className="btn sm" onClick={copy}><Copy size={14} /> Copy</button>
          <div className="spacer" />
          <button className="btn ghost sm" onClick={toChat} title="Carry on in Chat, where I can use your tools (with your OK)"><MessageSquare size={14} /> Do it in Chat</button>
        </div>
      )}
      {toast.node}
    </div>
  );
}
