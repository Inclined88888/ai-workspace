#!/usr/bin/env bash
# One-time setup of "Sign in with Microsoft" and "Sign in with Google" for AI Workspace.
#
# Every mail app that offers provider sign-in (Thunderbird, Spark, Outlook for Mac...) is registered once with Microsoft
# and Google by whoever publishes it. This script does that registration for this project, under YOUR developer
# accounts, using Microsoft's and Google's own sign-in pages and official CLIs (az, gcloud). Afterwards every person
# using AI Workspace just clicks "Sign in with Microsoft / Google"; nobody types a password into the app.
#
#   scripts/setup-sign-in.sh                 both providers
#   scripts/setup-sign-in.sh microsoft       only Microsoft (fully automatic after you sign in)
#   scripts/setup-sign-in.sh google          only Google (two pages in Google's console need a few clicks)
#
# Writes the IDs into workbench/backend/app/oauth_clients.py (the build's registration, see docs/connect-google-microsoft.md)
# and into the installed app in ~/.ai-workspace when present. Nothing else is changed.
set -euo pipefail

here=$(cd "$(dirname "$0")/.." && pwd)
which_ones=${1:-both}
MS_ID="" G_ID="" G_SECRET=""
say() { printf '\n\033[1m%s\033[0m\n' "$*"; }

microsoft() {
  command -v az >/dev/null || { echo "Installing Microsoft's Azure CLI (brew install azure-cli)…"; brew install azure-cli; }
  say "Microsoft: a browser opens on Microsoft's sign-in page. Sign in with the account that should own the registration."
  echo "(A personal Outlook account works if it has a Microsoft Entra directory; a work account works if your organisation lets members register apps.)"
  if ! az login --allow-no-subscriptions --only-show-errors >/dev/null || ! az ad signed-in-user show --query id -o tsv >/dev/null 2>&1; then
    microsoft_by_hand
    return
  fi
  graph=00000003-0000-0000-c000-000000000000
  # the delegated permission ids, read from Microsoft Graph itself (never typed in by hand)
  scopes=$(az ad sp show --id $graph --query "oauth2PermissionScopes[?contains(['openid','email','profile','offline_access','User.Read','Mail.ReadWrite','Mail.Send','Calendars.ReadWrite'], value)].{id:id,type:'Scope'}" -o json)
  n=$(python3 -c "import json,sys; print(len(json.loads(sys.argv[1])))" "$scopes")
  [[ $n == 8 ]] || { echo "Expected 8 Microsoft Graph permissions, found $n. Stopping." >&2; exit 1; }
  body=$(python3 - "$scopes" <<'PY'
import json, sys
print(json.dumps({
    "displayName": "AI Workspace",
    "signInAudience": "AzureADandPersonalMicrosoftAccount",   # work, school and personal (outlook.com) accounts
    "api": {"requestedAccessTokenVersion": 2},
    "isFallbackPublicClient": True,                          # a desktop app: PKCE, no secret
    "publicClient": {"redirectUris": ["http://localhost"]},  # Entra ignores the port for localhost
    "requiredResourceAccess": [{"resourceAppId": "00000003-0000-0000-c000-000000000000",
                                "resourceAccess": json.loads(sys.argv[1])}],
}))
PY
)
  existing=$(az ad app list --display-name "AI Workspace" --query "[?signInAudience=='AzureADandPersonalMicrosoftAccount'].appId | [0]" -o tsv 2>/dev/null || true)
  if [[ -n $existing ]]; then
    MS_ID=$existing
    echo "Found your existing registration: $MS_ID"
  else
    MS_ID=$(az rest --method POST --url https://graph.microsoft.com/v1.0/applications --headers "Content-Type=application/json" \
            --body "$body" --query appId -o tsv)
    echo "Registered: $MS_ID"
  fi
}

