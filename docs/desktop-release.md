# Desktop app: building, signing and releasing

The desktop app (`desktop/`) is a [Tauri v2](https://v2.tauri.app) shell around Workbench. It runs the Workbench
backend as a **sidecar**: the same FastAPI + SQLite code, frozen with PyInstaller into one executable, started on
`127.0.0.1` with a per-user data folder. The window shows the same React frontend the Docker install serves. No Docker,
no terminal, no password: the shell signs its own window in with a one-time token on every launch.

| | Where your data lives |
| --- | --- |
| macOS | `~/Library/Application Support/com.inclined88888.aiworkspace/` (logs: `~/Library/Logs/com.inclined88888.aiworkspace/`) |
| Windows | `%APPDATA%\com.inclined88888.aiworkspace\` (logs: `%LOCALAPPDATA%\com.inclined88888.aiworkspace\logs\`) |
| Linux | `~/.local/share/com.inclined88888.aiworkspace/` (logs: `~/.local/share/com.inclined88888.aiworkspace/logs/`) |

Secrets: the database encryption key (`APP_SECRET`), the tools key, and every provider API key are kept in the OS
keychain (macOS Keychain, Windows Credential Manager, Linux Secret Service). The database stores only a reference such as
`keychain:connection:3`. On a Linux machine with no unlocked Secret Service the app falls back to a `0600` file in the
data folder and Settings → Desktop app says so.

## How CI builds it

`.github/workflows/desktop.yml` runs on **manual dispatch** (Actions → Desktop app → Run workflow: installers appear as
workflow artifacts) and on **tags** `vX.Y.Z` or `desktop-vX.Y.Z` (installers are also attached to that GitHub release;
for `v*` tags it waits for `release.yml` to create the release first).

| Runner | Output |
| --- | --- |
| `macos-15` | `AI Workspace_<ver>_aarch64.dmg` (Apple silicon) |
| `macos-15-intel` | `AI Workspace_<ver>_x64.dmg` (Intel) |
| `windows-2022` | `AI Workspace_<ver>_x64_en-US.msi` and `AI Workspace_<ver>_x64-setup.exe` |
| `ubuntu-22.04` | `.deb` and `.AppImage` |

Two DMGs, not one universal DMG: PyInstaller can only build a universal2 sidecar if Python and every compiled wheel
(cryptography, pydantic-core, lxml, …) are universal2, which they are not on PyPI. Each architecture is frozen natively.

Each job: builds the frontend → freezes the backend (`desktop/scripts/build-sidecar.mjs`) → **smoke-tests the frozen
sidecar** (`desktop/scripts/smoke_sidecar.py`: health, launch token, frontend, exit-with-parent) → `tauri build`.

## Signing: off until the secrets exist

Nothing needs to be configured to get **unsigned** installers. Each kind of signing switches on by itself when its
repository secrets are present (Settings → Secrets and variables → Actions). The workflow log says which ones are on.

### Without signing, what users see

* **macOS:** the app is ad-hoc signed only. Gatekeeper says it "cannot be opened because Apple cannot check it for
  malicious software". Users right-click the app → **Open** → **Open** (macOS 15+: System Settings → Privacy & Security
  → **Open Anyway**). Once.
* **Windows:** SmartScreen shows "Windows protected your PC". Users click **More info** → **Run anyway**.
* **Keychain (macOS):** an unsigned app's keychain access is tied to its exact binary, so after each update macOS asks
  "AI Workspace wants to use your confidential information" once. Signing with a stable Developer ID removes this.

### macOS: Developer ID signing and notarisation

| Secret | What it is | How to get it |
| --- | --- | --- |
| `APPLE_CERTIFICATE` | Base64 of your **Developer ID Application** certificate exported as `.p12` | Xcode or developer.apple.com → Certificates → "Developer ID Application"; export from Keychain Access as .p12; `base64 -i cert.p12 \| pbcopy` |
| `APPLE_CERTIFICATE_PASSWORD` | Password you set when exporting the `.p12` | |
| `APPLE_SIGNING_IDENTITY` | e.g. `Developer ID Application: Your Name (TEAMID1234)` | `security find-identity -v -p codesigning` |
| `APPLE_ID` | Apple ID email used for notarisation | |
| `APPLE_PASSWORD` | An **app-specific password** for that Apple ID (not your real password) | appleid.apple.com → Sign-In and Security → App-Specific Passwords |
| `APPLE_TEAM_ID` | 10-character team ID | developer.apple.com → Membership |

**Cost: US$99 per year** (Apple Developer Program, individual or organisation; organisations need a D-U-N-S number,
which is free). With only the first three secrets the app is signed but not notarised (Gatekeeper still warns); add the
last three for a warning-free install.

`desktop/src-tauri/Entitlements.plist` grants the hardened-runtime exceptions the PyInstaller one-file sidecar needs
(it unpacks its Python libraries to a temp folder at launch). **Unverified until the first signed build:** if
notarised launches fail with a library-validation error, switch the sidecar to a PyInstaller one-folder build signed
file-by-file.

### Windows: code signing

Pick one. Since June 2023 new OV/EV certificates must live on a hardware token or cloud HSM, so a plain `.pfx` in a
secret is no longer an option for new certificates.

**Option A: Azure Artifact Signing (formerly Trusted Signing): about US$9.99 per month** (Basic: 5,000 signatures,
then US$0.005 each). Needs a paid Azure subscription. Eligibility: **organisations** in the US, Canada, EU, UK,
**Australia**, NZ and a few more; **individual developers only in the US and Canada.** So from Australia this needs a
registered organisation (company / ABN), not a personal account. Identity validation takes a few business days.

| Secret | What it is |
| --- | --- |
| `AZURE_CLIENT_ID`, `AZURE_CLIENT_SECRET`, `AZURE_TENANT_ID` | An Entra app registration (service principal) with the *Artifact Signing Certificate Profile Signer* role |
| `AZURE_SIGNING_ENDPOINT` | Your region's endpoint, e.g. `https://eus.codesigning.azure.net` |
| `AZURE_SIGNING_ACCOUNT` | Artifact Signing account name |
| `AZURE_SIGNING_PROFILE` | Certificate profile name |

**Option B: an OV certificate in a cloud HSM: about US$150–300 per year** plus the CA's cloud-signing fee (e.g.
SSL.com eSigner, DigiCert KeyLocker, Sectigo). Available to individuals worldwide. Put the provider's signing command in
`WINDOWS_SIGN_COMMAND`, using `%1` for the file path, e.g.
`CodeSignTool sign -username=... -password=... -totp_secret=... -input_file_path=%1 -override`.

