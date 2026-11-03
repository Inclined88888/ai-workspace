# Security tooling

Three free scanners guard two surfaces: the agent surface (SKILL.md packs we ship) and the build surface (secrets,
dependencies). One entry point: `scripts/security-scan.sh`.

| Tool | Licence | Version | Checks | Data leaves the machine? |
| --- | --- | --- | --- | --- |
| Gitleaks | MIT | 8.30.1 | Secrets in staged diff (hook) and full git history (CI) | No |
| OSV-Scanner | Apache 2.0 | 2.7.0 | Known vulnerabilities in every lockfile and requirements file | Yes: package names and versions go to osv.dev and deps.dev (transitive resolution) |
| t2i-skillguard | MIT | 0.1.0 | Prompt injection, script download, exec, secret exposure in SKILL.md and bundled scripts | No |

## Commands

```bash
git config core.hooksPath scripts/hooks          # once per clone: pre-commit blocks staged secrets
scripts/security-scan.sh staged                  # what the hook runs
scripts/security-scan.sh skills path/to/skills   # scan a skill folder before installing or trusting it
scripts/security-scan.sh full                    # history secrets + shipped skills + dependencies (the CI job)
```

Local install: `brew install gitleaks osv-scanner`; skillguard runs through `uvx` pinned to 0.1.0.

## What fails the build

- Any secret Gitleaks finds that is not listed in `.gitleaksignore`.
- Any HIGH or CRITICAL skillguard finding in `workbench/backend/app/packs/bundles/*/skills`.
  `workbench/backend/tests/fixtures/skills` holds deliberately malicious samples and is excluded.
- Any CRITICAL (CVSS 9 or more) dependency vulnerability. HIGH and lower are printed, not blocking.

## Baselines

- `.gitleaksignore` lists fingerprints (commit:file:rule:line) of reviewed false positives: test fixtures,
  placeholders, a pinned action SHA. Never add a real secret's fingerprint; rotate the secret instead.
- The CI job downloads Gitleaks and OSV-Scanner from their GitHub releases and checks the published SHA256.
  Bump version and checksum together.

## Not adopted, and why

- hermes-security-audit: not in the Hermes plugin catalog; no verifiable source.
- skill-auditor, agent-skill-scan-mcp: MIT but near zero adoption (2 and 0 stars); not trusted to run yet.
- TruffleHog: AGPL 3.0 and live verification sends found secrets to the provider. Needs owner approval.
- Trivy, Grype + Syft: overlap with OSV-Scanner. Add Syft only if an SBOM is required.
- Semgrep, SonarQube, OWASP ZAP, Cosign, slsa-verifier: useful, but each is its own project (rules tuning, a
  running server, a signed release pipeline). Not part of this change.
