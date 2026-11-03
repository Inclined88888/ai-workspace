# Hosted free tier: design only

**Status: design only. Nothing in this document is built, hosted, or paid for.** It records what a project-run free
tier would take, so a later decision can be made with the costs and the terms in view. Today every user brings their own
free key (Gemini or Groq), signs in with ChatGPT, pastes a paid key, or runs Ollama (see `docs/models-and-router.md`).

## Why not build it now

* **Someone pays.** Every request through a shared endpoint is billed to the project, not the user. Free upstream tiers
  are per account or per project, so they cannot be pooled for many users without breaking the provider's terms
  (Gemini's limits are "per project"; Groq's and OpenRouter's are per organisation or account).
* **Abuse.** A free, anonymous, OpenAI-compatible endpoint is resold or scraped within days. It needs sign-up, per-user
  quotas, and moderation from day one.
* **Privacy.** A hosted relay sees every prompt. The app's promise today is "your keys and your prompts only go to the
  provider you chose". A relay changes that promise and needs a privacy policy, data retention rules, and a DPA.
* **The bring-your-own-free-key flow already costs nothing** and takes about two minutes (Free cloud on the welcome
  screen), with no relay in the middle.

## If it is built: shape

```
desktop / docker app ──HTTPS──▶ relay (Cloudflare Worker) ──▶ upstream (Workers AI, or a paid key held by the relay)
        │                             │
        │  device token (per install)  └─ per-user daily counter (Durable Object / KV), hard cap, kill switch
```

* **Upstream.** Cloudflare Workers AI is the cheapest fit: 10,000 neurons a day are free per account, then
  $0.011 per 1,000 neurons (https://developers.cloudflare.com/workers-ai/platform/pricing/, read 2026-10-30). That free
  allowance belongs to the project's account, so it covers the project's own testing, not a user base.
* **Identity.** A device token minted at first run after an email or GitHub sign-in, stored in the OS keychain like any
  other key. No anonymous access.
* **Quota.** A small fixed daily allowance per user (for example 50 requests), counted at the relay, returned in
  `x-ratelimit-*` headers so the existing smart router shows it and falls back to the user's own keys when it is spent.
  It would appear as one more provider ("AI Workspace free") ranked after the user's own free keys.
* **Controls.** A global spend cap that turns the relay off, a per-user cap, prompt size limits, no logging of prompt
  bodies, and a published retention policy.
* **Client side.** Nothing new: the relay speaks OpenAI Chat Completions, so it is a preset plus a router profile.

## Rough cost

At Workers AI pricing, 1,000 daily users × 50 small requests is on the order of tens of dollars a day, depending on
the model and prompt sizes (an estimate, not measured). That needs a sponsor or donations before it is switched on.

## Open questions

* Who owns the Cloudflare account and the bill.
* Whether the relay should also offer a paid tier (it changes the licence and support story).
* Terms review for each upstream before launch.
