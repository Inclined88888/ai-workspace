# Evidence: acceptance tests re-run on integration/rooks-finish @ d632d85

Generated 2026-11-04 02:37 UTC by scripts/audit/evidence.py. Provider keys stripped from the environment; temp data dirs; scripted fake model servers.

## SLICE: Terminal turn runs on the app's shared loop; local file/shell tools delegated back with diff review and approval in the terminal; one approval shared by terminal and app; per-request ids

- delivering commits: a227272 (feat/session-approval-slice), 135e095 + 0ef30a2 (web approvals live + labels)
- run at: d632d85

test: `workbench/backend/tests/test_rooks_shared_loop.py`

```
$ cd workbench/backend && python -m pytest -v -p no:cacheprovider -W ignore tests/test_rooks_shared_loop.py
tests/test_rooks_shared_loop.py::test_terminal_tools_exist_only_on_the_cli_channel_and_replace_the_workspace_ones PASSED [ 12%]
tests/test_rooks_shared_loop.py::test_a_cli_tool_call_is_delegated_with_its_arguments PASSED [ 25%]
tests/test_rooks_shared_loop.py::test_the_shared_loop_waits_for_the_terminal_and_continues_with_its_result PASSED [ 37%]
tests/test_rooks_shared_loop.py::test_a_terminal_approval_shows_in_the_app_with_its_diff_and_the_app_can_answer PASSED [ 50%]
tests/test_rooks_shared_loop.py::test_an_earlier_yes_never_approves_a_later_change PASSED [ 62%]
tests/test_rooks_shared_loop.py::test_terminal_approval_endpoints_need_the_bridge_key_and_a_terminal_row PASSED [ 75%]
tests/test_rooks_shared_loop.py::test_the_terminal_reaches_an_app_chat_only_after_a_handoff PASSED [ 87%]
tests/test_rooks_shared_loop.py::test_terminal_egress_audit_counts_external_hosts PASSED [100%]
============================== 8 passed in 2.41s ===============================
(exit 0)
```

test: `rooks-code/tests/test_shared_loop_e2e.py`

```
$ cd rooks-code && python -m pytest -v -p no:cacheprovider -W ignore tests/test_shared_loop_e2e.py
tests/test_shared_loop_e2e.py::test_rooks_runs_on_the_apps_shared_loop_and_the_edit_is_approved_in_the_terminal PASSED [ 33%]
tests/test_shared_loop_e2e.py::test_an_edit_denied_in_the_app_is_not_made PASSED [ 66%]
tests/test_shared_loop_e2e.py::test_stopping_in_the_app_while_the_terminal_runs_a_tool_ends_the_turn_everywhere PASSED [100%]
============================== 3 passed in 12.06s ==============================
(exit 0)
```

result: PASS

## B1: Team lead over AI sub-agents: split, parallel read-only sub-agents on the Jobs board, synthesis back to chat

- delivering commits: 27aef95 + 19651f8 (feat/diff-B1), 66f646f (merge)
- run at: d632d85

test: `workbench/backend/tests/test_team_lead_subagents.py`

```
$ cd workbench/backend && python -m pytest -v -p no:cacheprovider -W ignore tests/test_team_lead_subagents.py
tests/test_team_lead_subagents.py::test_two_subagents_run_in_parallel_and_the_lead_synthesizes_into_the_chat PASSED [ 25%]
tests/test_team_lead_subagents.py::test_subagents_only_get_read_only_tools PASSED [ 50%]
tests/test_team_lead_subagents.py::test_a_subagent_cannot_call_a_write_tool_by_name PASSED [ 75%]
tests/test_team_lead_subagents.py::test_dispatch_needs_a_goal_and_two_to_six_subtasks PASSED [100%]
============================== 4 passed in 2.78s ===============================
(exit 0)
```

result: PASS

## B2: Knowledge-graph memory: multi-hop traversal for the user (API) and the model (explore_graph tool)

