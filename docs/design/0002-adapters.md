# 0002: Provider adapter interface (R1, item 88)

Status: **built** in Phase 0 item 3 (`workbench/backend/app/adapters.py`, contract tests in `tests/test_contract.py`).

## The rule
Nothing above the adapter layer knows which backend answers. `tests/test_contract.py::test_nothing_above_the_adapter_layer_branches_on_the_backend`
scans the source: only `adapters.py`, the connection API (`api.py`), the migration (`db.py`) and the run-log recorder
(`llm.py`) may mention `auth_kind` or `protocol`.

## Interface
`Adapter(connection)` exposes:

| Method | Returns |
| --- | --- |
| `chat(model, messages, tools)` | `{"message": {"role", "content", "tool_calls"?, "parsed_from_text"?}, "usage": {"prompt_tokens", "completion_tokens", "cost"?}}` |
| `stream(model, messages)` | events `{"delta"}`, `{"reasoning"}`, then `{"done", "usage"}` or `{"error"}` |
| `list_models()` | listed models (filtered for hosts that list unusable ones) plus the ones typed in |
| `context_chars()` | prompt budget in characters (Ollama: from `OLLAMA_NUM_CTX`) |
| `is_free(model)` | local models and OpenRouter `:free` |

Callers always speak one message shape: OpenAI chat style (`system`, `user`, `assistant` with `tool_calls`, `tool`). Errors are
`HTTPException`s with the provider's status and text (redacted on the way out, R7). `llm.py` wraps every call in one
`model_call` row (R2).

## Two independent axes
**auth_kind** (where the credential comes from):

| auth_kind | Credential | Protocols |
| --- | --- | --- |
| `api_key` | key stored encrypted | `chat_completions`, `responses`, `anthropic_messages` |
| `oauth_subscription` | OAuth access token from the connection's token store; missing or expired → state `needs_auth`, 401 "sign in again" | `responses` (plan usage covers Responses API requests only) |
| `session_scraping_forbidden` | none: refuses every use with the R5 text (D7) | none |
| `local_ollama` | none | `ollama_native` |
| `openai_compatible` | optional key | `chat_completions` |

**protocol** (the wire format), each converting both ways:

| Protocol | Request | Response |
| --- | --- | --- |
| `chat_completions` | as given; `stream_options.include_usage`; OpenRouter `usage.include` | `choices[0].message`, `usage.prompt_tokens/completion_tokens/cost` |
| `ollama_native` | `/api/chat`, `options.num_ctx`, tool arguments as objects, tool results carry `tool_name`, images as base64 list | `prompt_eval_count/eval_count`; a full window is an error, never a cut prompt |
| `responses` | `/responses`: system → `instructions`; items `message`, `function_call{call_id,name,arguments}`, `function_call_output{call_id,output}`, `input_image`; tools `{type: function, name, parameters, strict: false}`; `store: false` | `output[]` items `message` (`output_text`) and `function_call`; `usage.input_tokens/output_tokens`; stream `response.output_text.delta`, `response.completed` |
| `anthropic_messages` | `/messages`: `system` top level; `max_tokens` 8192; tool calls → `tool_use{id,name,input}`; tool results → `tool_result{tool_use_id}` in user turns, consecutive same-role turns merged; images as base64 `source`; headers `x-api-key`, `anthropic-version: 2023-06-01` | `content[]` `text`/`tool_use`; `usage.input_tokens/output_tokens`; stream `message_start`, `content_block_delta` (`text_delta`), `message_delta` |

Sources: Anthropic's Messages API docs page (request side, read 2026-10-29) and the vendors' generated SDK types (openai 3.41.0
`types/responses/*`, anthropic 2.3.0 `types/*`) for the response and streaming shapes. `anthropic-version` stays `2023-06-01`, the
value in Anthropic's own example; the SDK sends `2026-10-21`. Both are documented versions; moving is a one-line change.

## Contract tests
One parametrized suite runs on six backends (every auth kind that can answer, every protocol each speaks):

* model listing;
* a full agent turn through `/api/assistant/chat` that runs a tool, verifies its receipt and answers;
* the system prompt landing where each protocol defines it;
* streaming with usage recorded;
* provider errors surfaced and recorded;
* plus: an expired subscription asks for sign-in and sets `needs_auth`; subscription tokens cannot be pasted in; the R5 refusal
  everywhere; impossible pairs rejected; the legacy `kind` field still accepted; the source scan above.

The fake endpoints reject requests that break each documented wire format: Anthropic role alternation, `tool_result` answering a
real `tool_use`, top-level `system`, `max_tokens` and headers; Responses `function_call_output` answering a real `call_id`, no
`messages` field, complete tool definitions; Ollama object arguments and `tool_name`. Mutation checks: each of "system sent as a
message", "call_id dropped", "string arguments to Ollama" fails the suite.

**Not verified against the live APIs** (no keys in CI): the Responses and Anthropic Messages adapters are verified against
their documented and SDK-typed shapes only. The first real-key run is a manual check listed in BACKLOG.
