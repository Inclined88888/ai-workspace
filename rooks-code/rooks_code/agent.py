"""The agent loop: send the conversation to the model, run the tools it asks for (after approval), feed the results
back, and repeat until it answers without asking for a tool, hits the step limit, or the budget's hard cap."""

from __future__ import annotations

import json
import platform
import re
import time
from pathlib import Path

from . import providers
from .mcp import MCPError, MCPManager
from .receipts import RunLog
from .tools import SPECS, Plan, Toolbox, ToolError
from .undo import ChangeSets

SYSTEM = """You are Rooks Code, a careful coding agent working in the user's project through tools.
Project root: {root}
Operating system: {os}
Files and folders at the project root: {listing}

How to work:
- Investigate first: list_files, search and read_file before changing anything. Never guess file contents.
- Change files with edit_file (exact text replacement) or write_file. Keep changes minimal and in the project's style.
- Use run_command to run tests, builds and other CLIs, and to check your work. Commands run in the project root.
- The user approves edits and commands; if one is denied, do not retry it, ask or take another approach.
- When you decide to do something, call the tool in that same reply; do not just describe it.
- When the task is done, stop calling tools and reply with a short summary of what changed and how you checked it.
  Only claim what the tool results show."""


# Smaller local models often announce a step ("Now I'll run the tests.") and stop without calling a tool. When a reply
# ends like that, Rooks asks once more (at most NUDGES times per prompt) instead of ending the turn. Off with nudge = false.
_ANNOUNCES = re.compile(r"\b(I'll|I will|I am going to|I'm going to|Let's|Let me|Next,? I|Now,? I|First,? I|This will|"
                        r"We'll|We will)\b",
                        re.I)
NUDGE = ("You described a next step but did not call a tool. Call the tool now. If the task is already finished, reply "
         "with your final summary instead.")
NUDGES = 2


def announces_step(text: str) -> bool:
    """True when a reply's last lines promise an action ("Next, I'll run the tests:") rather than finish or ask."""
    tail = re.sub(r"(?i)let me know", "", text.strip())[-400:]
    if not tail or tail.endswith("?") or re.search(r"(?i)\b(summary|summari[sz]e|in short|all done)\b", tail):
        return False
    lines = [x for x in tail.splitlines() if x.strip()]
    return tail.endswith(":") or any(_ANNOUNCES.search(x) for x in lines[-2:])


class Budget:
    def __init__(self, max_tokens: int | None, max_usd: float | None, model: str, prices: dict, local: bool):
        self.max_tokens, self.max_usd = max_tokens, max_usd
        self.price = providers.price(model, prices, local)

    def check(self, totals: dict, next_input_estimate: int) -> str:
        """'' when the next call fits, else the reason it would cross a hard cap."""
        used = totals["tokens_in"] + totals["tokens_out"]
        if self.max_tokens and used + next_input_estimate > self.max_tokens:
            return f"token cap reached: {used:,} used, next call needs about {next_input_estimate:,}, cap {self.max_tokens:,}"
        if self.max_usd and self.price is not None:
            nxt = next_input_estimate * self.price[0] / 1_000_000
            if totals["usd"] + nxt > self.max_usd:
                return f"cost cap reached: ${totals['usd']:.4f} spent, next call about ${nxt:.4f}, cap ${self.max_usd:.2f}"
        return ""


