import { Brain, Download, Loader2, RefreshCw, Trash2, X } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { api } from "../api";
import { Empty, relTime, useToast } from "../util";

// Phase 4: fine-tuning. Datasets come from your own chats (user/assistant text only, redacted); training runs on a
// provider that offers it, because this container has no GPU. The finished model shows up in every model picker.
type Conv = { id: number; title: string; channel: string; updated_at: string; user_messages: number; assistant_messages: number };
type Dataset = { id: number; name: string; examples: number; chars: number; created_at: string; ready: boolean; note: string };
type Job = { id: number; dataset_id: number; connection_id: number; provider: string | null; base_model: string; status: string;
  fine_tuned_model: string; model_ref: string; error: string; created_at: string; updated_at: string };
type Provider = { id: number; name: string };

const DONE = new Set(["succeeded", "failed", "cancelled"]);
const tone = (s: string) => (s === "succeeded" ? "ok" : s === "failed" || s === "cancelled" ? "danger" : "warn");

export default function FineTune() {
  const toast = useToast();
  const [convs, setConvs] = useState<Conv[]>([]);
  const [picked, setPicked] = useState<Set<number>>(new Set());
  const [datasets, setDatasets] = useState<Dataset[]>([]);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [providers, setProviders] = useState<Provider[]>([]);
  const [name, setName] = useState("");
  const [system, setSystem] = useState("");
  const [form, setForm] = useState({ dataset_id: 0, connection_id: 0, base_model: "gpt-4.1-mini-2025-04-14", suffix: "" });
  const [busy, setBusy] = useState("");

  const load = useCallback(async () => {
    const [c, d, j, p] = await Promise.all([api<Conv[]>("/finetune/conversations"), api<Dataset[]>("/finetune/datasets"),
      api<Job[]>("/finetune/jobs"), api<{ providers: Provider[] }>("/providers")]);
    const provs = p.providers ?? [];
    setConvs(c); setDatasets(d); setJobs(j); setProviders(provs);
    setForm((f) => ({ ...f, dataset_id: f.dataset_id || d.find((x) => x.ready)?.id || 0, connection_id: f.connection_id || provs[0]?.id || 0 }));
  }, []);
  useEffect(() => { load().catch(toast.err); }, [load]); // eslint-disable-line react-hooks/exhaustive-deps

  const run = async (label: string, fn: () => Promise<unknown>, ok?: string) => {
    setBusy(label);
    try { await fn(); if (ok) toast.ok(ok); await load(); } catch (e) { toast.err(e); }
    setBusy("");
  };
  const toggle = (id: number) => setPicked((s) => { const n = new Set(s); if (n.has(id)) n.delete(id); else n.add(id); return n; });

  return (
    <div className="page">
      {toast.node}
      <div className="page-head">
        <div>
          <h1>Fine-tune</h1>
          <p className="muted">Teach a model your style from your own chats. Only your messages and the assistant's answers are used (no tool output,
            no memories), with secrets removed. Training runs at a provider that offers it and costs money there; the finished model then appears in every model picker.</p>
        </div>
      </div>

      <section className="card col" style={{ marginBottom: 12 }}>
        <div className="row"><b className="grow">1. Pick conversations</b><span className="small muted">{picked.size} picked</span>
          <button className="btn ghost sm" onClick={() => setPicked(new Set(convs.map((c) => c.id)))}>All</button>
          <button className="btn ghost sm" onClick={() => setPicked(new Set())}>None</button></div>
        {!convs.length && <Empty icon={<Brain size={22} />} title="No conversations yet" hint="Chat with the assistant first; answered conversations show here." />}
        <div style={{ maxHeight: 260, overflow: "auto" }}>
          {convs.map((c) => (
            <label key={c.id} className="row small" style={{ padding: "4px 0" }}>
              <input type="checkbox" checked={picked.has(c.id)} onChange={() => toggle(c.id)} />
              <span className="grow truncate">{c.title}</span>
              <span className="muted">{c.channel} · {c.user_messages + c.assistant_messages} messages · {relTime(c.updated_at)}</span>
            </label>
          ))}
        </div>
        <div className="row wrap">
          <input className="input grow" placeholder="Dataset name" value={name} onChange={(e) => setName(e.target.value)} maxLength={120} />
          <input className="input grow" placeholder="Optional system message for every example" value={system} onChange={(e) => setSystem(e.target.value)} maxLength={4000} />
          <button className="btn primary sm" disabled={!picked.size || !!busy}
            onClick={() => run("dataset", () => api("/finetune/datasets", { body: { name, system, conversation_ids: [...picked] } }), "Dataset built")}>
            {busy === "dataset" ? <Loader2 size={14} className="spin" /> : null} Build dataset</button>
        </div>
      </section>

      <section className="card flush" style={{ marginBottom: 12 }}>
        <div className="list-item"><b>2. Datasets</b><span className="small muted">&nbsp;· the JSONL also works for training on your own GPU (docs/finetune.md)</span></div>
        {!datasets.length && <div className="list-item muted small">None yet.</div>}
        {datasets.map((d) => (
          <div key={d.id} className="list-item">
            <div className="grow"><div className="truncate">{d.name}</div>
              <div className="small muted">{d.examples} examples · {(d.chars / 1000).toFixed(0)}k characters · {relTime(d.created_at)}{d.note ? ` · ${d.note}` : ""}</div></div>
            <a className="btn ghost sm" href={`/api/finetune/datasets/${d.id}/download`}><Download size={13} /> JSONL</a>
            <button className="btn ghost sm icon" aria-label={`Delete ${d.name}`} onClick={() => run("del", () => api(`/finetune/datasets/${d.id}`, { method: "DELETE" }))}><Trash2 size={13} /></button>
          </div>
        ))}
      </section>

      <section className="card col" style={{ marginBottom: 12 }}>
        <b>3. Train at a provider</b>
        <div className="small muted">Works with providers that offer OpenAI-style fine-tuning (OpenAI, Mistral, compatible servers). Check the provider's prices first.</div>
        <div className="row wrap">
          <select className="input" style={{ width: "auto" }} value={form.dataset_id} onChange={(e) => setForm({ ...form, dataset_id: Number(e.target.value) })} aria-label="Dataset">
            <option value={0}>Dataset…</option>
            {datasets.filter((d) => d.ready).map((d) => <option key={d.id} value={d.id}>{d.name} ({d.examples})</option>)}
          </select>
          <select className="input" style={{ width: "auto" }} value={form.connection_id} onChange={(e) => setForm({ ...form, connection_id: Number(e.target.value) })} aria-label="Provider">
            {providers.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
          </select>
          <input className="input" placeholder="Base model" value={form.base_model} onChange={(e) => setForm({ ...form, base_model: e.target.value })} />
          <input className="input" placeholder="Name suffix (optional)" value={form.suffix} onChange={(e) => setForm({ ...form, suffix: e.target.value })} maxLength={40} />
          <button className="btn primary sm" disabled={!form.dataset_id || !form.connection_id || !form.base_model.trim() || !!busy}
            onClick={() => run("job", () => api("/finetune/jobs", { body: form }), "Training started")}>
            {busy === "job" ? <Loader2 size={14} className="spin" /> : null} Start training</button>
        </div>
      </section>

      <section className="card flush">
        <div className="list-item"><b>Jobs</b></div>
        {!jobs.length && <div className="list-item muted small">No training jobs yet.</div>}
        {jobs.map((j) => (
          <div key={j.id} className="list-item">
            <span className={`badge ${tone(j.status)}`}>{j.status}</span>
            <div className="grow"><div className="truncate">{j.fine_tuned_model || j.base_model}</div>
              <div className="small muted truncate">{j.provider ?? "removed provider"} · dataset #{j.dataset_id} · {relTime(j.created_at)}{j.error ? ` · ${j.error}` : ""}</div></div>
            {j.model_ref && <button className="btn ghost sm" onClick={() => navigator.clipboard.writeText(j.model_ref).then(() => toast.ok("Model ref copied: pick it in Models or a chat"))}>Copy model</button>}
            {!DONE.has(j.status) && <>
              <button className="btn ghost sm icon" aria-label="Refresh" onClick={() => run(`r${j.id}`, () => api(`/finetune/jobs/${j.id}/refresh`, { method: "POST" }))}>
                {busy === `r${j.id}` ? <Loader2 size={13} className="spin" /> : <RefreshCw size={13} />}</button>
              <button className="btn ghost sm icon" aria-label="Cancel" onClick={() => run(`c${j.id}`, () => api(`/finetune/jobs/${j.id}/cancel`, { method: "POST" }), "Cancelled")}><X size={13} /></button>
            </>}
          </div>
        ))}
      </section>
    </div>
  );
}
