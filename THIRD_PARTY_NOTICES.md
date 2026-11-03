# Third-Party Notices

This file acknowledges third-party code patterns and designs adapted in Rooks Code.

## Codex (Apache 2.0)

**Source:** `/Users/azad_mavi/competitor-analysis/codex/codex-rs/tui/`  
**License:** Apache License 2.0  
**Copyright:** 2024 OpenAI

**Adapted patterns:**
- `InterruptManager` queue for deferring approval prompts during streaming (`chatwidget/interrupts.rs`)
- `ApprovalOverlay` single-key menu with shortcuts (y/n/a/d/Esc) (`bottom_pane/approval_overlay.rs`)
- `PatchHistoryCell` compact diff summary with terminal hyperlinks (`history_cell/patches.rs`)
- `StatusTimer` pause/resume for modal overlays (`status_indicator_widget/timer.rs`)

## OpenCode (MIT)

**Source:** `/Users/azad_mavi/competitor-analysis/opencode/packages/tui/`  
**License:** MIT License  
**Copyright:** 2024 OpenCode Contributors

**Adapted patterns:**
- Virtualized diff rendering with chunked Tree-sitter highlights (`component/patch-diff.tsx`)
- Live shell output dialog with polling and scroll keys (`component/dialog-shell-output.tsx`)
- Command palette footer key hints pattern

## Gemini CLI (Apache 2.0)

**Source:** `/Users/azad_mavi/competitor-analysis/gemini-cli/packages/cli/src/ui/`  
**License:** Apache License 2.0  
**Copyright:** 2026 Google LLC

**Adapted patterns:**
- Tabbed multi-question approval dialog (`components/AskUserDialog.tsx`)
- Type-to-jump in choice lists (`components/ChoiceQuestionView.tsx`)
- Streaming markdown with pending cursor (`components/shared/MarkdownDisplay.tsx`)
- Esc/Ctrl+C cancel with context awareness

## Hermes Agent (MIT)

**Source:** `/Users/azad_mavi/competitor-analysis/hermes-agent/ui-tui/src/components/`  
**License:** MIT License  
**Copyright:** 2024 Nous Research

**Adapted patterns:**
- Incremental markdown streaming with settled-block commit at blank-line boundaries (`streamingMarkdown.tsx`)
- Rich status panel with spinners, token counts, subagent heatmap (`thinking.tsx`)
- Grapheme-aware input with synthetic hint cursor (`textInput.tsx`)
- Typed-text fast-echo bypassing React for instant keystroke visibility
- Per-section visibility modes (expanded/collapsed/hidden)

## Aider (Apache 2.0)

**Source:** `/Users/azad_mavi/competitor-analysis/aider/aider/io.py`  
**License:** Apache License 2.0  
**Copyright:** 2024 Aider Contributors

**Adapted patterns:**
- Grouped confirmation dialog with (Y)es/(N)o/(A)ll/(S)kip/(D)on't ask again (`confirm_ask`)
- MarkdownStream progressive rendering
- External editor integration (Ctrl+X Ctrl+E)

## Cline (Apache 2.0)

**Source:** `/Users/azad_mavi/competitor-analysis/cline/apps/cli/src/runtime/interactive/`  
**License:** Apache License 2.0  
**Copyright:** 2024 Cline Contributors

**Adapted patterns:**
- Plan/Act mode toggle with notice tracker
- Approval controller with inline diff preview
- Cost/context indicators in prompt

## Kimi Code (MIT)

**Source:** `/Users/azad_mavi/competitor-analysis/kimi-code/packages/pi-tui/src/components/`  
**License:** MIT License  
**Copyright:** 2024 Moonshot AI

**Adapted patterns:**
- Rich slash command completion with descriptions and categories
- Chord keybinding system (multi-key sequences)
- Alt-screen TUI architecture with mouse support