- delivering commits: 3cbc117 (feat/diff-B2-kg)
- run at: d632d85

test: `workbench/backend/tests/test_knowledge_graph_traverse.py`

```
$ cd workbench/backend && python -m pytest -v -p no:cacheprovider -W ignore tests/test_knowledge_graph_traverse.py
tests/test_knowledge_graph_traverse.py::test_traverse_follows_direction_rel_and_depth PASSED [ 50%]
tests/test_knowledge_graph_traverse.py::test_the_model_answers_from_the_graph_and_never_sees_sensitive_entities PASSED [100%]
============================== 2 passed in 2.11s ===============================
(exit 0)
```

result: PASS

## B3: Packs from the terminal: rooks pack list/info/add/remove against the app's registry

- delivering commits: 2fbeed0 (feat/diff-B3-packs)
- run at: d632d85

test: `rooks-code/tests/test_pack_cli_e2e.py`

```
$ cd rooks-code && python -m pytest -v -p no:cacheprovider -W ignore tests/test_pack_cli_e2e.py
tests/test_pack_cli_e2e.py::test_rooks_pack_lists_adds_and_removes_the_same_packs_as_the_app PASSED [100%]
============================== 1 passed in 3.18s ===============================
(exit 0)
```

result: PASS

## B4: Hand-off: app conversation continues in rooks (one-time code) and the answer shows back in the app

- delivering commits: 73ac5da (feat/diff-B4-handoff)
- run at: d632d85

test: `rooks-code/tests/test_handoff_e2e.py`

```
$ cd rooks-code && python -m pytest -v -p no:cacheprovider -W ignore tests/test_handoff_e2e.py
tests/test_handoff_e2e.py::test_an_app_conversation_continues_in_the_terminal_and_comes_back PASSED [ 50%]
tests/test_handoff_e2e.py::test_codes_need_the_signed_in_app_and_the_terminal_token PASSED [100%]
============================== 2 passed in 5.27s ===============================
(exit 0)
```

test: `workbench/backend/tests/test_rooks_shared_loop.py`

```
$ cd workbench/backend && python -m pytest -v -p no:cacheprovider -W ignore tests/test_rooks_shared_loop.py::test_the_terminal_reaches_an_app_chat_only_after_a_handoff
tests/test_rooks_shared_loop.py::test_the_terminal_reaches_an_app_chat_only_after_a_handoff PASSED [100%]
============================== 1 passed in 1.77s ===============================
(exit 0)
```

result: PASS

## B5: Egress audit from the terminal: rooks egress reports what left the machine, same numbers as the app

- delivering commits: 31ef807 (feat/diff-B5-egress)
- run at: d632d85

test: `rooks-code/tests/test_egress_cli_e2e.py`

```
$ cd rooks-code && python -m pytest -v -p no:cacheprovider -W ignore tests/test_egress_cli_e2e.py
tests/test_egress_cli_e2e.py::test_rooks_egress_reports_what_the_app_sent_and_matches_the_app PASSED [100%]
============================== 1 passed in 2.92s ===============================
(exit 0)
```

test: `workbench/backend/tests/test_rooks_shared_loop.py`

```
$ cd workbench/backend && python -m pytest -v -p no:cacheprovider -W ignore tests/test_rooks_shared_loop.py::test_terminal_egress_audit_counts_external_hosts
tests/test_rooks_shared_loop.py::test_terminal_egress_audit_counts_external_hosts PASSED [100%]
============================== 1 passed in 1.46s ===============================
(exit 0)
```

result: PASS

## B6: Redaction from the terminal: rooks redact filter and --check gate; AWS and PEM private keys now redacted

- delivering commits: bccec7e (feat/diff-B6-redact)
- run at: d632d85

test: `rooks-code/tests/test_redact_cli.py`

