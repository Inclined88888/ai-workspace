"use strict";
/**
 * Desktop notifications from ROOKS's live events (GET /api/v1/events), using the window's signed-in session.
 * The first baseline only records the current state, so opening the app does not replay old news. After that:
 *   - more items need your review              → "ROOKS needs your review", opens Needs your review
 *   - work reaches "ready for your review"    → opens the conversation it came from (or Needs your review)
 *   - work completes or cannot finish          → opens the conversation it came from
 * A dropped connection reconnects with a growing wait; its new baseline replaces what was known.
 */
const { EventEmitter } = require("node:events");

const NOTIFY_PHASES = { awaiting_approval: "Ready for your review", completed: "Completed", failed: "Could not finish" };

class Live extends EventEmitter {
  constructor(net, session, baseUrl) {
    super();
    this.net = net;
    this.session = session;
    this.base = baseUrl;
    this.req = null;
    this.closed = false;
    this.wait = 1000;
    this.phases = new Map();
    this.review = null;
  }

  start() {
    if (this.closed) return;
    const req = this.net.request({ url: `${this.base}/api/v1/events`, session: this.session, useSessionCookies: true,
      headers: { "X-Workbench": "1", "X-Rooks-API": "1", Accept: "text/event-stream" } });
    this.req = req;
    let buf = "";
    req.on("response", (res) => {
      if (process.env.ROOKS_DESKTOP_TEST === "1") console.log(`LIVE status ${res.statusCode}`);
      if (res.statusCode !== 200) {  // not signed in yet (401): check again soon, so news after signing in is not missed
        res.on("data", () => {});
        res.on("end", () => this.again(res.statusCode === 401 ? 2000 : null));
        return;
      }
      this.wait = 1000;
      res.on("data", (chunk) => {
        buf += chunk.toString("utf8");
        let i;
        while ((i = buf.indexOf("\n\n")) >= 0) {
          const block = buf.slice(0, i);
          buf = buf.slice(i + 2);
          for (const line of block.split("\n")) {
            if (!line.startsWith("data:")) continue;
            try {
              const e = JSON.parse(line.slice(5));
              if (process.env.ROOKS_DESKTOP_TEST === "1") console.log(`LIVE event ${e.type}`);
              this.handle(e);
            } catch { /* ignore a malformed frame */ }
          }
        }
      });
      res.on("end", () => this.again());
      res.on("error", () => this.again());
    });
    req.on("error", (err) => { if (process.env.ROOKS_DESKTOP_TEST === "1") console.log(`LIVE error ${err.message}`); this.again(); });
    req.end();
  }

  again(ms) {
    if (this.closed) return;
    const wait = ms || this.wait;
    this.wait = Math.min(this.wait * 2, 30000);
    setTimeout(() => this.start(), wait);
  }

  handle(e) {
    if (e.type === "baseline") {
      const seed = this.review === null;  // the very first baseline: remember, do not announce
      for (const w of e.work || []) {
        if (seed) this.phases.set(w.id, w.phase);
        else this.workChanged(w);
      }
      if (seed) this.review = e.needs_review;
      else this.reviewChanged(e.needs_review);
      return;
    }
    if (e.type === "needs_review") this.reviewChanged(e.count);
    if (e.type === "work") for (const w of e.items || []) this.workChanged(w);
  }

  reviewChanged(n) {
    if (typeof n === "number" && this.review !== null && n > this.review) {
      this.emit("notify", { title: "ROOKS needs your review", body: n === 1 ? "One item is waiting for you." : `${n} items are waiting for you.`,
        path: "/needs-you", kind: "review" });
    }
    if (typeof n === "number") this.review = n;
  }

  workChanged(w) {
    const before = this.phases.get(w.id);
    this.phases.set(w.id, w.phase);
    if (before === w.phase || !NOTIFY_PHASES[w.phase]) return;
    const path = w.conversation_id ? `/assistant/${w.conversation_id}` : w.phase === "awaiting_approval" ? "/needs-you" : "/";
    this.emit("notify", { title: `${w.status_text || NOTIFY_PHASES[w.phase]}: ${String(w.title).slice(0, 80)}`,
      body: w.phase === "failed" && w.error ? String(w.error).slice(0, 160) : "Open ROOKS to see it.", path, kind: w.phase, work: w.id });
  }

  close() {
    this.closed = true;
    try { this.req && this.req.abort(); } catch { /* already closed */ }
  }
}

module.exports = { Live };
