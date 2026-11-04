"""The agent loop: send the conversation to the model, run the tools it asks for (after approval), feed the results
back, and repeat until it answers without asking for a tool, hits the step limit, or the budget's hard cap."""

from __future__ import annotations

import json
import os
import platform
import re
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

from . import images, providers
from .extras import BUILD_TOOLS, READ_ONLY, Extras, project_instructions
from .extras import NAMES as EXTRA_NAMES
from .extras import SPECS as EXTRA_SPECS
from .learning import Learning
from .lint_test import LintTestRunner
from .loop_detection import LoopDetector
from .mcp import MCPError, MCPManager
from .receipts import RunLog
from .repo_map import RepoMap
from .tools import SPECS, Plan, Toolbox, ToolError
from .undo import ChangeSets

SYSTEM = """You are Rooks Code, a careful coding agent working in the user's project through tools.
Project root: {root}
Operating system: {os}
{runtimes}
Files and folders at the project root: {listing}

How to work:
- Investigate first: list_files, search and read_file before changing anything. Never guess file contents.
- Change files with edit_file (exact text replacement) or write_file. Keep changes minimal and in the project's style.
- Use run_command to run tests, builds and other CLIs, and to check your work. Commands run in the project root.
- The user approves edits and commands; if one is denied, do not retry it, ask or take another approach.
- When you decide to do something, call the tool in that same reply; do not just describe it.
- For work of 3+ steps keep a todo checklist and update it as you go. Look things up with web_search / web_fetch
  instead of guessing APIs. Start servers and watchers with start_background, not run_command. Hand broad research
  to a task sub-agent so your own context stays small.
- When the user states a lasting fact or preference (their name, tools they use, how they like things done), save it
  with remember. If a saved skill fits the task, load_skill it first and follow it.
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
RETRIES = 3          # transient provider errors (429, 5xx) per model call
RETRY_BASE_S = 2.0   # 2, 6, 18 seconds unless the provider says how long
RETRY_MAX_S = 30.0

# Long sessions: when the conversation would fill COMPACT_AT of the model's window, everything before the last few turns
# is replaced by a summary the model writes, so a session can run for hours without the provider rejecting it.
COMPACT_AT = 0.7
KEEP_TURNS = 2  # the newest user prompts (with their tool calls and results) always stay word for word
# Proactive pruning: when a single tool result exceeds this many chars, shorten it immediately
PRUNE_TOOL_RESULT_AT = 8000
DELEGATION_WAIT_S = 900  # one shared-loop turn, including time the user takes to approve a change
BRIDGE_LOCAL = {"read_file", "list_files", "search", "edit_file", "write_file", "run_command"}  # run here, not in the app
# Tail budget: keep last N tool results unpruned
TAIL_TOOL_RESULTS = 4

SUMMARY_SYSTEM = ("You compress a coding session so it can continue. Write a dense summary of the conversation below for "
                  "the agent that continues it: the user's goals and preferences, files read or changed (exact paths), "
                  "commands run and their results, decisions made, and what is still open. Facts only, no advice.")


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
    def __init__(self, settings, ui, model=None, log: RunLog | None = None, messages: list[dict] | None = None,
                 sub: bool = False, sub_mode: str = "explore"):
        self.s, self.ui = settings, ui
        self.root: Path = settings.root.resolve()
        self.log = log or RunLog(self.root, provider=settings.provider, model=settings.model)
        self.model = model or providers.make(settings, on_egress=self._egress)
        self.tools = Toolbox(self.root, settings.command_timeout)
        self.changes = ChangeSets(self.root)
        self.sub = sub  # a sub-agent (the task tool): no MCP servers, no learning; tools depend on sub_mode
        self.sub_mode = sub_mode if sub else "explore"  # explore|plan = read-only; build = edits under parent approvals
        self.mcp = MCPManager(settings.mcp_servers, str(self.root), warn=ui.warn) if settings.mcp_servers and not sub else None
        self.messages: list[dict] = list(messages or [])
        self.always: set[str] = set()  # effects approved for the rest of the session ("a" at the prompt)
        self.budget = Budget(settings.max_tokens, settings.max_usd, settings.model, settings.prices, settings.local)
        self.system = SYSTEM.format(root=self.root, os=f"{platform.system()} {platform.release()}", listing=_listing(self.root),
                                    runtimes=_runtimes(self.root))
        self._turn: int | None = None
        self.learning = Learning(settings, session_id=self.log.session) if getattr(settings, "learn", True) and not sub else None
        self.extras = Extras(self)
        self.instructions, self.instruction_files = project_instructions(self.root)
        if self.instructions:
            self.system += "\n\n" + self.instructions
        self._extra = ""  # this request's memory and skills context (learning.py)
        self.loop_detector = LoopDetector() if not sub else None
        self._nudge = ""
        # Build lightweight repo map for project overview
        self.repo_map = RepoMap(self.root, max_tokens=1500) if not sub else None
        if self.repo_map:
            repo_map_text = self.repo_map.get_repo_map()
            if repo_map_text:
                self.system += f"\n\nProject structure (symbol index):\n{repo_map_text}"

        # Lint/test runner (optional, enabled via settings)
        self.lint_test_runner = None
        if not sub and getattr(settings, "lint_after_edit", False):
            self.lint_test_runner = LintTestRunner(self.root)

        # Through the app (provider workbench) the model is picked per task and the app keeps the spend, so a
        # per-model price is neither known nor needed here: only warn when the user picked a model directly.
        # NVIDIA NIM's hosted API is a free trial (no card, no bill), so a missing price there is not a risk to flag.
        if settings.max_usd and self.budget.price is None and settings.provider not in ("workbench", "nvidia"):
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
        if self.sub:
            allow = BUILD_TOOLS if self.sub_mode == "build" else READ_ONLY
            return [t for t in SPECS + EXTRA_SPECS if t["name"] in allow]
        return SPECS + EXTRA_SPECS + (self.learning.specs() if self.learning else []) + (self.mcp.tool_specs() if self.mcp else [])

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

    # sessions ----------------------------------------------------------------------------------------------------
    @property
    def transcript_path(self) -> Path:
        return self.log.path.with_suffix(".messages.json")

    def save(self) -> None:
        """The conversation next to its receipt, so `rooks -c` (or --resume ID) can pick it up later."""
        if self.sub:
            return  # a sub-agent shares the parent's receipt; its scratch conversation is not a session
        try:
            self.transcript_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.transcript_path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"provider": self.s.provider, "model": self.s.model, "messages": images.without_pixels(self.messages)},
                                      ensure_ascii=False), encoding="utf-8")
            tmp.replace(self.transcript_path)
        except OSError as e:
            self.ui.warn(f"could not save the session for later: {e}")

    def switch(self, settings, model=None) -> None:
        """Another model or provider for the rest of the session; the conversation carries over."""
        self.s = settings
        self.model = model or providers.make(settings, on_egress=self._egress)
        self.log.provider, self.log.model = settings.provider, settings.model
        self.budget = Budget(settings.max_tokens, settings.max_usd, settings.model, settings.prices, settings.local)
        if self.learning:
            self.learning.s = settings

    # compaction --------------------------------------------------------------------------------------------------
    def _estimate(self) -> int:
        pics = sum(1 for m in self.messages if isinstance(m.get("content"), list) for p in m["content"] if p.get("type") == "image_url")
        return len(self.system + json.dumps(images.without_pixels(self.messages)) + json.dumps(self.tool_specs())) // 4 + pics * 1000

    def _split(self) -> int:
        """Index where the kept tail starts: at a real user prompt, so no tool result loses its call."""
        prompts = [i for i, m in enumerate(self.messages) if m["role"] == "user" and m.get("content") != NUDGE]
        return prompts[-KEEP_TURNS] if len(prompts) >= KEEP_TURNS else 0

    def compact(self, reason: str = "") -> bool:
        """Summarise everything before the last KEEP_TURNS prompts. Returns whether anything was compacted."""
        cut = self._split()
        if cut <= 0:  # one long prompt: nothing whole to summarise, so shorten its older tool outputs instead
            return self._shrink_tool_results()
        old, before = self.messages[:cut], self._estimate()
        lines = []
        for m in old:
            body = images.text_of(m.get("content"))
            if m.get("tool_calls"):
                body += " " + " ".join(f"[calls {c['function']['name']} {c['function']['arguments'][:300]}]" for c in m["tool_calls"])
            lines.append(f"{m['role'].upper()}: {body[:4000]}")
        t0 = time.monotonic()
        try:
            reply = self.model.complete(SUMMARY_SYSTEM, [{"role": "user", "content": "\n\n".join(lines)[-200_000:]}], [])
            summary = providers.THINK_RE.sub("", reply.text or "").strip()
            usd = providers.cost(self.s.model, reply.tokens_in, reply.tokens_out, self.s.prices, self.s.local)
            self.log.add("model_call", "ok", parent=self._turn, latency_ms=int((time.monotonic() - t0) * 1000),
                         tokens_in=reply.tokens_in, tokens_out=reply.tokens_out, usd_cost=usd, detail="compaction summary")
        except providers.ProviderError as e:
            summary = ""
            self.log.add("model_call", "error", parent=self._turn, failure_reason=str(e), detail="compaction summary")
        if not summary:  # no summary: the oldest turns are dropped rather than the whole session failing
            summary = f"(The first {len(old)} messages were dropped to fit the model's window; no summary was available.)"
        self.messages = [{"role": "user", "content": f"[Summary of the earlier part of this session]\n{summary}"},
                         {"role": "assistant", "content": "Understood. Continuing from that summary."}, *self.messages[cut:]]
        after = self._estimate()
        self.log.add("compaction", "ok", parent=self._turn, detail=f"{len(old)} messages summarised, ~{before:,} to ~{after:,} tokens"
                     + (f" ({reason})" if reason else ""))
        self.ui.tool_note(f"compacted the conversation: ~{before:,} to ~{after:,} tokens")
        return True

    def _shrink_tool_results(self, keep_last: int = TAIL_TOOL_RESULTS, limit: int = PRUNE_TOOL_RESULT_AT) -> bool:
        """Proactively shorten old tool results that exceed the size limit.
        Returns whether anything was changed."""
        changed = 0
        for m in self.messages[:-keep_last]:
            if m["role"] == "tool" and len(m.get("content") or "") > limit:
                m["content"] = m["content"][:limit - 500] + "\n[... output shortened to fit the model's context window ...]"
                changed += 1
        if changed:
            self.log.add("compaction", "ok", parent=self._turn, detail=f"shortened {changed} old tool output(s)")
            self.ui.tool_note(f"shortened {changed} old tool output(s) to fit the context window")
        return bool(changed)

    # one prompt --------------------------------------------------------------------------------------------------
    def run(self, prompt: str) -> str:
        if self.uses_shared_loop():
            return self.run_via_bridge(prompt)

        first = len(self.messages)
        self._extra = self.learning.context(prompt) if self.learning else ""
        # Reset loop detector for new prompt
        if self.loop_detector:
            self.loop_detector.reset()
        self.messages.append({"role": "user", "content": images.content(prompt, self.root)})  # @shot.png sends the picture
        self._turn = turn = self.log.add("turn", "running", args=prompt)
        self.changes.begin(prompt)
        started, outcome, answer, writes, failed_checks, nudges = time.monotonic(), "no_claim", "", 0, 0, 0
        try:
            for step in range(self.s.max_steps):
                estimate = self._estimate()
                if self.s.context_tokens and estimate > self.s.context_tokens * COMPACT_AT and self.compact("context window"):
                    estimate = self._estimate()
                reason = self.budget.check(self.log.totals(), estimate)
                if reason:
                    outcome, answer = "budget_paused", f"Stopped: {reason}. Raise the cap with --max-tokens/--max-usd."
                    self.ui.warn(answer)
                    break

                # Loop/doom detection check at the start of each step
                if self.loop_detector and self.loop_detector.is_loop_detected():
                    outcome, answer = "loop_detected", (
                        f"Loop detected: {self.loop_detector._last_loop_detail}. "
                        f"Please provide guidance or type 'continue' to try a different approach."
                    )
                    self.ui.warn(answer)
                    break

                self.ui.thinking(True)
                t0 = time.monotonic()
                streamed: list[str] = []
                try:
                    reply = self._complete(streamed)
                except providers.ProviderError as e:
                    self.log.add("model_call", "error", parent=turn, latency_ms=int((time.monotonic() - t0) * 1000),
                                 failure_reason=str(e))
                    outcome, answer = "model_error", f"Model error: {e}"
                    self.ui.error(answer)
                    break
                finally:
                    self.ui.thinking(False)
                    if streamed:
                        self.ui.stream_end()
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
                if reply.text and not streamed:
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
                    if self._nudge:
                        result, self._nudge = f"{result}\n\n{self._nudge}", ""
                    self.messages.append({"role": "tool", "tool_call_id": call.id, "content": result})

                # After all tool calls in this step, run lint/test on changed files if enabled
                if self.lint_test_runner and writes:
                    changed_files = list(self.changes.current.keys()) if self.changes.current else []
                    if changed_files:
                        lint_results = self.lint_test_runner.run_lint_on_changed_files(
                            [self.root / f for f in changed_files]
                        )
                        if lint_results:
                            lint_text = self.lint_test_runner.format_lint_results(lint_results)
                            self.messages.append({"role": "user", "content": f"Lint check results:\n{lint_text}"})
                            # Re-estimate context after adding lint results
                            estimate = self._estimate()

                        # Run tests
                        test_result = self.lint_test_runner.run_tests()
                        if not test_result.passed:
                            test_text = self.lint_test_runner.format_test_result(test_result)
                            self.messages.append({"role": "user", "content": f"Test results:\n{test_text}"})
                            estimate = self._estimate()
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
            self.save()
            if self.learning and outcome not in ("interrupted", "model_error"):
                self.learning.after(prompt, answer, self.messages[first:])
        return answer

    def uses_shared_loop(self) -> bool:
        """Connected to the AI Workspace app, linked to one of its conversations, and not a sub-agent.
        ROOKS_SHARED_LOOP=0 keeps the local loop (the app still receives the finished turn via /v1/rooks/turn)."""
        return (os.environ.get("ROOKS_SHARED_LOOP", "1") != "0" and not self.sub and self.s.provider == "workbench"
                and bool(self.s.base_url and self.s.api_key and getattr(self.ui, "conversation_id", None)))

    def run_via_bridge(self, prompt: str) -> str:
        """Run one prompt on the app's shared agent loop (/v1/rooks/run_turn): one conversation, one ledger and the same
        tools as chat. File and shell tools still run here, on this machine, through _call (approval, diff, undo)."""
        base = self.s.base_url.rstrip("/").removesuffix("/v1")
        hdr = {"Authorization": f"Bearer {self.s.api_key}", "Content-Type": "application/json", "X-Workbench": "1"}
        body = json.dumps({"conversation_id": self.ui.conversation_id, "prompt": prompt,
                           "model": self.s.model if self.s.model not in ("", "default") else None}).encode()
        self._turn = turn = self.log.add("turn", "running", args=prompt)
        answer, outcome = "", "answered"
        req = urllib.request.Request(f"{base}/v1/rooks/run_turn", data=body, headers=hdr, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=DELEGATION_WAIT_S) as resp:
                for raw in resp:
                    line = raw.decode("utf-8", "replace").strip()
                    if not line.startswith("data:"):
                        continue
                    try:
                        ev = json.loads(line[5:])
                    except json.JSONDecodeError:
                        continue
                    kind = ev.get("type")
                    if kind == "tool_delegation":
                        call = providers.ToolCall(str(ev.get("id")), ev.get("tool", ""), ev.get("args") or {})
                        result, out = self._call(call, turn)
                        ok = out not in ("denied", "tool_error", "bad_args", "unknown_tool", "check_failed", "loop_detected")
                        post = urllib.request.Request(f"{base}/v1/rooks/tool_result", method="POST", headers=hdr,
                                                      data=json.dumps({"delegation_id": ev.get("id"), "ok": ok,
                                                                       "result": result}).encode())
                        try:
                            with urllib.request.urlopen(post, timeout=30):
                                pass
                        except urllib.error.HTTPError as e:
                            if e.code not in (404, 409):
                                raise
                            # the turn was stopped (or timed out) in the app while this tool ran here: the app has
                            # already closed the step, so this result is dropped and the stream ends with "stopped"
                            self.ui.tool_note(f"{call.name} finished after the turn was stopped; result not sent")
                    elif kind == "tool_call" and ev.get("name") not in BRIDGE_LOCAL:
                        self.ui.tool_note(f"{ev.get('name')} (app)", read=True)
                    elif kind == "approval_required":
                        self.ui.tool_note(f"{ev.get('tool')} is waiting for your OK in the app (Approvals): {ev.get('reason', '')}")
                    elif kind == "writing" and self._streaming():
                        self.ui.stream(ev.get("text", ""))
                    elif kind == "message":
                        answer = ev.get("content", "") or answer
                    elif kind == "stopped":
                        answer, outcome = ev.get("content", "Stopped."), "stopped"
                    elif kind == "error":
                        answer, outcome = f"Error: {ev.get('error')}", "model_error"
                        self.ui.error(str(ev.get("error")))
                    elif kind == "done":
                        if outcome == "answered":
                            outcome = ev.get("outcome") or "no_claim"  # the app's claim check on its own answer
                        break
        except (OSError, ValueError) as e:  # urllib errors are OSError subclasses
            self.log.finish(turn, "model_error", failure_reason=str(e)[:500])
            raise providers.ProviderError(f"the app's shared loop is unreachable: {e}") from e
        if answer and outcome != "model_error":  # a stop from the app is shown too: the user sees why it ended
            self.ui.assistant(answer, final=True)
        self.log.finish(turn, outcome)
        return answer or "(no answer)"

    def _complete(self, streamed: list[str]):
        """One model call. A rate limit or an overloaded provider is tried again after a pause (its Retry-After when
        it sends one), up to RETRIES times, but never once text has been shown: a half answer is not restarted.
        Falls back to configured fallback models on transient failures (429, 5xx)."""
        on_text = None
        if self._streaming():
            def on_text(delta: str) -> None:
                if not streamed:
                    self.ui.thinking(False)
                streamed.append(delta)
                self.ui.stream(delta)
                # Loop detection: track streaming content for chanting
                if self.loop_detector:
                    self.loop_detector.on_content_chunk(delta)

        # Build the fallback chain: primary + configured fallbacks
        fallback_chain = [(self.s.provider, self.s.model)]
        fallback_chain.extend(self.s.fallback_models)

        last_error: providers.ProviderError | None = None
        for fb_idx, (fb_provider, fb_model) in enumerate(fallback_chain):
            # Create model for this fallback if not the primary
            if fb_idx > 0:
                # Switch to fallback provider/model
                from . import providers as providers_mod
                import copy
                fallback_settings = copy.copy(self.s)
                fallback_settings.provider = fb_provider
                fallback_settings.model = fb_model
                # Look up provider config
                from .config import PROVIDERS
                p = PROVIDERS.get(fb_provider, {})
                fallback_settings.kind = p.get("kind", "openai")
                fallback_settings.base_url = p.get("base_url", "").rstrip("/")
                fallback_settings.key_env = p.get("key_env", "")
                fallback_settings.local = bool(p.get("local", False))
                self.model = providers_mod.make(fallback_settings, on_egress=self._egress)
                self.ui.warn(f"Falling back to {fb_provider}/{fb_model}...")

            for attempt in range(RETRIES + 1):
                try:
                    system = self.system + (f"\n\n{self._extra}" if self._extra else "")
                    if on_text:
                        return self.model.complete(system, self.messages, self.tool_specs(), on_text=on_text)
                    return self.model.complete(system, self.messages, self.tool_specs())
                except providers.ProviderError as e:
                    last_error = e
                    if attempt == RETRIES or streamed or not getattr(e, "transient", False):
                        # This fallback failed, try next in chain
                        break
                    wait = min(e.retry_after if e.retry_after is not None else RETRY_BASE_S * 3 ** attempt, RETRY_MAX_S)
                    self.ui.thinking(False)
                    self.ui.warn(f"{self.s.provider} is busy (HTTP {e.status}); trying again in {wait:.0f} s")
                    self.log.add("model_call", "retry", parent=self._turn, failure_reason=str(e)[:500])
                    time.sleep(wait)
                    self.ui.thinking(True)
            if streamed or not getattr(last_error, "transient", False):
                break  # text was already shown, or the error will not heal by switching (bad request, bad key)
        assert last_error is not None
        raise last_error  # the original error, with its status, so callers explain it the same way as before

    def _streaming(self) -> bool:
        return bool(getattr(self.s, "stream", False)) and getattr(self.ui, "can_stream", lambda: False)()

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
                allow = BUILD_TOOLS if self.sub_mode == "build" else READ_ONLY
                if self.sub and call.name not in allow:
                    raise ToolError(f"{call.name} is not available to a {self.sub_mode} sub-agent")
                if call.name in EXTRA_NAMES:
                    plan = self.extras.prepare(call.name, call.arguments)
                elif self.learning and call.name in self.learning.names():
                    plan = self.learning.prepare(call.name, call.arguments)
                else:
                    plan = self.tools.prepare(call.name, call.arguments)
            except ToolError as e:
                kind = "unknown_tool" if str(e).startswith("unknown tool") else "bad_args"
                self.log.add("tool_call", kind, parent=turn, tool=call.name, args=args_text, failure_reason=str(e))
                self.ui.tool_note(f"{call.name}: {e}")
                return f"Error: {e}", kind
            except (ValueError, TypeError) as e:
                self.log.add("tool_call", "bad_args", parent=turn, tool=call.name, args=args_text, failure_reason=str(e))
                return f"Error: bad arguments: {e}", "bad_args"

        # Loop detection: check this tool call
        if self.loop_detector:
            loop_result = self.loop_detector.on_tool_call(call.name, call.arguments)
            if loop_result.detected and loop_result.loop_type == "read_heavy":
                self.log.add("loop_detection", "nudged", parent=turn, tool=call.name, detail=loop_result.detail)
                self._nudge = ("Note: you have read many files without changing anything or running a command. If you "
                               "have enough to act, act now; if the request is unclear, stop and ask the user.")
            elif loop_result.detected:
                self.log.add("loop_detection", "detected", parent=turn, tool=call.name,
                             detail=f"{loop_result.loop_type}: {loop_result.detail}")
                self.ui.tool_note(f"Loop detected: {loop_result.detail}")
                # Don't execute the tool; return the loop detection message
                return f"Loop detected: {loop_result.detail}. Stopping to avoid unproductive repetition.", "loop_detected"
        effect = {"command": "external"}.get(plan.effect, plan.effect)
        action = self.log.add("tool_call", "running", parent=turn, tool=call.name, effect=effect, args=args_text,
                              from_text=int(call.from_text))
        blocked = self._project_hook(call.name, plan.effect, call.arguments)
        if blocked:
            self.log.finish(action, "denied", detail=blocked[:500])
            self.ui.tool_note(f"stopped by a project hook: {blocked[:200]}")
            return f"Error: a project hook stopped this ({blocked}). Do not retry it unless the user says to.", "denied"
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
                plan.tool = call.name  # the app's Approvals card names the tool
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

    def _project_hook(self, tool: str, effect: str, arguments: dict) -> str:
        """Optional .rooks/hooks/before_tool script. Exit 0 allows; any other exit stops the tool. Empty if no hook."""
        script = self.root / ".rooks" / "hooks" / "before_tool"
        if not script.is_file():
            return ""
        payload = json.dumps({"tool": tool, "effect": effect, "arguments": arguments}, ensure_ascii=False)
        try:
            proc = subprocess.run([str(script)], input=payload, text=True, capture_output=True, timeout=8, cwd=self.root)
        except (OSError, subprocess.TimeoutExpired) as e:
            return f"before_tool hook failed ({e})"
        if proc.returncode == 0:
            return ""
        return (proc.stderr or proc.stdout or "blocked by before_tool").strip()[:500]

    def _mcp(self, server, tool: str, arguments: dict):
        ok, text = server.call(tool, arguments)
        return (text or "(no content)") if ok else f"Error from {server.name}: {text}", None, "" if ok else "mcp error"

    def close(self) -> None:
        self.extras.close()
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


def _runtimes(root) -> str:
    """Which interpreters this machine really has, so the model's first command works (no `python: not found`)."""
    import shutil

    root = Path(root)
    found = []
    for venv in (".venv", "venv", "env"):
        py = root / venv / "bin" / "python"
        if py.exists():
            found.append(f"project virtualenv: {venv}/bin/python (use it for this project's tests and scripts)")
            break
    names = [n for n in ("python3", "python", "node", "npm", "pnpm", "uv", "cargo", "go") if shutil.which(n)]
    if "python3" in names and "python" not in names:
        found.append("`python` is not installed here: use python3")
    if names:
        found.append("on PATH: " + ", ".join(names))
    return "Runtimes: " + "; ".join(found) if found else ""

