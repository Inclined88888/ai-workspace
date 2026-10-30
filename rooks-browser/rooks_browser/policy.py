"""What the agent may do without asking, and where it may go.

Two rules live here:

* Site policy: a per-site allow list and deny list (host patterns such as `example.com`, `*.example.com` or `*`).
  Deny wins. An empty allow list means "anywhere not denied". Checked for every page the browser loads (main frame and
  iframes), not just URLs the model types, so a redirect or a link cannot leave the allowed sites.
* Sensitive actions: submitting a form, purchases, sending messages, and logging in always need a yes from the user,
  (downloads do not: they are saved to the downloads folder, never opened, and listed in the receipt;
  `--ask download` makes them ask too)
  unless the user pre-approved that category for this run (`--approve submit,message`). Password fields are never filled
  by the agent: the user types them in the visible browser (headed mode), and the value is never seen or stored.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from fnmatch import fnmatch
from pathlib import Path
from urllib.parse import urlsplit

from .receipts import is_local

CATEGORIES = ("submit", "purchase", "message", "login", "download")
LABELS = {"submit": "submit a form", "purchase": "make a purchase", "message": "send a message", "login": "log in",
          "download": "download a file"}

_PURCHASE = re.compile(r"\b(buy|purchase|checkout|check out|place (?:your )?order|pay|payment|add to (?:cart|basket|bag)|"
                       r"order now|subscribe|donate|confirm order|book now|complete (?:order|purchase))\b", re.I)
_MESSAGE = re.compile(r"\b(send|post|reply|tweet|comment|publish|share|submit (?:comment|review|message)|message)\b", re.I)
_CARD = re.compile(r"\b(card ?number|credit card|debit card|cvv|cvc|security code|expiry|expiration|iban|account number|sort code|bsb)\b", re.I)
_LOGIN = re.compile(r"\b(log ?in|sign ?in|sign ?up|register|create account|authenticate|continue with (?:google|apple|microsoft))\b", re.I)


def host_of(url: str) -> str:
    return (urlsplit(url).hostname or "").lower()


def _match(host: str, pattern: str) -> bool:
    p = pattern.lower().strip()
    if "://" in p:
        p = host_of(p)
    p = p.split("/")[0].split(":")[0] if p != "*" else p
    if not p:
        return False
    if p == "*" or host == p or fnmatch(host, p):
        return True
    # "example.com" also covers its subdomains; "*.example.com" covers example.com itself
    bare = p[2:] if p.startswith("*.") else p
    return host == bare or host.endswith("." + bare)


@dataclass
class SitePolicy:
    allow: list[str] = field(default_factory=list)
    deny: list[str] = field(default_factory=list)
    block_private: bool = False  # service mode: no localhost, LAN or other containers


    def check(self, url: str) -> tuple[bool, str]:
        scheme = urlsplit(url).scheme.lower()
        if scheme in ("about", "data", "blob", "chrome-error"):
            return True, ""
        if scheme not in ("http", "https", "file"):
            return False, f"{scheme}: URLs are not allowed"
        host = host_of(url)
        if self.block_private and (scheme == "file" or is_local(host)):
            return False, f"{host or 'local files'}: private and local addresses are blocked in service mode"
        if scheme == "file":
            return (True, "") if not self.allow else (False, "local files are not on the allow list")
        for p in self.deny:
            if _match(host, p):
                return False, f"{host} is on the deny list ({p})"
        if self.allow and not any(_match(host, p) for p in self.allow):
            return False, f"{host} is not on the allow list ({', '.join(self.allow)})"
        return True, ""

    def merged(self, allow: list[str] | None = None, deny: list[str] | None = None) -> "SitePolicy":
        return SitePolicy([*self.allow, *(allow or [])], [*self.deny, *(deny or [])], self.block_private)

    # Persistent per-site lists (rooks-browser sites allow|deny|remove HOST)
    @classmethod
    def load(cls, path: Path) -> "SitePolicy":
        try:
            d = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return cls()
        return cls([str(x) for x in d.get("allow", [])], [str(x) for x in d.get("deny", [])])

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"allow": self.allow, "deny": self.deny}, indent=2) + "\n", encoding="utf-8")
        tmp.replace(path)


@dataclass
class Approval:
    """One sensitive action waiting for a yes or no."""
    id: str
    category: str
    action: str
    description: str
    url: str

    def to_dict(self) -> dict:
        return {"id": self.id, "category": self.category, "label": LABELS.get(self.category, self.category),
                "action": self.action, "description": self.description, "url": self.url}


def classify(action: str, el: dict | None, args: dict) -> str | None:
    """The sensitive category of an action, or None if it can run without asking.

    `el` is the element the action targets, as produced by the page snapshot: tag, role, type, name, in_form,
    form_has_password, form_action, is_submit."""
    if not el:
        return None
    name = " ".join(str(el.get(k) or "") for k in ("name", "value", "form_action"))
    if action == "type":
        if (el.get("type") or "").lower() == "password":
            return "login"
        if _CARD.search(el.get("name") or ""):
            return "purchase"
        if args.get("submit") and el.get("in_form"):
            return _form_category(el, name)
        return None
    if action in ("click", "select"):
        if _PURCHASE.search(name):
            return "purchase"
        if el.get("is_submit") and el.get("in_form"):
            return _form_category(el, name)
        if _LOGIN.search(name) and (el.get("form_has_password") or el.get("is_submit")):
            return "login"
        if _MESSAGE.search(el.get("name") or "") and (el.get("is_submit") or el.get("role") == "button"):
            return "message"
    return None


def _form_category(el: dict, name: str) -> str:
    if el.get("form_has_password") or _LOGIN.search(name):
        return "login"
    if _PURCHASE.search(name):
        return "purchase"
    if _MESSAGE.search(el.get("name") or ""):
        return "message"
    return "submit"


def parse_categories(text: str | None) -> set[str]:
    if not text:
        return set()
    out = {c.strip().lower() for c in text.split(",") if c.strip()}
    if "all" in out:
        return set(CATEGORIES) - {"login"}  # logging in is always the user's job
    bad = out - set(CATEGORIES)
    if bad:
        raise ValueError(f"unknown approval categories: {', '.join(sorted(bad))} (use {', '.join(CATEGORIES)})")
    return out
