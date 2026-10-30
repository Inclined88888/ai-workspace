"""Security checks for third-party skills (GitHub hub): static code scan, prompt-injection scan, permissions, risk.

Everything here works offline on a folder that has already been fetched. Optional external scanners (bandit, semgrep,
gitleaks, osv-scanner) are used when they are on PATH; the built-in rules always run, so a machine without them still
blocks the known-bad patterns. Findings are plain dicts so they serialise straight into the report the user reads.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import yaml

SEVERITY_ORDER = {"info": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}
TEXT_EXT = {".md", ".txt", ".py", ".sh", ".bash", ".zsh", ".js", ".mjs", ".cjs", ".ts", ".json", ".toml", ".yaml", ".yml",
            ".ps1", ".bat", ".cmd", ".rb", ".pl", ".lua", ".go", ".rs", ".html", ".cfg", ".ini", ".env", ""}
CODE_EXT = TEXT_EXT - {".md", ".txt"}
MAX_FILE = 512 * 1024
BINARY_MAGIC = {b"\x7fELF": "Linux executable", b"MZ": "Windows executable", b"\xcf\xfa\xed\xfe": "macOS executable",
                b"\xfe\xed\xfa\xcf": "macOS executable", b"\xca\xfe\xba\xbe": "macOS universal binary"}


@dataclass(frozen=True)
class Rule:
    id: str
    severity: str
    category: str
    pattern: re.Pattern
    message: str            # plain words, shown to the user


def _r(id_, sev, cat, rx, msg, flags=re.I):
    return Rule(id_, sev, cat, re.compile(rx, flags), msg)


CODE_RULES = [
    _r("pipe-to-shell", "critical", "remote code", r"\b(curl|wget|iwr|Invoke-WebRequest)\b[^\n|]{0,300}\|\s*(sudo\s+)?(ba|z|da)?sh\b",
       "Downloads a script from the internet and runs it straight away"),
    _r("download-cradle", "critical", "remote code", r"\b(IEX|Invoke-Expression)\b|DownloadString\(|-EncodedCommand\b",
       "PowerShell download-and-run pattern"),
    _r("reverse-shell", "critical", "remote control", r"/dev/tcp/|\bnc\s+(-\w+\s+)*-e\b|bash\s+-i\s*>&|socat\s+exec:",
       "Opens a remote shell that lets someone else control this computer"),
    _r("decode-and-run", "critical", "obfuscation",
       r"(base64\s+(-d|--decode)[^\n]{0,120}\|\s*(ba)?sh)|((eval|exec)\s*\([^\n]{0,80}(b64decode|base64|atob|codecs\.decode|zlib\.decompress|marshal\.loads))",
       "Decodes hidden code and runs it"),
    _r("destructive-delete", "critical", "destructive", r"\brm\s+-[a-z]*r[a-z]*f?\s+(--no-preserve-root\s+)?(/|~|\$HOME|\"\$HOME\")(\s|$|/\s|\")",
       "Deletes your home folder or the whole disk"),
    _r("crypto-miner", "critical", "abuse", r"xmrig|stratum\+tcp|coinhive|cryptonight", "Cryptocurrency mining"),
    _r("exfil-endpoint", "high", "data exfiltration",
       r"discord(app)?\.com/api/webhooks|hooks\.slack\.com/services|pastebin\.com|webhook\.site|requestbin|pipedream\.net|"
       r"ngrok\.io|ngrok-free\.app|transfer\.sh|api\.telegram\.org/bot|interact\.sh|burpcollaborator|oast\.(fun|me|pro)",
       "Sends data to a drop box or webhook service often used to steal data"),
    _r("secret-files", "high", "credentials",
       r"\.ssh/|id_rsa|id_ed25519|\.aws/credentials|\.netrc\b|\.npmrc|\.pypirc|\.docker/config\.json|\.config/gh/|\.kube/config|"
       r"Login Data|Cookies\.binarycookies|find-generic-password|Keychains/|wallet\.dat|\.gnupg",
       "Reads files where passwords, keys or browser logins are kept"),
    _r("env-dump", "high", "credentials",
       r"(json\.dumps|str|dict|print|send|post)\s*\(\s*(dict\()?os\.environ\b\s*\)?|JSON\.stringify\(\s*process\.env\s*\)|\bprintenv\b|\benv\s*>\s*\S",
       "Collects every environment variable, which usually includes API keys"),
    _r("persistence", "high", "persistence",
       r"crontab\s+-|LaunchAgents|LaunchDaemons|\.bashrc|\.zshrc|\.bash_profile|\.profile\b|authorized_keys|systemctl\s+enable|"
       r"CurrentVersion\\\\Run|schtasks\s+/create|Startup\\\\",
       "Changes start-up files so it keeps running after you close it"),
    _r("privilege", "high", "system", r"\bsudo\s|\bchmod\s+(-R\s+)?[0-7]?777\b|spctl\s+--master-disable|Set-MpPreference|csrutil\s+disable|setenforce\s+0",
       "Asks for administrator rights or switches off system protection"),
    _r("write-outside", "high", "filesystem",
       r"open\(\s*[rfb]?['\"](/|~|C:\\\\)[^'\"]*['\"]\s*,\s*['\"][wax]|writeFileSync\(\s*['\"`](/|~)|>\s*(~|/etc|/usr|\$HOME)/",
       "Writes files outside its own folder"),
    _r("shell-exec", "medium", "shell", r"\bos\.system\(|\bsubprocess\.|shell\s*=\s*True|child_process|\bexecSync\(|\bspawn\(|Runtime\.getRuntime\(\)\.exec|\bos\.popen\(",
       "Runs other programs or shell commands"),
    _r("dynamic-code", "medium", "dynamic code", r"\beval\s*\(|\bexec\s*\(|new\s+Function\s*\(|__import__\s*\(|importlib\.import_module\(|\bcompile\s*\(",
       "Builds and runs code at run time, which hides what it really does"),
    _r("network", "medium", "network",
       r"\b(requests|httpx|aiohttp|urllib(\.request)?|http\.client|socket)\.\w+\(|\bfetch\s*\(|\baxios\b|XMLHttpRequest|\bcurl\s|\bwget\s|Invoke-WebRequest",
       "Talks to the internet"),
    _r("long-base64", "medium", "obfuscation", r"['\"][A-Za-z0-9+/]{240,}={0,2}['\"]", "Contains a long encoded blob", 0),
    _r("hex-escapes", "medium", "obfuscation", r"(\\x[0-9a-fA-F]{2}){24,}", "Contains a long run of hex-escaped bytes"),
    _r("char-codes", "medium", "obfuscation", r"(chr\(\d+\)\s*\+\s*){6,}|fromCharCode\((\s*\d+\s*,){10,}", "Builds strings from character codes to hide them"),
    _r("install-hook", "medium", "install script", r"\"(pre|post)?install\"\s*:|cmdclass\s*=\s*\{[^}]*install", "Runs code automatically when its packages install"),
]

RULES_BY_ID = {r.id: r for r in CODE_RULES}

INJECTION_RULES = [
    _r("ignore-instructions", "critical", "prompt injection",
       r"\b(ignore|disregard|forget|override)\b[^.\n]{0,40}\b(all|any|every|previous|prior|above|earlier|system|developer|safety)\b[^.\n]{0,30}\b(instructions?|prompts?|rules?|guidelines?|messages?)",
       "Tells the assistant to ignore its rules"),
    _r("secrecy", "high", "prompt injection",
       r"\b(do\s*n[o']t|never|without)\s+(tell|telling|inform|informing|ask|asking|mention|mentioning|notify|notifying|show|showing)\w*\s+(the\s+)?(user|anyone|human|owner)|\bsilently\b|\bsecretly\b|\bcovertly\b|without\s+(the\s+)?user'?s?\s+(knowledge|consent|approval)",
       "Tells the assistant to hide what it does from you"),
    _r("credential-grab", "high", "prompt injection",
       r"\b(reveal|print|output|send|post|upload|exfiltrate|leak|share|include|copy)\b[^.\n]{0,60}\b(system\s+prompt|api[\s_-]?keys?|secrets?|credentials?|passwords?|tokens?|\.env\b|ssh\s+keys?|private\s+keys?|cookies)",
       "Asks the assistant to send out your keys, passwords or private data"),
    _r("approval-bypass", "high", "prompt injection",
       r"auto-?approve|always\s+approve|bypass\s+(the\s+)?(approval|confirmation|permission|sandbox|security)|dangerously-skip-permissions|--yolo\b|disable\s+(the\s+)?(sandbox|safety|guardrails?)",
       "Tries to switch off approvals or the sandbox"),
    _r("role-hijack", "medium", "prompt injection",
       r"\byou\s+are\s+now\b|\bnew\s+(system\s+)?instructions\b|\bact\s+as\s+(DAN|root|an?\s+unrestricted)|\bjailbreak|\bdeveloper\s+mode\b|</?(system|assistant)>",
       "Tries to give the assistant a new identity or new rules"),
    _r("hidden-chars", "high", "hidden text", r"[\u200b-\u200f\u2060-\u2064\ufeff\u202a-\u202e\u2066-\u2069\U000e0000-\U000e007f]",
       "Contains invisible characters that can hide instructions", 0),
    _r("image-beacon", "medium", "data exfiltration", r"!\[[^\]]*\]\(https?://[^)\s]*\?[^)\s]*=[^)\s]*\)",
       "Embeds an image link with parameters, a known way to leak data when the text is rendered"),
]

HIDDEN_COMMENT = re.compile(r"<!--([\s\S]*?)-->")
IMPERATIVE = re.compile(r"\b(ignore|run|execute|send|upload|delete|curl|wget|assistant|you must|do not tell|always)\b", re.I)
URL = re.compile(r"https?://([a-zA-Z0-9.-]+\.[a-zA-Z]{2,})(?::\d+)?")
ENV_READ = re.compile(r"os\.environ(?:\.get)?\(\s*['\"]([A-Z0-9_]+)|os\.environ\[\s*['\"]([A-Z0-9_]+)|os\.getenv\(\s*['\"]([A-Z0-9_]+)|process\.env\.([A-Z0-9_]+)|\$\{?([A-Z][A-Z0-9_]{2,})\}?")
ABS_PATH = re.compile(r"['\"`](~/[^'\"`\s]+|/(?:etc|usr|var|opt|Users|home|Library|tmp)/[^'\"`\s]*|[A-Z]:\\\\[^'\"`]+)['\"`]")
HARMLESS_DOMAINS = {"example.com", "example.org", "localhost", "schemas.openxmlformats.org", "www.w3.org", "json-schema.org",
                    "github.com", "docs.github.com", "opensource.org", "www.apache.org", "spdx.org", "semver.org"}
SHELL_VARS = {"HOME", "PATH", "PWD", "USER", "SHELL", "TMPDIR", "LANG", "TERM", "BASH_SOURCE", "SKILL_DIR"}

# Known-bad or typosquatted package names (a short, offline list; osv-scanner is used too when installed).
BAD_PACKAGES = {
    "python": {"reqeusts", "requessts", "request", "python3-dateutil", "jeIlyfish", "colourama", "urlib3", "djanga",
               "python-sqlite", "setup-tools", "pytorch-nightly-cpu", "ctx", "noblesse", "pymafka", "crypt"},
    "node": {"crossenv", "cross-env.js", "event-stream@3.3.6", "flatmap-stream", "ua-parser-js@0.7.29", "coa@2.0.3",
             "rc@1.2.9", "node-ipc@10.1.1", "colors@1.4.1", "faker@6.6.6", "discord.dll", "electorn", "loadyaml"},
}


def finding(rule: Rule | None, file: str, line: int, snippet: str, **kw) -> dict:
    base = {"rule": rule.id, "severity": rule.severity, "category": rule.category, "message": rule.message} if rule else {}
    base.update({"file": file, "line": line, "snippet": snippet.strip()[:200]})
    base.update(kw)
    return base


def iter_files(root: Path):
    for p in sorted(root.rglob("*")):
        if p.is_file() and ".git" not in p.relative_to(root).parts:
            yield p


def read_text(p: Path) -> str | None:
    try:
        data = p.read_bytes()[:MAX_FILE]
    except OSError:
        return None
    if b"\x00" in data[:4096]:
        return None
    return data.decode("utf-8", errors="replace")


def parse_skill_md(text: str) -> tuple[dict, str]:
    """SKILL.md: YAML frontmatter between --- lines (name, description, optional allowed-tools / permissions), then Markdown."""
    m = re.match(r"^\ufeff?---\s*\n(.*?)\n---\s*\n?(.*)$", text, re.S)
    if not m:
        return {}, text
    try:
        meta = yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError:
        meta = {}
    return (meta if isinstance(meta, dict) else {}), m.group(2)


# ---------------------------------------------------------------------------------------------------------- static
def static_scan(root: Path) -> list[dict]:
    out: list[dict] = []
    for p in iter_files(root):
        rel = str(p.relative_to(root))
        head = p.read_bytes()[:4]
        kind = next((v for k, v in BINARY_MAGIC.items() if head.startswith(k)), None)
        if kind:
            out.append({"rule": "compiled-binary", "severity": "high", "category": "unreviewable",
                        "message": f"Ships a compiled program ({kind}) that cannot be reviewed", "file": rel, "line": 0, "snippet": ""})
            continue
        if p.suffix.lower() not in CODE_EXT and not _has_shebang(p):
            continue
        text = read_text(p)
        if text is None:
            continue
        for i, line in enumerate(text.splitlines(), 1):
            for rule in CODE_RULES:
                if rule.pattern.search(line):
                    out.append(finding(rule, rel, i, line))
        out += _deps(p, rel, text)
    return out + external_scanners(root)


def _has_shebang(p: Path) -> bool:
    try:
        return p.read_bytes()[:2] == b"#!"
    except OSError:
        return False


def _deps(p: Path, rel: str, text: str) -> list[dict]:
    out = []
    name = p.name.lower()
    if name.startswith("requirements") and name.endswith(".txt"):
        for i, line in enumerate(text.splitlines(), 1):
            pkg = re.split(r"[<>=!~\[;\s]", line.strip(), maxsplit=1)[0].lower()
            if not pkg or pkg.startswith("#"):
                continue
            if pkg.startswith(("git+", "http", "-e", "--")) or "@" in line and "://" in line:
                out.append({"rule": "url-dependency", "severity": "medium", "category": "dependencies",
                            "message": "Installs a package straight from a URL instead of the package index", "file": rel, "line": i, "snippet": line})
            elif pkg in BAD_PACKAGES["python"]:
                out.append({"rule": "bad-package", "severity": "high", "category": "dependencies",
                            "message": f"Depends on '{pkg}', a known malicious or typo-squatted package", "file": rel, "line": i, "snippet": line})
    if name == "package.json":
        try:
            pkg = json.loads(text)
        except ValueError:
            return out
        for section in ("dependencies", "devDependencies", "optionalDependencies"):
            for dep, ver in (pkg.get(section) or {}).items():
                key, keyv = dep.lower(), f"{dep.lower()}@{str(ver).lstrip('^~=')}"
                if key in BAD_PACKAGES["node"] or keyv in BAD_PACKAGES["node"]:
                    out.append({"rule": "bad-package", "severity": "high", "category": "dependencies",
                                "message": f"Depends on '{dep}', a known malicious or typo-squatted package", "file": rel, "line": 0, "snippet": f"{dep}: {ver}"})
                elif re.match(r"^(git\+|https?:|github:|file:)", str(ver)):
                    out.append({"rule": "url-dependency", "severity": "medium", "category": "dependencies",
                                "message": "Installs a package straight from a URL instead of the registry", "file": rel, "line": 0, "snippet": f"{dep}: {ver}"})
    return out


def external_scanners(root: Path) -> list[dict]:
    """Run whichever well-known scanners are installed. Each is optional and time-boxed; failures are reported as info."""
    out: list[dict] = []
    if shutil.which("bandit"):
        try:
            r = subprocess.run(["bandit", "-r", "-q", "-f", "json", str(root)], capture_output=True, text=True, timeout=60)
            for res in (json.loads(r.stdout or "{}").get("results") or []):
                sev = {"HIGH": "high", "MEDIUM": "medium"}.get(res.get("issue_severity"), "low")
                out.append({"rule": f"bandit:{res.get('test_id')}", "severity": sev, "category": "bandit", "message": res.get("issue_text", ""),
                            "file": str(Path(res["filename"]).relative_to(root)), "line": res.get("line_number", 0), "snippet": (res.get("code") or "")[:200]})
        except (subprocess.SubprocessError, ValueError, OSError) as e:
            out.append({"rule": "bandit-error", "severity": "info", "category": "scanner", "message": f"bandit did not finish: {e}", "file": "", "line": 0, "snippet": ""})
    if shutil.which("gitleaks"):
        try:
            r = subprocess.run(["gitleaks", "detect", "--no-git", "-s", str(root), "-f", "json", "-r", "/dev/stdout", "--no-banner", "--exit-code", "0"],
                               capture_output=True, text=True, timeout=60)
            for res in json.loads(r.stdout or "[]"):
                out.append({"rule": f"gitleaks:{res.get('RuleID')}", "severity": "high", "category": "credentials",
                            "message": "Contains what looks like a real secret or API key", "file": str(res.get("File", "")).replace(str(root) + "/", ""),
                            "line": res.get("StartLine", 0), "snippet": "[redacted]"})
        except (subprocess.SubprocessError, ValueError, OSError):
            pass
    if shutil.which("semgrep"):
        try:
            r = subprocess.run(["semgrep", "scan", "--config", "p/secrets", "--config", "p/command-injection", "--json", "--quiet", "--metrics", "off", str(root)],
                               capture_output=True, text=True, timeout=120)
            for res in (json.loads(r.stdout or "{}").get("results") or []):
                sev = {"ERROR": "high", "WARNING": "medium"}.get(res.get("extra", {}).get("severity"), "low")
                out.append({"rule": f"semgrep:{res.get('check_id', '').split('.')[-1]}", "severity": sev, "category": "semgrep",
                            "message": res.get("extra", {}).get("message", "")[:200], "file": str(Path(res["path"]).relative_to(root)),
                            "line": res.get("start", {}).get("line", 0), "snippet": ""})
        except (subprocess.SubprocessError, ValueError, OSError):
            pass
    if shutil.which("osv-scanner"):
        try:
            r = subprocess.run(["osv-scanner", "--format", "json", "-r", str(root)], capture_output=True, text=True, timeout=120)
            for res in (json.loads(r.stdout or "{}").get("results") or []):
                for pkg in res.get("packages") or []:
                    ids = [v.get("id") for v in pkg.get("vulnerabilities") or []]
                    if ids:
                        out.append({"rule": "osv", "severity": "high", "category": "dependencies",
                                    "message": f"{pkg['package']['name']} {pkg['package'].get('version', '')} has known vulnerabilities: {', '.join(ids[:5])}",
                                    "file": res.get("source", {}).get("path", "").replace(str(root) + "/", ""), "line": 0, "snippet": ""})
        except (subprocess.SubprocessError, ValueError, OSError):
            pass
    return out


def scanners_available() -> list[str]:
    return ["built-in rules"] + [s for s in ("bandit", "semgrep", "gitleaks", "osv-scanner") if shutil.which(s)]


# ---------------------------------------------------------------------------------------------------- injection
def injection_scan(root: Path) -> list[dict]:
    """Instruction text is what the model reads, so it is scanned like code: every Markdown/text file plus descriptions."""
    out: list[dict] = []
    for p in iter_files(root):
        if p.suffix.lower() not in {".md", ".txt", ".mdx", ".rst", ""} and p.name != "SKILL.md":
            continue
        text = read_text(p)
        if text is None:
            continue
        rel = str(p.relative_to(root))
        for i, line in enumerate(text.splitlines(), 1):
            for rule in INJECTION_RULES:
                if rule.pattern.search(line):
                    out.append(finding(rule, rel, i, line.encode("unicode_escape").decode() if rule.id == "hidden-chars" else line))
        for m in HIDDEN_COMMENT.finditer(text):
            if IMPERATIVE.search(m.group(1)):
                line = text.count("\n", 0, m.start()) + 1
                out.append({"rule": "hidden-comment", "severity": "high", "category": "hidden text",
                            "message": "Hides instructions inside an HTML comment you would not see", "file": rel, "line": line, "snippet": m.group(1)[:200]})
    return out


# -------------------------------------------------------------------------------------------------- permissions
def permissions(root: Path, meta: dict, mcp: dict | None = None) -> dict:
    """What the skill asks for (declared) and what its files show it would use (inferred), in one manifest."""
    declared = meta.get("permissions") if isinstance(meta.get("permissions"), dict) else {}
    domains, paths, env, shell, scripts = set(), set(), set(), False, []
    for p in iter_files(root):
        rel = str(p.relative_to(root))
        text = read_text(p)
        if text is None:
            continue
        is_code = p.suffix.lower() in CODE_EXT - {".json", ".toml", ".yaml", ".yml"} or _has_shebang(p)
        if is_code and p.suffix.lower() in {".py", ".sh", ".bash", ".js", ".mjs", ".cjs", ".ts", ".ps1", ""}:
            scripts.append(rel)
        if is_code:
            domains |= {d.lower() for d in URL.findall(text)}
            paths |= {m for m in ABS_PATH.findall(text)}
            for groups in ENV_READ.findall(text):
                name = next((g for g in groups if g), "")
                if name and name not in SHELL_VARS:
                    env.add(name)
            if RULES_BY_ID["shell-exec"].pattern.search(text) or p.suffix in {".sh", ".bash", ".ps1"}:
                shell = True
    domains -= HARMLESS_DOMAINS
    tools = meta.get("allowed-tools") or meta.get("allowed_tools") or meta.get("tools") or []
    if isinstance(tools, str):
        tools = [t.strip() for t in re.split(r"[,\s]+", tools) if t.strip()]
    if mcp:
        shell = True
        env |= set((mcp.get("env") or {}).keys())
    return {
        "network": sorted(set(declared.get("network") or []) | domains),
        "files": sorted(set(declared.get("files") or []) | paths),
        "env": sorted(set(declared.get("env") or []) | env),
        "shell": bool(declared.get("shell")) or shell,
        "tools": sorted({str(t) for t in tools}),
        "scripts": sorted(scripts),
        "declared": declared,
        "undeclared": sorted(
            ([f"network: {d}" for d in domains - set(declared.get("network") or [])] if declared else [])
            + ([f"files: {f}" for f in paths - set(declared.get("files") or [])] if declared else [])),
    }


def describe_permissions(perms: dict) -> list[str]:
    """Plain-words lines for the approval screen."""
    lines = []
    lines.append("Connect to: " + ", ".join(perms["network"]) if perms["network"] else "No internet access needed")
    lines.append("Read or write outside its folder: " + ", ".join(perms["files"]) if perms["files"] else "Only touches its own folder")
    lines.append("Run programs and shell commands" if perms["shell"] else "Does not run other programs")
    if perms["env"]:
        lines.append("Read settings/keys from: " + ", ".join(perms["env"]))
    if perms["tools"]:
        lines.append("Use assistant tools: " + ", ".join(perms["tools"]))
    if perms["scripts"]:
        lines.append(f"Includes {len(perms['scripts'])} helper script(s): " + ", ".join(perms["scripts"][:6]))
    return lines


# --------------------------------------------------------------------------------------------------------- risk
def risk(static: list[dict], injection: list[dict], perms: dict, sandbox: dict | None = None) -> dict:
    """Low / Medium / High with the reasons that decided it. Anything critical, or any high-severity injection, is High."""
    reasons: list[str] = []
    level = 0
    def bump(to: int, why: str):
        nonlocal level
        level = max(level, to)
        if why not in reasons:
            reasons.append(why)
    allf = static + injection
    for f in allf:
        if f["severity"] == "critical":
            bump(2, f"{f['message']} ({f['file']}:{f['line']})")
    highs = [f for f in allf if f["severity"] == "high"]
    for f in highs:
        if f["category"] in ("prompt injection", "hidden text", "data exfiltration", "remote code", "credentials"):
            bump(2, f"{f['message']} ({f['file']}:{f['line']})")
    if len(highs) >= 2:
        bump(2, f"{len(highs)} high-severity findings")
    elif highs:
        bump(1, f"{highs[0]['message']} ({highs[0]['file']}:{highs[0]['line']})")
    mediums = [f for f in allf if f["severity"] == "medium"]
    if any(f["category"] == "obfuscation" for f in mediums):
        bump(1, "Contains obfuscated or encoded content")
    if len(mediums) >= 4:
        bump(1, f"{len(mediums)} medium-severity findings")
    if perms["network"] and perms["shell"]:
        bump(1, "Can both run commands and reach the internet")
    elif perms["network"]:
        bump(1, "Reaches the internet: " + ", ".join(perms["network"][:4]))
    if perms["undeclared"]:
        bump(1, "Uses access it does not declare: " + ", ".join(perms["undeclared"][:4]))
    if sandbox:
        for v in sandbox.get("violations", []):
            bump(2 if v["kind"] in ("network", "secret-read", "write-outside", "persistence") else 1, f"Trial run tried to {v['what']}")
        if sandbox.get("status") == "timeout":
            bump(1, "Trial run did not finish in time")
    if not reasons:
        reasons.append("No risky patterns found; it only contains instructions" if not perms["scripts"] else "No risky patterns found in its scripts")
    return {"level": ["low", "medium", "high"][level], "reasons": reasons,
            "counts": {s: sum(1 for f in allf if f["severity"] == s) for s in ("critical", "high", "medium", "low")}}
