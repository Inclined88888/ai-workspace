"""Server settings from an email address, for the "Other provider" (IMAP/SMTP + CalDAV) path.

Order: a built-in table of common providers (with their app-password steps), then Mozilla's ISPDB autoconfig database
(the one Thunderbird uses), then the domain's own autoconfig file, then the domain's MX records (looked up over DNS
over HTTPS) to spot Google Workspace and Microsoft 365, which should use one-click connect instead.

Online lookups can be turned off with MAIL_AUTODETECT_ONLINE=0 and pointed elsewhere for tests with ISPDB_URL and
DOH_URL.
"""

from __future__ import annotations

import os
import re
import xml.etree.ElementTree as ET

import httpx

KNOWN: dict[str, dict] = {
    "icloud": {
        "domains": ("icloud.com", "me.com", "mac.com"),
        "label": "iCloud Mail", "imap_host": "imap.mail.me.com", "imap_port": 993, "smtp_host": "smtp.mail.me.com",
        "smtp_port": 587, "smtp_security": "starttls", "caldav_url": "https://caldav.icloud.com",
        "app_password": True, "app_password_url": "https://account.apple.com/account/manage",
        "steps": ["Turn on two-factor authentication for your Apple Account if it isn't already.",
                  "Go to account.apple.com, sign in, then Sign-In and Security > App-Specific Passwords.",
                  "Click Generate an app-specific password, name it AI Workspace, and paste it here."],
        "username_hint": "Your full iCloud address. If that's refused, AI Workspace retries with the part before the @.",
    },
    "yahoo": {
        "domains": ("yahoo.com", "ymail.com", "rocketmail.com", "yahoo.co.uk", "yahoo.com.au", "yahoo.ca", "yahoo.fr", "yahoo.de"),
        "label": "Yahoo Mail", "imap_host": "imap.mail.yahoo.com", "imap_port": 993, "smtp_host": "smtp.mail.yahoo.com",
        "smtp_port": 465, "smtp_security": "ssl", "caldav_url": "https://caldav.calendar.yahoo.com",
        "app_password": True, "app_password_url": "https://login.yahoo.com/account/security",
        "steps": ["Open login.yahoo.com/account/security (Account Security).",
                  "Click Generate app password (or Manage app passwords), name it AI Workspace.",
                  "Copy the 16-character password and paste it here. Your normal Yahoo password won't work."],
    },
    "aol": {
        "domains": ("aol.com", "aim.com"),
        "label": "AOL Mail", "imap_host": "imap.aol.com", "imap_port": 993, "smtp_host": "smtp.aol.com", "smtp_port": 465,
        "smtp_security": "ssl", "app_password": True, "app_password_url": "https://login.aol.com/account/security",
        "steps": ["Open login.aol.com/account/security.", "Generate an app password and paste it here."],
    },
    "fastmail": {
        "domains": ("fastmail.com", "fastmail.fm", "messagingengine.com"),
        "label": "Fastmail", "imap_host": "imap.fastmail.com", "imap_port": 993, "smtp_host": "smtp.fastmail.com",
        "smtp_port": 465, "smtp_security": "ssl", "caldav_url": "https://caldav.fastmail.com/dav/",
        "app_password": True, "app_password_url": "https://app.fastmail.com/settings/security/apps",
        "steps": ["In Fastmail open Settings > Privacy & Security > Manage app passwords and access.",
                  "Create a new app password with IMAP, SMTP and CalDAV access and paste it here."],
    },
    "zoho": {
        "domains": ("zoho.com", "zohomail.com"),
        "label": "Zoho Mail", "imap_host": "imap.zoho.com", "imap_port": 993, "smtp_host": "smtp.zoho.com", "smtp_port": 465,
        "smtp_security": "ssl", "caldav_url": "https://calendar.zoho.com/caldav/", "app_password": True,
        "app_password_url": "https://accounts.zoho.com/home#security/app_password",
        "steps": ["Turn on IMAP in Zoho Mail settings.", "Create an application-specific password in Zoho Accounts > Security."],
    },
    "gmx": {
        "domains": ("gmx.com", "gmx.net", "gmx.de"),
        "label": "GMX", "imap_host": "imap.gmx.com", "imap_port": 993, "smtp_host": "mail.gmx.com", "smtp_port": 587,
        "smtp_security": "starttls", "app_password": False,
        "steps": ["Turn on POP3/IMAP access in GMX settings first."],
    },
    "proton": {
        "domains": ("proton.me", "protonmail.com", "pm.me"),
        "label": "Proton Mail (via Proton Mail Bridge)", "imap_host": "127.0.0.1", "imap_port": 1143, "smtp_host": "127.0.0.1",
        "smtp_port": 1025, "smtp_security": "starttls", "app_password": True,
        "steps": ["Proton encrypts mail, so IMAP only works through the Proton Mail Bridge app on this computer (paid plans).",
                  "Install and sign in to Bridge, then copy the IMAP username and the Bridge password it shows."],
    },
}