**Option C: Microsoft Store (free).** The Store re-signs MSIX packages, so there is no certificate cost and no
SmartScreen warning. Not wired up yet (it needs an MSIX package and a Partner Center account).

Either way SmartScreen reputation builds up over the first downloads; signing does not remove the warning on day one.

### In-app updater (prepared, disabled)

The updater code is in `desktop/src-tauri/src/updater.rs` behind the `updater` Cargo feature, which is off by default,
so the app never contacts an update server today. To turn it on:

1. `cd desktop && npx tauri signer generate -w ~/.tauri/ai-workspace.key` (free; keep the private key safe: losing it means
   existing installs can no longer update).
2. Put the **public** key in `desktop/src-tauri/tauri.updater.conf.json` in place of the placeholder and commit it.
3. Add secrets `TAURI_SIGNING_PRIVATE_KEY` (the private key file's contents) and `TAURI_SIGNING_PRIVATE_KEY_PASSWORD`.

From the next tag, builds include the updater, sign update bundles (`.sig`), and the app checks
`releases/latest/download/latest.json` at start-up and asks before installing. **One more step is still needed:**
generating and uploading `latest.json` (Tauri's format, listing each platform's bundle URL and signature) in the
`publish` job. That is left for the release that turns the updater on, so it can be tested end to end.

### Cost summary

| Item | Cost | Needed for |
| --- | --- | --- |
| Apple Developer Program | US$99 / year | No Gatekeeper warning on macOS |
| Azure Artifact Signing (organisation) | ~US$9.99 / month | Signed Windows installers |
| or OV certificate + cloud HSM | ~US$150–300 / year + HSM fee | Signed Windows installers (individuals outside US/Canada) |
| Tauri updater keys | free | Automatic updates |
| GitHub Actions minutes | free for public repos | Builds (macOS minutes are free on public repos too) |

## Building locally

```bash
cd workbench/frontend && npm ci && npm run build          # the UI the sidecar serves
python3.12 -m pip install -r desktop/sidecar/requirements.txt
cd desktop && npm ci
node scripts/build-sidecar.mjs                            # → src-tauri/binaries/workbench-server-<target>
npx tauri build                                           # installers in src-tauri/target/release/bundle/
```

Development without freezing: `WORKBENCH_SIDECAR_CMD="python3 sidecar/workbench_server.py" npx tauri dev` (a placeholder
file must still exist at `src-tauri/binaries/workbench-server-<target>` because Tauri checks external binaries at build
time; any executable works).

Linux needs `libwebkit2gtk-4.1-dev libgtk-3-dev librsvg2-dev libayatana-appindicator3-dev patchelf`.
