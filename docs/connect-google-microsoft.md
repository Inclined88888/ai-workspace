# Connect Google and Connect Microsoft: maintainer setup

AI Workspace's **Connect your mail & calendar** card (Settings, and the Home page until something is
connected) lets a person click **Connect Google** or **Connect Microsoft**, sign in on Google's or Microsoft's own page
in their normal browser, and get **both** their inbox and their calendar in one go. Everything else (iCloud, Yahoo,
Fastmail, AOL, Zoho, your own domain) goes through **Other provider**, which detects the servers from the email address
and walks through creating an app password.

The one-click buttons need an OAuth client registration that belongs to whoever publishes the build. This repo ships with
**empty** client IDs on purpose; until you fill them in, clicking the button explains that one-click isn't set up in this
build and offers the app-password path and "Use your own app registration". Nothing is faked.

## How it works (for reviewers)

- OAuth 2.0 authorization code flow with **PKCE (S256)** for a native app (RFC 8252). The backend opens a one-shot
  listener on a random port on the loopback interface, opens the system browser (desktop build) or a new tab (web),
  and exchanges the code itself. A copy-paste fallback accepts the final redirect URL when the browser is on another
  device.
- Google redirect: `http://127.0.0.1:<random port>/oauth/callback`. Microsoft redirect: `http://localhost:<random
  port>/oauth/callback` (Entra ignores the port for `localhost`, so one registered `http://localhost` covers every port;
  set `MICROSOFT_REDIRECT_HOST=127.0.0.1` if your tenant requires it and register `http://127.0.0.1` instead).