ONE_CLICK = {
    "google": ("gmail.com", "googlemail.com"),
    "microsoft": ("outlook.com", "hotmail.com", "live.com", "msn.com", "outlook.com.au", "hotmail.co.uk", "live.com.au", "passport.com"),
}


def _online() -> bool:
    return os.getenv("MAIL_AUTODETECT_ONLINE", "1").strip() not in ("0", "false", "no")


def _ispdb(domain: str) -> dict | None:
    urls = [f"{os.getenv('ISPDB_URL', 'https://autoconfig.thunderbird.net/v1.1').rstrip('/')}/{domain}"]
    if not os.getenv("ISPDB_URL"):
        urls.append(f"https://autoconfig.{domain}/mail/config-v1.1.xml")
    for url in urls:
        try:
            r = httpx.get(url, timeout=6, follow_redirects=True)
        except httpx.HTTPError:
            continue
        if r.status_code != 200:
            continue
        try:
            root = ET.fromstring(r.content)
        except ET.ParseError:
            continue
        imap = root.find(".//incomingServer[@type='imap']")
        smtp = root.find(".//outgoingServer[@type='smtp']")
        if imap is None or smtp is None:
            continue
        sock = (smtp.findtext("socketType") or "SSL").upper()
        return {"label": root.findtext(".//displayName") or domain, "imap_host": imap.findtext("hostname", ""),
                "imap_port": int(imap.findtext("port") or 993), "smtp_host": smtp.findtext("hostname", ""),
                "smtp_port": int(smtp.findtext("port") or 465), "smtp_security": "starttls" if sock == "STARTTLS" else "ssl",
                "source": "ispdb"}
    return None


def _mx(domain: str) -> list[str]:
    try:
        r = httpx.get(os.getenv("DOH_URL", "https://cloudflare-dns.com/dns-query"), params={"name": domain, "type": "MX"},
                      headers={"Accept": "application/dns-json"}, timeout=5)
        return [a.get("data", "").lower() for a in r.json().get("Answer") or []]
    except (httpx.HTTPError, ValueError):
        return []


def detect(email: str) -> dict:
    """What to show for an address: one-click provider, known settings with app-password steps, or best guesses."""
    email = email.strip()
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        return {"kind": "invalid", "message": "Enter a full email address."}
    domain = email.split("@", 1)[1].lower()
    for provider, domains in ONE_CLICK.items():
        if domain in domains:
            return {"kind": "one_click", "provider": provider, "domain": domain}
    for key, k in KNOWN.items():
        if domain in k["domains"]:
            return {"kind": "known", "key": key, "domain": domain, "username": email,
                    **{f: v for f, v in k.items() if f != "domains"}, "source": "built-in"}
    if _online():
        mx = _mx(domain)
        if any("google.com" in m or "googlemail.com" in m for m in mx):
            return {"kind": "one_click", "provider": "google", "domain": domain, "workspace": True}
        if any("protection.outlook.com" in m for m in mx):
            return {"kind": "one_click", "provider": "microsoft", "domain": domain, "workspace": True}
        found = _ispdb(domain)
        if found:
            return {"kind": "known", "domain": domain, "username": email, "app_password": False,
                    "steps": ["Use your mail password, or an app password if your provider offers one."], **found}
    return {"kind": "guess", "domain": domain, "username": email, "label": domain, "imap_host": f"imap.{domain}", "imap_port": 993,
            "smtp_host": f"smtp.{domain}", "smtp_port": 465, "smtp_security": "ssl", "app_password": False, "source": "guess",
            "steps": ["These are common defaults. Check your provider's help page for its IMAP and SMTP settings."]}