class Agent:
    def __init__(self, settings, ui, model=None, log: RunLog | None = None):
        self.s, self.ui = settings, ui
        self.root: Path = settings.root.resolve()
        self.log = log or RunLog(self.root, provider=settings.provider, model=settings.model)
        self.model = model or providers.make(settings, on_egress=self._egress)
        self.tools = Toolbox(self.root, settings.command_timeout)
        self.changes = ChangeSets(self.root)
        self.mcp = MCPManager(settings.mcp_servers, str(self.root), warn=ui.warn) if settings.mcp_servers else None
        self.messages: list[dict] = []
        self.always: set[str] = set()  # effects approved for the rest of the session ("a" at the prompt)
        self.budget = Budget(settings.max_tokens, settings.max_usd, settings.model, settings.prices, settings.local)
        self.system = SYSTEM.format(root=self.root, os=f"{platform.system()} {platform.release()}", listing=_listing(self.root))
        self._turn: int | None = None
        if settings.max_usd and self.budget.price is None:
            ui.warn(f"price of {settings.model} is unknown, so the ${settings.max_usd} cap cannot be enforced; the token cap "
                    f"({settings.max_tokens or 'none'}) still is. Add it under [prices] to enforce a dollar cap.")

    # receipts --------------------------------------------------------------------------------------------------
    def _egress(self, method: str, url: str, outcome: str, ms: int, error: str) -> None:
        from urllib.parse import urlsplit
        u = urlsplit(url)
        host = u.hostname or ""
        self.log.add("egress", outcome, parent=self._turn, provider=host, tool="model", effect="local" if _is_local(host) else "external",
                     args=f"{method} {u.scheme}://{host}{f':{u.port}' if u.port else ''}{u.path}", latency_ms=ms,
                     failure_reason=error)

    def tool_specs(self) -> list[dict]:
        return SPECS + (self.mcp.tool_specs() if self.mcp else [])

    def trusted_effect(self, effect: str, mcp_server: str | None = None) -> str:
        """Why this action needs no prompt, or '' when the user must approve it."""
        if effect == "read":
            return "read-only"
        if self.s.auto_approve:
            return "auto-approved (--yes)"
        if mcp_server and self.mcp and mcp_server in self.mcp.trusted:
            return f"trusted MCP server {mcp_server}"
        if not mcp_server and self.s.trusted():
            return "trusted folder"
        if effect in self.always:
            return "approved for this session"
        return ""

    # one prompt --------------------------------------------------------------------------------------------------
    def run(self, prompt: str) -> str:
        self.messages.append({"role": "user", "content": prompt})
        self._turn = turn = self.log.add("turn", "running", args=prompt)
        self.changes.begin(prompt)
        started, outcome, answer, writes, failed_checks, nudges = time.monotonic(), "no_claim", "", 0, 0, 0
        try:
            for step in range(self.s.max_steps):
                estimate = len(self.system + json.dumps(self.messages) + json.dumps(self.tool_specs())) // 4
                reason = self.budget.check(self.log.totals(), estimate)
                if reason:
                    outcome, answer = "budget_paused", f"Stopped: {reason}. Raise the cap with --max-tokens/--max-usd."
                    self.ui.warn(answer)
                    break
                self.ui.thinking(True)
                t0 = time.monotonic()
                try:
                    reply = self.model.complete(self.system, self.messages, self.tool_specs())
                except providers.ProviderError as e:
                    self.log.add("model_call", "error", parent=turn, latency_ms=int((time.monotonic() - t0) * 1000),
                                 failure_reason=str(e))
                    outcome, answer = "model_error", f"Model error: {e}"
                    self.ui.error(answer)
                    break
                finally:
                    self.ui.thinking(False)
                reply.text = providers.THINK_RE.sub("", reply.text or "").strip()
                if reply.text:
                    # Small local models may print some calls as text and return others as real calls in the same
                    # reply (edit as text, then "run the check" as a real call): keep both, text ones first.
                    names = {t["name"] for t in self.tool_specs()}
                    text_calls, text = providers.parse_text_tool_calls(reply.text, names)
                    if text_calls:
                        seen = {(c.name, json.dumps(c.arguments, sort_keys=True)) for c in reply.tool_calls}
                        extra = [c for c in text_calls if (c.name, json.dumps(c.arguments, sort_keys=True)) not in seen]
                        for n, c in enumerate(extra):
                            c.id = f"text_{n}"
                        reply.tool_calls, reply.text = extra + reply.tool_calls, text
                usd = providers.cost(self.s.model, reply.tokens_in, reply.tokens_out, self.s.prices, self.s.local)
                self.log.add("model_call", "ok", parent=turn, latency_ms=int((time.monotonic() - t0) * 1000),
                             tokens_in=reply.tokens_in, tokens_out=reply.tokens_out, usd_cost=usd,
                             detail=f"step {step + 1}, {len(reply.tool_calls)} tool calls")
                msg: dict = {"role": "assistant", "content": reply.text or ""}
                if reply.tool_calls:
                    msg["tool_calls"] = [{"id": c.id, "type": "function", "function": {
                        "name": c.name, "arguments": json.dumps(c.arguments)}} for c in reply.tool_calls]
                self.messages.append(msg)
                nudge = (not reply.tool_calls and self.s.nudge and nudges < NUDGES and announces_step(reply.text))
                if reply.text:
                    self.ui.assistant(reply.text, final=not reply.tool_calls and not nudge)
                if nudge:
                    nudges += 1
                    self.messages.append({"role": "user", "content": NUDGE})
                    continue
                if not reply.tool_calls:
                    answer = reply.text
                    break
                for call in reply.tool_calls:
                    result, kind = self._call(call, turn)
                    writes += kind in ("verified", "check_failed")
                    failed_checks += kind == "check_failed"
                    self.messages.append({"role": "tool", "tool_call_id": call.id, "content": result})
            else:
                outcome, answer = "step_limit", f"Stopped after {self.s.max_steps} model calls without finishing (--max-steps)."
                self.ui.warn(answer)
        except KeyboardInterrupt:
            outcome, answer = "interrupted", "Interrupted."
            self.ui.warn(answer)
            # keep the transcript valid: every tool call needs a result before the next prompt
            last = self.messages[-1] if self.messages else {}
            if last.get("role") == "assistant" and last.get("tool_calls"):
                for c in last["tool_calls"]:
                    self.messages.append({"role": "tool", "tool_call_id": c["id"], "content": "interrupted by the user"})
        finally:
            saved = self.changes.commit()
            if outcome == "no_claim" and writes:
                outcome = "unbacked" if failed_checks else "backed"
            t = self.log.totals()
            self.log.finish(turn, outcome, latency_ms=int((time.monotonic() - started) * 1000),
                            tokens_in=t["tokens_in"], tokens_out=t["tokens_out"],
                            usd_cost=None if t["cost_unknown"] else t["usd"],
                            evidence=f"{writes} file change(s) checked, {failed_checks} failed" if writes else "",
                            detail="; ".join(x for x in (f"undo: {saved.name}" if saved else "",
                                                         f"nudged {nudges}x to call the tool it announced" if nudges else "") if x))
            self.ui.footer(t, self.s, saved is not None)
        return answer

    def _call(self, call: providers.ToolCall, turn: int) -> tuple[str, str]:
        """Run one tool call through prepare -> approve -> apply -> verify. Returns (result for the model, outcome)."""
        args_text = json.dumps(call.arguments, ensure_ascii=False)
        if call.bad_json:
            self.log.add("tool_call", "bad_args", parent=turn, tool=call.name, args=call.bad_json[:2000],
                         failure_reason="arguments were not valid JSON")
            return "Error: your arguments were not valid JSON. Send them again as a JSON object.", "bad_args"
        target = self.mcp.resolve(call.name) if self.mcp else None
        if target:
            server, tool = target
            summary = f"MCP {server.name}: {tool} {args_text[:300]}"
            plan = Plan("external", summary, summary, lambda: self._mcp(server, tool, call.arguments))
            mcp_server = server.name
        else:
            mcp_server = None
            try:
                plan = self.tools.prepare(call.name, call.arguments)
            except ToolError as e:
                kind = "unknown_tool" if str(e).startswith("unknown tool") else "bad_args"
                self.log.add("tool_call", kind, parent=turn, tool=call.name, args=args_text, failure_reason=str(e))
                self.ui.tool_note(f"{call.name}: {e}")
                return f"Error: {e}", kind
            except (ValueError, TypeError) as e:
                self.log.add("tool_call", "bad_args", parent=turn, tool=call.name, args=args_text, failure_reason=str(e))
                return f"Error: bad arguments: {e}", "bad_args"
        effect = {"command": "external"}.get(plan.effect, plan.effect)
        action = self.log.add("tool_call", "running", parent=turn, tool=call.name, effect=effect, args=args_text,
                              from_text=int(call.from_text))
        if plan.effect != "read":
            self.ui.show_plan(plan)
            if self.s.dry_run:
                self.log.finish(action, "dry_run", detail="dry run: not applied")
                self.ui.tool_note("dry run: not applied")
                return (f"[dry run] Not applied: {plan.summary}. The user is previewing; continue as if it succeeded but "
                        f"say in your summary that nothing was changed."), "dry_run"
            why = self.trusted_effect(plan.effect, mcp_server)
            if why:
                self.ui.tool_note(why)
            else:
                answer = self.ui.ask(plan)
                decision = "approved" if answer in ("y", "a") else "denied"
                self.log.add("decision", decision, parent=action, tool=call.name,
                             detail="approved for the rest of the session" if answer == "a" else "")
                if answer == "a":
                    self.always.add(plan.effect)
                if decision == "denied":
                    self.log.finish(action, "denied")
                    return f"The user denied this ({plan.summary}). Do not retry it; ask or try another approach.", "denied"
        else:
            self.ui.tool_note(plan.summary, read=True)
        if plan.path is not None:
            self.changes.snapshot(plan.path)
        t0 = time.monotonic()
        try:
            result, verified, evidence = plan.apply()
        except (ToolError, MCPError, OSError) as e:
            self.log.finish(action, "tool_error", failure_reason=str(e)[:2000], latency_ms=int((time.monotonic() - t0) * 1000))
            self.ui.tool_note(f"failed: {str(e)[:300]}")
            return f"Error: {e}", "tool_error"
        ms = int((time.monotonic() - t0) * 1000)
        if verified is True:
            outcome = "verified"
        elif verified is False:
            outcome = "check_failed"
        elif plan.effect == "command" and evidence != "exit code 0":
            outcome = "tool_error"
        elif evidence == "mcp error":
            outcome = "tool_error"
        else:
            outcome = "ok"
        self.log.finish(action, outcome, verified=verified, evidence=evidence, latency_ms=ms,
                        failure_reason=evidence if outcome == "tool_error" else "")
        self.ui.tool_result(plan, result, outcome)
        return result, outcome

    def _mcp(self, server, tool: str, arguments: dict):
        ok, text = server.call(tool, arguments)
        return (text or "(no content)") if ok else f"Error from {server.name}: {text}", None, "" if ok else "mcp error"

    def close(self) -> None:
        if self.mcp:
            self.mcp.close()


def _listing(root: Path, limit: int = 60) -> str:
    """The project's top level, so the model starts from real paths instead of guessing (small models invent src/)."""
    from .tools import SKIP_DIRS
    try:
        names = sorted(p.name + ("/" if p.is_dir() else "") for p in root.iterdir() if p.name not in SKIP_DIRS)
    except OSError:
        return "(unreadable)"
    return ", ".join(names[:limit]) + (f", ... ({len(names) - limit} more)" if len(names) > limit else "") or "(empty)"


def _is_local(host: str) -> bool:
    import ipaddress
    h = (host or "").lower().strip("[]")
    if h == "localhost" or h.endswith((".local", ".internal", ".localhost")):
        return True
    try:
        ip = ipaddress.ip_address(h)
        return ip.is_private or ip.is_loopback or ip.is_link_local
    except ValueError:
        return "." not in h