- Tokens are stored encrypted with the app's secret store (the OS keychain in the desktop build when
  `WORKBENCH_SECRET_BACKEND=keychain`, Fernet-encrypted in the database otherwise). Access tokens refresh automatically;
  a revoked or expired grant turns the account chip into **Sign in again**. **Disconnect** deletes the tokens, revokes
  the Google grant (Microsoft Graph has no revoke endpoint, so the card tells the person where to remove it:
  <https://account.live.com/consent/Manage> or <https://myapps.microsoft.com>), and removes the synced mailbox and calendar.
- Every network call goes through the audited `egress` client. Sending email and creating events with attendees always
  stop for an explicit approval in the assistant, even in a clean conversation.

### Scopes requested

| Provider | Scope | Why | Google classification |
| --- | --- | --- | --- |
| Google | `openid email profile` | show which account is connected | non-sensitive |
| Google | `gmail.readonly` | list, read and search mail | **restricted** |
| Google | `gmail.compose` | save drafts and send (with approval) | **restricted** |
| Google | `calendar.events` | read events, create events and invitations | sensitive |
| Microsoft | `openid email profile offline_access User.Read` | identity and refresh tokens | delegated, user consent |
| Microsoft | `Mail.ReadWrite Mail.Send` | read, search, draft, send (with approval) | delegated, user consent* |
| Microsoft | `Calendars.ReadWrite` | read and create events | delegated, user consent* |

\* subject to the tenant's consent policy; see "Microsoft: work and school accounts" below.

## 1. Google (Gmail + Google Calendar)

Cost: Google Cloud project and OAuth client are free. Verification of restricted Gmail scopes is free from Google but
may require a paid annual security assessment (see 1.5).

1. Go to <https://console.cloud.google.com/>, create a project named e.g. `AI Workspace` (no billing account needed).
2. **APIs & Services → Library**: enable **Gmail API** and **Google Calendar API**.
3. **Google Auth Platform → Branding**: app name `AI Workspace`, support email, logo (optional until verification),
   home page URL, privacy policy URL, terms URL, and authorised domain (the domain those pages live on; verify it in
   Google Search Console).
4. **Audience**: User type **External**. Leave publishing status **Testing** while you try it; add your own Google
   accounts as test users (max 100).
5. **Data access → Add or remove scopes**: add `openid`, `.../auth/userinfo.email`, `.../auth/userinfo.profile`,
   `https://www.googleapis.com/auth/gmail.readonly`, `https://www.googleapis.com/auth/gmail.compose`,
   `https://www.googleapis.com/auth/calendar.events`.
6. **Clients → Create client → Application type: Desktop app**, name `AI Workspace desktop`. No redirect URI is needed
   (Desktop clients accept any `http://127.0.0.1:<port>` loopback redirect). Copy the **Client ID** and **Client secret**.
   For installed apps Google states the secret "is obviously not treated as a secret"; its token endpoint still requires
   it, so it ships inside the build. PKCE is what protects the flow.

### 1.5 Verification, and what happens before it is done

- **Testing mode**: only listed test users can sign in, they see a "Google hasn't verified this app" screen, and
  **refresh tokens expire after 7 days**, so people must click **Sign in again** weekly. Fine for you and a few testers.
- **In production but unverified**: anyone can sign in after clicking *Advanced → Go to AI Workspace (unsafe)*, but
  Google caps the app at **100 users** for its lifetime until verification completes. The connect card tells users
  about that screen while they sign in.
- **Verification** (Google Auth Platform → Verification Center): brand verification (2–3 business days), then
  restricted-scope review: a written justification for each scope, an unlisted YouTube demo video showing the consent
  screen (with the client ID visible in the address bar) and each feature that uses each scope, and a privacy policy
  that meets the Google API Services User Data Policy and Limited Use requirements. Budget **several weeks**.
- **Security assessment (CASA)**: Google requires an annual assessment by an App Defense Alliance authorised lab when
  an app with restricted scopes can access that data "from or through a third-party server". The desktop build keeps
  mail on the person's computer, which is the strongest case for not needing one; however, when a person chooses a
  cloud AI model, message text they ask about is sent to that model provider at their direction. Google decides the
  outcome during review, so plan for it: published lab prices in 2026 run from about **US$500–900/year** (AL1) to
  about **US$5,400/year** (AL2, manual penetration test); Google itself charges nothing. Revalidation is every 12 months.
- **Ways to avoid the restricted review**: personal use (you plus a handful of people you know, under the 100-user cap),
  an **Internal** app inside a Google Workspace organisation, or ask people to use a Gmail app password via
  **Other provider** (works today, no Google review; needs 2-Step Verification on their account).

## 2. Microsoft (Outlook.com, Hotmail, Microsoft 365 + Outlook Calendar)

Cost: free. An Entra tenant comes with any Microsoft account (or create a free one via the Microsoft 365 Developer
Program or the Azure free account; no paid subscription is needed for app registrations).

1. Go to <https://entra.microsoft.com> → **Identity → Applications → App registrations → New registration**.
2. Name `AI Workspace`. Supported account types: **Accounts in any organizational directory and personal Microsoft
   accounts** (this is the `common` endpoint). 
3. Redirect URI: platform **Public client/native (mobile & desktop)**, value `http://localhost`.
4. **Authentication → Advanced settings → Allow public client flows: Yes**. Do **not** create a client secret; the app is
   a public client and uses PKCE.
5. **API permissions → Add → Microsoft Graph → Delegated**: `openid`, `email`, `profile`, `offline_access`,
   `User.Read`, `Mail.ReadWrite`, `Mail.Send`, `Calendars.ReadWrite`. Do not click "Grant admin consent" for other
   tenants; each person consents for themselves.
6. Copy the **Application (client) ID** from Overview.
7. **Publisher verification** (Branding & properties): link a verified Microsoft AI Cloud Partner Program (formerly
   MPN) ID. Free, but needs a business with a verified domain. Without it, users in many work tenants are blocked from
   consenting to an unverified multi-tenant app.

### Microsoft: work and school accounts

Personal Microsoft accounts (outlook.com, hotmail.com, live.com) can consent straight away. For work or school
accounts, the tenant's consent policy decides: Microsoft's default "Microsoft-managed" policy (rolled out from late
2025) requires **admin consent** for mail and calendar permissions in many tenants. When that happens the app shows
"Your organisation needs an administrator to approve AI Workspace for mail and calendar". The admin can approve it at
`https://login.microsoftonline.com/<their tenant>/adminconsent?client_id=<your client id>`.

## 3. Put the IDs into a build

Any of these works; the first that is set wins at run time:

1. **Environment** (good for self-hosted/Docker): `GOOGLE_OAUTH_CLIENT_ID`, `GOOGLE_OAUTH_CLIENT_SECRET`,
   `MICROSOFT_OAUTH_CLIENT_ID`, optional `MICROSOFT_OAUTH_TENANT` (default `common`).
2. **Per install**: Settings → Connect your mail & calendar → *Use your own app registration (advanced)*. Lets a power user or an IT
   department use their own registration without rebuilding.
3. **Build time** (desktop releases): set repository **variables** `GOOGLE_OAUTH_CLIENT_ID` and
   `MICROSOFT_OAUTH_CLIENT_ID`, and repository **secret** `GOOGLE_OAUTH_CLIENT_SECRET`. The desktop workflow runs
   `scripts/write-oauth-clients.py`, which writes them into `workbench/backend/app/oauth_clients.py` before the sidecar
   is frozen. With nothing set, that step is a no-op and the buttons explain the fallback.

```bash
GOOGLE_OAUTH_CLIENT_ID=1234-abc.apps.googleusercontent.com GOOGLE_OAUTH_CLIENT_SECRET=GOCSPX-... \
MICROSOFT_OAUTH_CLIENT_ID=00000000-0000-0000-0000-000000000000 python scripts/write-oauth-clients.py
# One-click Google: on; one-click Microsoft: on
```

## 4. Checking it works

1. Start the app with the IDs set, open Settings → Connect your mail & calendar, click **Connect Google**, sign in, allow everything.
2. The chip shows `you@gmail.com · Mail + Calendar`. Mail shows the inbox; Calendar shows *Google Calendar · you@…*.
3. Ask the assistant "reply to the last email saying thanks"; it should hold `send_email` for approval.
4. If someone unticks the Gmail or Calendar box on Google's consent screen, the card says which part is missing and
   offers to sign in again.

Other knobs (mostly for tests): `GOOGLE_AUTH_URL`, `GOOGLE_TOKEN_URL`, `GOOGLE_REVOKE_URL`, `GOOGLE_USERINFO_URL`,
`GMAIL_API_BASE`, `GCAL_API_BASE`, `MICROSOFT_LOGIN_BASE`, `GRAPH_API_BASE`, `CONNECT_CALLBACK_PORT` (fixed loopback
port), `CONNECT_OPEN_BROWSER=0` (never open a browser from the backend), `MAIL_AUTODETECT_ONLINE=0` (Other provider
uses only the built-in table, no MX/ISPDB lookups).