```
$ cd rooks-code && python -m pytest -v -p no:cacheprovider -W ignore tests/test_redact_cli.py
tests/test_redact_cli.py::test_rooks_redact_hides_every_secret_and_keeps_ordinary_text PASSED [ 33%]
tests/test_redact_cli.py::test_check_fails_on_secrets_and_passes_clean_text PASSED [ 66%]
tests/test_redact_cli.py::test_the_vendored_rules_still_match_the_app PASSED [100%]
============================== 3 passed in 0.35s ===============================
(exit 0)
```

result: PASS

## FIX-base: Failures present on base 64980bf, fixed: rooks login + streaming fake (merge loss), document taint LIKE bug (safety gate), date-rot in portal watch, path-sensitive cli-agents test

- delivering commits: 61ea132, ec8165c (fix/rooks-merge-regressions), e23a134/0905df8
- run at: d632d85

test: `rooks-code/tests/test_sessions_stream_login.py`

```
$ cd rooks-code && python -m pytest -v -p no:cacheprovider -W ignore tests/test_sessions_stream_login.py
tests/test_sessions_stream_login.py::test_openai_answers_and_tool_calls_stream PASSED [  7%]
tests/test_sessions_stream_login.py::test_anthropic_streams_and_marks_cache_breakpoints PASSED [ 15%]
tests/test_sessions_stream_login.py::test_piped_output_does_not_stream PASSED [ 23%]
tests/test_sessions_stream_login.py::test_continue_picks_up_the_last_session PASSED [ 30%]
tests/test_sessions_stream_login.py::test_long_sessions_are_compacted_before_the_window_fills PASSED [ 38%]
tests/test_sessions_stream_login.py::test_login_checks_the_key_saves_it_privately_and_uses_it PASSED [ 46%]
tests/test_sessions_stream_login.py::test_login_refuses_a_key_that_does_not_work PASSED [ 53%]
tests/test_sessions_stream_login.py::test_key_shapes_are_recognised PASSED [ 61%]
tests/test_sessions_stream_login.py::test_doctor_explains_what_is_missing PASSED [ 69%]
tests/test_sessions_stream_login.py::test_model_picker_lists_and_switches PASSED [ 76%]
tests/test_sessions_stream_login.py::test_provider_switch_keeps_the_conversation PASSED [ 84%]
tests/test_sessions_stream_login.py::test_an_overloaded_provider_is_tried_again PASSED [ 92%]
tests/test_sessions_stream_login.py::test_no_price_warning_through_the_app_but_still_for_a_direct_model PASSED [100%]
============================== 13 passed in 6.03s ==============================
(exit 0)
```

test: `rooks-code/tests/test_tui_pty.py`

```
$ cd rooks-code && python -m pytest -v -p no:cacheprovider -W ignore tests/test_tui_pty.py
tests/test_tui_pty.py::test_interactive_cli_accepts_input_streams_and_exits PASSED [100%]
============================== 1 passed in 0.85s ===============================
(exit 0)
```

test: `workbench/backend/tests/test_attachments.py`

```
$ cd workbench/backend && python -m pytest -v -p no:cacheprovider -W ignore tests/test_attachments.py
tests/test_attachments.py::test_image_upload_is_typed_by_its_bytes_and_sent_as_an_image_part PASSED [ 20%]
tests/test_attachments.py::test_document_text_is_marked_and_taints_the_turn PASSED [ 40%]
tests/test_attachments.py::test_limits_and_refusals PASSED               [ 60%]
tests/test_attachments.py::test_only_the_newest_images_go_as_pixels PASSED [ 80%]
tests/test_attachments.py::test_telegram_photo_goes_through_the_same_path PASSED [100%]
============================== 5 passed in 3.26s ===============================
(exit 0)
```

test: `workbench/backend/tests/test_sites.py`

```
$ cd workbench/backend && python -m pytest -v -p no:cacheprovider -W ignore tests/test_sites.py::test_plain_english_watch
tests/test_sites.py::test_plain_english_watch PASSED                     [100%]
============================== 1 passed in 2.12s ===============================
(exit 0)
```

