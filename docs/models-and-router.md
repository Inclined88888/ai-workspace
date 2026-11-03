# Models, sign-ins and the smart router

Cloud is the default: thinking happens on the provider's servers, so the user's computer stays cool and quiet. Local
Ollama is still there, marked Advanced.

## First run: "Choose how to think"

Shown on the desktop app's first start, and in Docker whenever you are signed in and no model is connected
(`/api/status` → `needs_models`).

1. **Free cloud (recommended).** A free Google Gemini (AI Studio) or Groq key. The screen opens the provider's key page;
   you copy the key and it lands in the field (Paste button, or it is read from the clipboard when you come back to the
   window, if the browser allows). The key's provider is recognised from its prefix, it is checked with a real model-list
   call *before* it is saved, and only the outcome comes back to the page. Adding both is offered, so the router has
   somewhere to go when one free day runs out.
2. **Sign in with your subscription.** Only flows the provider's terms allow:

   | Plan | Status | Why |
   |---|---|---|
   | ChatGPT (Free, Go, Plus, Pro, Business) | **Available** | OpenAI's Sign in with ChatGPT for open-source apps (https://developers.openai.com/siwc/token-sharing-open-source, https://help.openai.com/en/articles/20001542-using-your-chatgpt-plan-in-other-apps-and-sites). |
   | Claude Pro/Max | Not available | Anthropic's terms forbid third-party apps offering Claude.ai login or routing plan credentials (https://code.claude.com/docs/en/legal-and-compliance). Use a Claude API key. |
   | SuperGrok | Not available yet | xAI offers subscription sign-in to named partners only (Hermes: https://x.ai/news/grok-hermes, Kilo Code: https://kilo.ai/docs/ai-providers/xai); there is no public client registration, and reusing another app's client id is not allowed. Use an xAI API key. |
   | Google AI Pro/Ultra | Not available | Google prohibits reusing Gemini CLI / Antigravity OAuth in other apps, and accounts have been suspended for it (https://github.com/google-gemini/gemini-cli/discussions/20632). A free AI Studio key gives you Gemini anyway. |

3. **Paste API keys.** OpenAI, Anthropic, xAI, Google Gemini, Groq, OpenRouter, Mistral, DeepSeek, or any
   OpenAI-compatible base URL.
4. **Local Ollama (advanced).**

## Sign in with ChatGPT: how it works

`workbench/backend/app/siwc.py` follows OpenAI's open-source guide:

* First sign-in registers this install with `client_id=dynamic_agent_client`; OpenAI issues an `oaiapp_…` client id
  that is saved and reused. No client secret, partner key, or other vendor's client id is used.
* PKCE (S256), fresh state and nonce, loopback callback on `http://127.0.0.1:1455/auth/callback` (a random free
  port if 1455 is taken; `SIWC_PORT` overrides). In Docker the container's loopback isn't your browser's, so the screen also accepts the
  callback address pasted from the browser bar.
* The ID token is verified against OpenAI's JWKS; the `chatgpt.tokens.use.direct` scope must be granted.
* Requests go to `/v1/responses` with `store:false`, `stream:true`. Usage counts against the plan's limits (shared with
  the user's other apps); `subscription_sharing_usage_limit_exceeded` / `…_unavailable` are treated as a daily limit and
  the router moves on.
* Sign out revokes the refresh token first. OpenAI requires approval for commercial apps; this repo is MIT open source.

## Where keys and tokens live

Desktop app: the OS keychain (macOS Keychain, Windows Credential Manager, Secret Service on Linux); the database keeps only a `keychain:` reference. Docker: encrypted at rest in SQLite with the
app secret (there is no OS keychain in a container). Keys and tokens are never logged or returned by the API; key
validation returns only "works / rejected / unreachable" and a model count.

## The smart router (`auto`)

`auto`, `auto:fast` and `auto:strong` are virtual models, and `auto` becomes the default once any cloud provider is
connected. The code is `workbench/backend/app/router.py`.

* **Fast or strong.** Strong when the turn offers tools, the prompt is over about 12,000 characters, or the channel is
  research, browser, Rooks Code, Rooks Browser, an agent or the company add-on; fast otherwise (titles, summaries,
  memory). Each provider has a fast and a strong model, picked automatically from its model list or chosen on the Models
  page.
* **Order.** Subscription sign-ins first, then free tiers, then paid keys, then other compatible URLs. Local Ollama is
  last and only used when "Fall back to local" is on or nothing else is connected. Reorder or turn providers off on the
  Models page.
* **Fallback.** On 429, quota/credit errors, 5xx or an unreachable host, the next provider is tried. Streaming falls back
  only before the first token: a half-written answer is never restarted elsewhere. A provider that refuses a request (a 400-type error) is skipped for that request but not parked.
* **Daily limits.** A daily-limit error parks the provider until its reset (midnight Pacific for Gemini; the provider's
  reset header for Groq and others). Busy (per-minute) errors park it for `Retry-After`, or 30 s. Rejected keys park it
  for an hour and show "Sign in again" or "key rejected". When `x-ratelimit-remaining-requests` reaches zero on a daily
  window, the provider is parked before it fails.
* **Showing it.** Every answer carries a "via" line: provider, model, tier, free requests left today, and any provider
  that was skipped ("Google Gemini daily limit reached, switched"). The Models page (sidebar foot) shows the route, each
  provider's quota meter, when it resets, and a "try again now" button for a parked provider.
* **Quota numbers.** Groq and OpenRouter send rate-limit headers, so the count is the provider's own. Google does not, so
  Gemini shows requests used today plus a note pointing at AI Studio.

Free tiers checked 2026-10-30: Groq gpt-oss-120b 30 RPM / 1,000 RPD (https://console.groq.com/docs/rate-limits);
OpenRouter `:free` 20/min and 50/day, 1,000/day after $10 of credits; Gemini free limits are per project and reset at
midnight Pacific; Google no longer lists exact free numbers (https://ai.google.dev/gemini-api/docs/rate-limits), and free
prompts may be used to improve Google's products.

### Rooks Code, Rooks Browser and other OpenAI clients

The bridge at `/v1/chat/completions` (tools API key) accepts `model: "auto"`. Rooks: `-P workbench -m auto`. Send
`X-Workbench-Channel: rooks-code` (or `rooks-browser`, `browser`) to get the strong tier; the response includes a
`router` object naming the provider that answered.

### API

* `POST /api/providers/detect` `{api_key}` → provider guess (no network call).
* `POST /api/providers/validate` `{api_key, preset?, base_url?, save?}` → checks the key; with `save` stores it.
* `GET/PUT /api/router` (order, disabled, per-provider models, local fallback), `POST /api/router/reset/{id}`.
* `GET /api/auth/subscriptions`; `POST /api/auth/chatgpt/start`, `GET /api/auth/chatgpt/status/{state}`,
  `POST /api/auth/chatgpt/complete` `{callback_url}`, `POST /api/auth/chatgpt/signout/{id}`.

## Known gaps

* Sign in with ChatGPT is tested end to end against a fake OAuth server only, not against OpenAI.
* The via line is shown for answers in the current session; it is not stored with past messages.
* Gemini's remaining free quota is not reported by Google, so only usage is shown.
* A hosted free tier is a design only: `docs/hosted-free-tier.md`.
