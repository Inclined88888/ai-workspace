# Third-Party Notices for Rooks Code Engine

This document lists third-party code and ideas incorporated into Rooks Code's engine, with attribution as required by their licenses.

---

## Aider

**License:** Apache-2.0
**Copyright:** Copyright 2023-2025 Paul Gauthier
**Source:** https://github.com/Aider-AI/aider

### Incorporated Techniques:
- **Repo Map** (repomap.py): Tree-sitter based symbol extraction and relevance ranking for project overview. Adapted to use Python stdlib AST instead of tree-sitter.
- **Layered Fuzzy Edit Matching** (coders/editblock_coder.py): Multi-tier fallback matching (exact → whitespace → indentation → line-trimmed → fuzzy). Ported to edit_matching.py.
- **Linter Integration** (linter.py): Running configured linters after edits and feeding errors back with file context.

### Files Modified:
- rooks_code/repo_map.py (lightweight AST-based version)
- rooks_code/edit_matching.py (layered fuzzy matching)
- rooks_code/lint_test.py (linter/test runner)
- rooks_code/agent.py (integration)

---

## Opencode

**License:** MIT
**Copyright:** Copyright 2024 opencode contributors
**Source:** https://github.com/sst/opencode

### Incorporated Techniques:
- **Multi-Tier Fuzzy Matching** (packages/core/src/tool/plugin/edit.ts): Exact → Unicode normalization → line-trimmed → LCS-based line alignment preserving original line endings (CRLF/LF/mixed).
- **Miss Hint Generation** (edit.ts:199-241): When oldString not found, computes whitespace-insensitive line votes to locate nearest match, shows ±12 lines around it.
- **Line Ending Preservation** (edit.ts:104-125, 168-188): Matches against LF-normalized view, maps offsets back to source bytes, rejoins replacement using matched region's line endings.

### Files Modified:
- rooks_code/edit_matching.py (layered fuzzy matching with miss hints)
- rooks_code/tools.py (integration)

---

## Gemini-CLI

**License:** Apache-2.0
**Copyright:** Copyright 2025 Google LLC
**Source:** https://github.com/google-gemini/gemini-cli

### Incorporated Techniques:
- **Loop/Doom Detection** (packages/core/src/services/loopDetectionService.ts):
  - Consecutive identical tool calls: SHA256 of (tool_name, args) history; detects cycles of length 1-5 repeated 5×.
  - Content chanting: Streaming text chunked (50 chars), hashed, detects same chunk 10× within 5× chunk size average distance.
  - Read-heavy pattern: Many read tools without write progress (opencode-style doom loop).
- **Self-Correcting Edit Tiers** (packages/core/src/tools/edit.ts): Exact → flexible → regex → fuzzy (Levenshtein).
- **Omission Placeholder Detection** (omissionPlaceholderDetector.ts): Detects `// ... existing code ...` patterns.

### Files Modified:
- rooks_code/loop_detection.py (multi-layer loop detection)
- rooks_code/edit_matching.py (fuzzy tier)

---

## Codex / codex-rs

**License:** Apache-2.0
**Copyright:** Copyright 2024 OpenAI
**Source:** https://github.com/openai/codex

### Incorporated Techniques:
- **Apply Patch Format** (codex-rs/apply-patch/): Unified diff with `*** Begin Patch`, `*** Update File:`, `@@` hunks. Parser validates, computes new content, verifies by re-reading.
- **Context Compaction** (codex-rs/core/compact/): Auto-compaction when near context window: summarizes earlier turns via auxiliary model, keeps head/tail, inserts summary checkpoint.

### Files Modified:
- rooks_code/agent.py (enhanced compaction with proactive pruning)

---

## Hermes-Agent

**License:** MIT
**Copyright:** Copyright 2024-2025 Nous Research
**Source:** https://github.com/NousResearch/hermes-agent

### Incorporated Techniques:
- **Multi-Strategy Context Compression** (agent/context_compressor.py, agent/conversation_compression.py):
  - Proactive pruning: drops stale tool outputs first (large outputs → summaries)
  - Tail budget: keeps last N turns whole, demotes older tool results to stubs
  - Summary dispatch: auxiliary model summarizes middle, iterative updates