microsoft_by_hand() {
  # az signs in but cannot register apps for an account with no Microsoft Entra directory (most personal Outlook
  # accounts since 2024) or when an organisation blocks it. Microsoft's own web page does the same registration.
  say "Microsoft did not let the CLI register the app for that account. Microsoft's own registration page opens instead."
  cat <<MSG
  If it says you need a directory: sign in with a work or school account, or press "Create a free Azure account"
  (no charge, it only creates the directory). A personal Outlook address can still SIGN IN to AI Workspace later.

  1. New registration: Name "AI Workspace".
     Supported account types: "Accounts in any organizational directory and personal Microsoft accounts".
     Redirect URI: platform "Public client/native (mobile & desktop)", value  http://localhost   > Register.
  2. Authentication: "Allow public client flows" = Yes > Save.
  3. API permissions > Add > Microsoft Graph > Delegated: offline_access, User.Read, Mail.ReadWrite, Mail.Send,
     Calendars.ReadWrite > Add permissions.
  4. Overview: copy "Application (client) ID" and paste it here.
MSG
  open "https://entra.microsoft.com/#view/Microsoft_AAD_RegisteredApps/CreateApplicationBlade/quickStartType~/null/isMSAApp~/false" 2>/dev/null || true
  read -r -p "Application (client) ID: " MS_ID
  [[ $MS_ID =~ ^[0-9a-fA-F-]{36}$ ]] || { echo "That is not a client ID (36 characters like 1a2b3c4d-...). Run again: scripts/setup-sign-in.sh microsoft" >&2; MS_ID=""; }
}

google() {
  command -v gcloud >/dev/null || { echo "Install the Google Cloud CLI first: brew install --cask google-cloud-sdk" >&2; exit 1; }
  say "Google: a browser opens on Google's sign-in page. Sign in with the Google account that should own the registration."
  gcloud auth login --brief --quiet >/dev/null
  project="ai-workspace-$(LC_ALL=C tr -dc 'a-z0-9' </dev/urandom | head -c 6)"
  existing=$(gcloud projects list --filter="name:'AI Workspace'" --format="value(projectId)" --limit 1 2>/dev/null || true)
  if [[ -n $existing ]]; then project=$existing; echo "Using your existing project $project"
  else gcloud projects create "$project" --name="AI Workspace" --quiet; fi
  gcloud services enable gmail.googleapis.com calendar-json.googleapis.com --project "$project" --quiet
  say "Google needs two short pages done in its console (it has no command for these). They open now."
  cat <<EOF
  1. Branding page: App name "AI Workspace", your email as support and developer contact, Save.
     Audience page: External. Add your own Gmail address (and anyone else's) under Test users.
     To skip the 7-day sign-in expiry for a handful of people, press Publish app; Google then shows
     an "unverified app" screen with Advanced > Go to AI Workspace, for up to 100 people.
  2. Clients page: Create client > Application type "Desktop app" > name "AI Workspace" > Create.
     Copy the Client ID and Client secret it shows and paste them here.
EOF
  open "https://console.cloud.google.com/auth/branding?project=$project" 2>/dev/null || true
  sleep 2
  open "https://console.cloud.google.com/auth/clients/create?project=$project" 2>/dev/null || true
  read -r -p "Client ID: " G_ID
  read -r -s -p "Client secret: " G_SECRET; echo
  [[ $G_ID == *.apps.googleusercontent.com ]] || { echo "That doesn't look like a Google client ID (it ends in .apps.googleusercontent.com). Run again: scripts/setup-sign-in.sh google" >&2; G_ID=""; G_SECRET=""; }
}

case $which_ones in
  microsoft) microsoft ;;
  google) google ;;
  both) microsoft || true; google || true ;;  # one provider failing never loses the other's registration
  *) echo "usage: $0 [microsoft|google]" >&2; exit 2 ;;
esac

write() {  # $1 = path of oauth_clients.py
  [[ -f $1 ]] || return 0
  GOOGLE_OAUTH_CLIENT_ID=$G_ID GOOGLE_OAUTH_CLIENT_SECRET=$G_SECRET MICROSOFT_OAUTH_CLIENT_ID=$MS_ID OAUTH_CLIENTS_FILE=$1 \
    python3 "$here/scripts/write-oauth-clients.py"
}
write "$here/workbench/backend/app/oauth_clients.py"
write "$HOME/.ai-workspace/app/workbench/backend/app/oauth_clients.py"
say "Done. Restart the app (ai-workspace --stop, then ai-workspace) and click Sign in with Microsoft or Google on the Mail page."
echo "To give every install the same buttons, commit workbench/backend/app/oauth_clients.py."