test: `workbench/backend/tests/test_cli_agents.py`

```
$ cd workbench/backend && python -m pytest -v -p no:cacheprovider -W ignore tests/test_cli_agents.py
tests/test_cli_agents.py::test_detects_signed_in_claude_and_codex PASSED [  9%]
tests/test_cli_agents.py::test_signed_out_and_missing PASSED             [ 18%]
tests/test_cli_agents.py::test_api_key_sign_in_is_reported PASSED        [ 27%]
tests/test_cli_agents.py::test_claude_job_asks_then_resumes_with_only_the_approved_steps PASSED [ 36%]
tests/test_cli_agents.py::test_deny_stops_without_changes PASSED         [ 45%]
tests/test_cli_agents.py::test_accept_edits_mode_and_plan_mode PASSED    [ 54%]
tests/test_cli_agents.py::test_signed_out_mid_run_is_a_clear_failure PASSED [ 63%]
tests/test_cli_agents.py::test_codex_read_only_first_then_workspace_write PASSED [ 72%]
tests/test_cli_agents.py::test_folder_must_exist PASSED                  [ 81%]
tests/test_cli_agents.py::test_non_git_folder_diff_comes_from_edit_steps PASSED [ 90%]
tests/test_cli_agents.py::test_subscriptions_list_offers_claude_through_claude_code PASSED [100%]
============================== 11 passed in 6.40s ==============================
(exit 0)
```

result: PASS

## CANCEL: Stop from the app while rooks runs a delegated tool: the turn ends as stopped in the app and the terminal, the step is closed, the late result is dropped, the model is not asked again; Stop also lands during web tool steps

- delivering commits: 726f968
- run at: d632d85

test: `rooks-code/tests/test_shared_loop_e2e.py`

```
$ cd rooks-code && python -m pytest -v -p no:cacheprovider -W ignore tests/test_shared_loop_e2e.py::test_stopping_in_the_app_while_the_terminal_runs_a_tool_ends_the_turn_everywhere
tests/test_shared_loop_e2e.py::test_stopping_in_the_app_while_the_terminal_runs_a_tool_ends_the_turn_everywhere PASSED [100%]
============================== 1 passed in 5.79s ===============================
(exit 0)
```

test: `workbench/backend/tests/test_chat_controls.py`

```
$ cd workbench/backend && python -m pytest -v -p no:cacheprovider -W ignore tests/test_chat_controls.py
tests/test_chat_controls.py::test_stop_cancels_the_turn_and_keeps_partial_state PASSED [ 11%]
tests/test_chat_controls.py::test_stop_without_a_turn_is_a_noop PASSED   [ 22%]
tests/test_chat_controls.py::test_regenerate_drops_the_last_reply_and_reanswers PASSED [ 33%]
tests/test_chat_controls.py::test_regenerate_refuses_when_there_is_no_reply PASSED [ 44%]
tests/test_chat_controls.py::test_edit_and_resend_truncates_and_replaces PASSED [ 55%]
tests/test_chat_controls.py::test_edit_rules PASSED                      [ 66%]
tests/test_chat_controls.py::test_resend_does_not_duplicate_the_user_message PASSED [ 77%]
tests/test_chat_controls.py::test_chat_controls_require_auth PASSED      [ 88%]
tests/test_chat_controls.py::test_stop_during_a_tool_step_ends_the_turn_as_stopped PASSED [100%]
============================== 9 passed in 5.35s ===============================
(exit 0)
```

result: PASS

## L4-catalog: models.dev price list download is off by default; Settings switch; env override

- delivering commits: 1c89656
- run at: d632d85

test: `workbench/backend/tests/test_catalog_default_off.py`

```
$ cd workbench/backend && python -m pytest -v -p no:cacheprovider -W ignore tests/test_catalog_default_off.py
tests/test_catalog_default_off.py::test_model_catalog_update_is_off_until_turned_on PASSED [ 50%]
tests/test_catalog_default_off.py::test_the_environment_variable_overrides_the_setting PASSED [100%]
============================== 2 passed in 1.62s ===============================
(exit 0)
```

