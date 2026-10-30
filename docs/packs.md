# Packs: Trade, Legal and Study (feat/v02-packs)

Packs are optional bundles of skills, templates and pages for one kind of work. The assistant stays general: a pack only
adds shortcuts, and they show up only once the pack is added. Open **Packs** in the sidebar (under *Your work*) and press
**Add**. One click installs the whole pack, or nothing if any part fails. **Remove** takes the shortcuts away and leaves
your quotes, cards and notes where they were.

| Pack | Tier | What you get |
|---|---|---|
| Trade | Pro | A quote from a site photo, a voice note or a few typed lines. Then an invoice, payment reminder drafts and PDFs. ServiceM8 or Xero push is optional. |
| Legal | Pro | Clause library, version compare with a redline, defined-terms, cross-reference and citation checks, and a matter timeline. |
| Study & research | Free (lab notebook is Pro) | Flashcards with spaced repetition, worked maths checked with sympy, references (BibTeX, RIS, DOI, Zotero) and a lab notebook. |

Tiers are declared in `workbench/backend/app/features_v02.py` (`FEATURE_TIERS`). The check goes through
`packs.registry.allowed()`. That uses `account.allowed()` when the accounts branch is present, and otherwise allows
everything.

## Safety

- **Every skill in a pack goes through the same security gate as GitHub skills** (`skills_hub.review_folder`). A pack
  with a risky skill is refused and rolled back. Each add or remove is written to the skills audit log as a `pack` event.
- **Nothing is sent for you.** Payment reminders are drafts: you copy one, open it in your own mail app, or share it from
  the phone. Marking a document as sent or paid is something you do yourself.
- **ServiceM8 and Xero push takes two steps every time.**
  1. *Preview* shows exactly what would be sent and gives a one-time code that lasts 10 minutes.
  2. *Send* works only with that code.

  The assistant's pack tools can't push.
- **Every Legal result carries "Not legal advice"** and the place in the document it came from. Citation checks look at
  the format only. Each citation has an AustLII search link so you can open the source and check it yourself.
- **Maths runs in the sandbox** (`compute.run`), not inside the app.

## Trade: defaults and settings

Defaults are Australian, and you can change all of them under *Your business*:

- **Tax:** GST 10%, prices ex GST.
- **Payment terms:** 14 days. **Quotes are valid for:** 30 days.
- **Labour:** $95/hour. **Materials markup:** 20%. **Currency:** AUD.

A document is titled *Tax invoice* only when you're registered for GST and have a valid ABN. The ABN is checked with
the ATO checksum. Your logo, ABN and payment details go on every PDF.

Voice notes use the browser's own dictation (Web Speech) where it's available. You can always type or paste a
transcript instead.

### Connecting ServiceM8 (optional)

1. In ServiceM8 open **Settings → API Keys** and create a key that has permission to create jobs.
2. In AI Workspace open **Packs → Trade → Your business → ServiceM8** and paste the key.
3. On a quote, press **ServiceM8…** to preview, then **Send to ServiceM8** to send it.

The key is sent as `X-API-Key` and stored encrypted.

### Connecting Xero (optional)

1. In the Xero developer portal (developer.xero.com → My Apps) create a **Custom Connection**.
2. Give it the scopes **accounting.invoices** and **accounting.contacts**. Xero apps created since March 2026 use these
   granular scopes. Older connections that still have `accounting.transactions` also work.
3. Choose who authorises it and approve the email Xero sends. The connection is tied to one organisation.
4. Copy the **client id** and **client secret** into **Packs → Trade → Your business → Xero**.
5. On an invoice, press **Xero…** to preview, then **Send to Xero** to send it. Xero gets a **draft** invoice, so you
   approve it in Xero.

The app swaps the id and secret for a 30-minute token (the `client_credentials` grant) each time you send. It doesn't
ask for particular scopes, so the token gets whatever the connection was authorised for. Custom Connections are a paid
Xero add-on.

## Study: Zotero (optional)

1. Zotero 7 must be running on the same computer.
2. **Importing** your library uses Zotero's local API. In Zotero open **Settings → Advanced** and tick
   **Allow other applications on this computer to communicate with Zotero**.
3. **Sending** references *to* Zotero uses the same connector endpoint the browser extension uses. It saves into the
   collection that is selected in Zotero.

Everything stays on `127.0.0.1:23119`. Nothing goes to zotero.org. DOI look-up reads `doi.org` (CSL JSON), and only when
you press **Look up**.

## Phone

The phone app has a **Packs** screen, reached from Settings → Packs. You can review the flashcards that are due, and
read your quotes and invoices with reminder drafts that you share into your own mail app. Quotes are made and edited on
the computer. The phone talks to `/api/mobile/packs/*` with its paired-device token.

## For developers

- **Backend:** `workbench/backend/app/packs/` holds `registry`, `store`, `trade`, `trade_push`, `legal`, `study`,
  `tools`, `routes` and `mobile_routes`.
- **Bundles:** `packs/bundles/<id>/` holds `pack.json`, `skills/*/SKILL.md` and `templates/`.
- **Frontend:** `src/pages/Packs*.tsx` and `src/packs/`. Styles use the `pk-` prefix, theme variables only, and respect
  `prefers-reduced-motion` and `data-motion="off"`.
- **Mobile:** `mobile/src/app/packs/` and `mobile/src/lib/packs.ts`.
- **Tests:** `tests/test_packs.py`, `frontend/tests/packs.test.mjs` and `mobile/src/lib/__tests__/packs.test.ts`. All
  run offline: models, ServiceM8, Xero, Zotero and doi.org are faked with `httpx.MockTransport`.
