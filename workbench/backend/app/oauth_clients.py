"""OAuth client IDs this build ships with, for one-click "Connect Google" and "Connect Microsoft".

These are public identifiers, not secrets: a desktop app cannot keep a secret, so security comes from PKCE and the
127.0.0.1 loopback redirect (RFC 8252). Google still issues a "client secret" for Desktop app clients and its token
endpoint requires it; Google's own docs say that for installed apps it "is obviously not treated as a secret".

Leave a value empty and the app shows a friendly "not set up in this build" message with the Other provider path.
How the maintainer creates them: docs/connect-google-microsoft.md. Override at run time with the environment variables
GOOGLE_OAUTH_CLIENT_ID, GOOGLE_OAUTH_CLIENT_SECRET, MICROSOFT_OAUTH_CLIENT_ID and MICROSOFT_OAUTH_TENANT, or per
install in Settings > Connect your mail & calendar > Use your own app registration. CI can fill this file from repository variables
with scripts/write-oauth-clients.py.
"""

GOOGLE_CLIENT_ID = ""
GOOGLE_CLIENT_SECRET = ""
MICROSOFT_CLIENT_ID = ""
MICROSOFT_TENANT = "common"