- **Retry with Fallback Chain** (run_agent.py): Per-call timeout + stale detection; 429/5xx → credential pool rotation → fallback model chain; clean user-facing messages.
- **Iteration Budget** (run_agent.py): IterationBudget tracks tool calls per turn, hard cap, integrates with compaction trigger.
- **Tool Error Recovery Guardrails** (run_agent.py, agent/tool_guardrails.py): Identical-call streak detection, batch cycle detection, appends guidance to tool result, stops turn on halt.

### Files Modified:
- rooks_code/agent.py (proactive pruning, fallback chain, iteration budget)
- rooks_code/providers.py (retry logic already present, enhanced)

---

## Summary of License Compliance

| Project | License | Attribution Required | Modification Notes |
|---------|---------|---------------------|-------------------|
| Aider | Apache-2.0 | Yes (NOTICE file) | Adapted to stdlib AST, no tree-sitter dependency |
| Opencode | MIT | Yes (license text) | Ported from TypeScript to Python |
| Gemini-CLI | Apache-2.0 | Yes (NOTICE file) | Ported from TypeScript to Python |
| Codex | Apache-2.0 | Yes (NOTICE file) | Concepts only, no code copied |
| Hermes-Agent | MIT | Yes (license text) | Concepts only, no code copied |

---

## How to Update

When adding new third-party techniques:
1. Add an entry to this file with project name, license, copyright, and source URL
2. List the specific techniques incorporated
3. List the files modified in Rooks Code
4. Ensure license compliance (include license text if required by Apache-2.0)
5. Update the summary table

---

## License Texts