result: PASS

## APP-TERM: #7 terminal inside the app: real shell over a PTY WebSocket, signed in + same origin + loopback only, no app secrets in its env, the tab's close ends the shell and its background jobs

- delivering commits: 4e17ff4
- run at: d632d85

test: `workbench/backend/tests/test_app_terminal.py`

```
$ cd workbench/backend && python -m pytest -v -p no:cacheprovider -W ignore tests/test_app_terminal.py
tests/test_app_terminal.py::test_a_signed_in_page_gets_a_working_shell_without_the_apps_secrets PASSED [ 12%]
tests/test_app_terminal.py::test_closing_the_tab_ends_the_shell_and_what_it_started PASSED [ 25%]
tests/test_app_terminal.py::test_who_may_not_open_a_terminal[signed_out] PASSED [ 37%]
tests/test_app_terminal.py::test_who_may_not_open_a_terminal[other_origin] PASSED [ 50%]
tests/test_app_terminal.py::test_who_may_not_open_a_terminal[remote] PASSED [ 62%]
tests/test_app_terminal.py::test_who_may_not_open_a_terminal[switched_off] PASSED [ 75%]
tests/test_app_terminal.py::test_status_needs_a_session PASSED           [ 87%]
tests/test_app_terminal.py::test_local_means_a_loopback_peer_and_a_loopback_page PASSED [100%]
============================== 8 passed in 1.65s ===============================
(exit 0)
```

result: PASS

## UI-chromium: B1 run tree, B2 graph walk, #7 app terminal, axe-core WCAG A/AA and ARIA snapshots

- run at: 4e17ff4; screenshots, axe JSON and ARIA trees in docs/evidence/ui/chromium/

```
$ AXE_JS=axe-core-4.10.3/axe.min.js UI_BROWSER=chromium python scripts/audit/ui_screens.py
PASS setup and sign in
PASS B1: every sub-task listed
PASS B1: synthesis shown
PASS B1: failed sub-task marked
PASS B2: 2-hop walk shows all three
PASS B2: paths show the links
PASS B2: rel filter narrows the walk
PASS #7: typed command ran in the app terminal (app-term-42)
PASS #7: leaving the page ended the shell (open=0)
PASS chat: a main landmark is exposed
PASS chat: at least one live region
PASS b1-team-runs: no serious or critical axe violations ([])
PASS b2-graph-walk: no serious or critical axe violations ([])
PASS app-terminal: no serious or critical axe violations ([])
PASS chat: no serious or critical axe violations ([])
b1-team-runs axe: []
b2-graph-walk axe: []
app-terminal axe: []
chat axe: []
```

result: PASS

## UI-webkit: B1 run tree, B2 graph walk, #7 app terminal, axe-core WCAG A/AA and ARIA snapshots

- run at: 4e17ff4; screenshots, axe JSON and ARIA trees in docs/evidence/ui/webkit/

```
$ AXE_JS=axe-core-4.10.3/axe.min.js UI_BROWSER=webkit python scripts/audit/ui_screens.py
PASS setup and sign in
PASS B1: every sub-task listed
PASS B1: synthesis shown
PASS B1: failed sub-task marked
PASS B2: 2-hop walk shows all three
PASS B2: paths show the links
PASS B2: rel filter narrows the walk
PASS #7: typed command ran in the app terminal (app-term-42)
PASS #7: leaving the page ended the shell (open=0)
PASS chat: a main landmark is exposed
PASS chat: at least one live region
PASS b1-team-runs: no serious or critical axe violations ([])
PASS b2-graph-walk: no serious or critical axe violations ([])
PASS app-terminal: no serious or critical axe violations ([])
PASS chat: no serious or critical axe violations ([])
b1-team-runs axe: []
b2-graph-walk axe: []
app-terminal axe: []
chat axe: []
```

result: PASS