### Apache-2.0 (Aider, Gemini-CLI, Codex)
```
                                 Apache License
                           Version 2.0, January 2004
                        http://www.apache.org/licenses/

   TERMS AND CONDITIONS FOR USE, REPRODUCTION, AND DISTRIBUTION

   1. Definitions.

      "License" shall mean the terms and conditions for use, reproduction,
      and distribution as defined by Sections 1 through 9 of this document.

      "Licensor" shall mean the copyright owner or entity authorized by
      the copyright owner that is granting the License.

      "Legal Entity" shall mean the union of the acting entity and all
      other entities that control, are controlled by, or are under common
      control with that entity. For the purposes of this definition,
      "control" means (i) the power, direct or indirect, to cause the
      direction or management of such entity, whether by contract or
      otherwise, or (ii) ownership of fifty percent (50%) or more of the
      outstanding shares, or (iii) beneficial ownership of such entity.

      "You" (or "Your") shall mean an individual or Legal Entity
      exercising permissions granted by this License.

      "Source" form shall mean the preferred form for making modifications,
      including but not limited to software source code, documentation
      source, and configuration files.

      "Object" form shall mean any form resulting from mechanical
      transformation or translation of a Source form, including but
      not limited to compiled object code, generated documentation,
      and conversions to other media types.

      "Work" shall mean the work of authorship, whether in Source or
      Object form, made available under the License, as indicated by a
      copyright notice that is included in or attached to the work
      (an example is provided in the Appendix below).

      "Derivative Works" shall mean any work, whether in Source or Object
      form, that is based on (or derived from) the Work and for which the
      editorial revisions, annotations, elaborations, or other modifications
      represent, as a whole, an original work of authorship. For the purposes
      of this License, Derivative Works shall not include works that remain
      separable from, or merely link (or bind by name) to the interfaces of,
      the Work and Derivative Works thereof.

      "Contribution" shall mean any work of authorship, including
      the original version of the Work and any modifications or additions
      to that Work or Derivative Works thereof, that is intentionally
      submitted to Licensor for inclusion in the Work by the copyright owner
      or by an individual or Legal Entity authorized to submit on behalf of
      the copyright owner. For the purposes of this License, "submitted"
      means any form of electronic, verbal, or written communication sent
      to the Licensor or its representatives, including but not limited to
      communication on electronic mailing lists, bulletin board systems,
      and similar chat rooms, but excluding communication that is conspicuously
      marked or otherwise designated in writing by the contributor as "Not a
      Contribution."

      "Contributor" shall mean Licensor and any individual or Legal Entity
      on behalf of whom a Contribution has been received by Licensor and
      subsequently incorporated within the Work.

   2. Grant of Copyright License. Subject to the terms and conditions of
      this License, each Contributor hereby grants to You a perpetual,
      worldwide, non-exclusive, no-charge, royalty-free, irrevocable
      copyright license to reproduce, prepare Derivative Works of,
      publicly display, publicly perform, sublicense, and distribute the
      Work and such Derivative Works in Source or Object form.

   3. Grant of Patent License. Subject to the terms and conditions of
      this License, each Contributor hereby grants to You a perpetual,
      worldwide, non-exclusive, no-charge, royalty-free, irrevocable
      (except as stated in this section) patent license to make, have made,
      use, offer to sell, sell, import, and otherwise transfer the Work,
      where such license applies only to those patent claims licensable
      by such Contributor that are necessarily infringed by their
      Contribution(s) alone or by combination of their Contribution(s)
      with the Work to which such Contribution(s) was submitted. If You
      institute patent litigation against any entity (including a
      cross-claim or counterclaim in a lawsuit) alleging that the Work
      or a Contribution incorporated within the Work constitutes direct
      or contributory patent infringement, then any patent licenses
      granted to You under this License for that Work shall terminate
      as of the date such litigation is filed.

   4. Redistribution. You may reproduce and distribute copies of the
      Work or Derivative Works thereof in any medium, with or without
      modifications, and in Source or Object form, provided that You
      meet the following conditions:

      (a) You must give any other recipients of the Work or
          Derivative Works a copy of this License; and

      (b) You must cause any modified files to carry prominent notices
          stating that You changed the files; and

      (c) You must retain, in the Source form of any Derivative Works
          that You distribute, all copyright, patent, trademark, and
          attribution notices from the Source form of the Work,
          excluding those notices that do not pertain to any part of
          the Derivative Works; and

      (d) If the Work includes a "NOTICE" text file as part of its
          distribution, then any Derivative Works that You distribute must
          include a readable copy of the attribution notices contained
          within such NOTICE file, excluding those notices that do not
          pertain to any part of the Derivative Works, in at least one
          of the following places: within a NOTICE text file distributed
          as part of the Derivative Works; within the Source form or
          Object form of the Derivative Works, if displayed; or within
          the display generated by the Derivative Works, if and wherever
          such display is generated. The contents of the NOTICE file
          are for informational purposes only and do not modify the
          License. You may add Your own attribution notices within
          Derivative Works that You distribute, alongside or as an addendum
          to the NOTICE text from the Work, provided that such additional
          attribution notices cannot be construed as modifying the License.

      You may add Your own copyright statement to Your modifications and
      may provide additional or different attribution notices within
      Derivative Works that You distribute, provided that such additional
      attribution notices cannot be construed as modifying the License.

   5. Submission of Contributions. Unless You explicitly state otherwise,
      any Contribution intentionally submitted for inclusion in the Work
      by You to the Licensor shall be under the terms and conditions of
      this License, without any additional terms or conditions.
      Notwithstanding the above, nothing herein shall supersede or modify
      the terms of any separate license agreement you may have executed
      with Licensor regarding such Contribution.

   6. Trademarks. This License does not grant permission to use the trade
      names, trademarks, service marks, or product names of the Licensor,
      except as required for reasonable and customary use in describing the
      origin of the Work and reproducing the content of the NOTICE file.

   7. Disclaimer of Warranty. Unless required by applicable law or
      agreed to in writing, Licensor provides the Work (and each
      Contributor provides its Contribution) on an "AS IS" BASIS,
      WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or
      implied, including, without limitation, warranties or conditions
      of TITLE, NON-INFRINGEMENT, MERCHANTABILITY, or FITNESS FOR A
      PARTICULAR PURPOSE. You are solely responsible for determining the
      appropriateness of using or redistributing the Work and assume any
      risks associated with Your exercise of permissions under this License.

   8. Limitation of Liability. In no event and under no legal theory,
      whether in tort (including negligence), contract, or otherwise,
      unless required by applicable law or agreed to in writing, shall
      any Contributor be liable to You for damages, including any direct,
      indirect, special, incidental, or consequential damages of any
      character arising as a result of this License or out of the use or
      inability to use the Work (including but not limited to damages for
      loss of goodwill, work stoppage, computer equipment malfunction or
      injury, or damages resulting from lost data or lost time), even if
      such Contributor has been advised of the possibility of such damages.

   9. Accepting Warranty or Additional Liability. While redistributing
      the Work or Derivative Works thereof, You may choose to offer,
      and charge a fee for, acceptance of the Work and Derivative Works
      the acceptance of warranty or additional liability by You. If You
      accept such warranty or additional liability, You are solely
      responsible for any claims arising from such acceptance.
```

### MIT (Opencode, Hermes-Agent)
```
MIT License

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

---

*Generated on 2026-11-03 as part of the Rooks Code engine learning initiative.*