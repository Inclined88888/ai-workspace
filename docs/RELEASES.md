# Signed release artifacts

Item 80 ships a pinned GitHub Actions workflow using GitHub OIDC and Sigstore-backed
artifact attestations. No long-lived signing private key is stored in the repository.
Version tags use `vMAJOR.MINOR.PATCH`; creating a tag is a separate release decision.
The workflow requires the selected commit to be on main and to have a successful
CI run, then packages deterministic source tar.gz, the stdlib `ws` client, and
SHA256SUMS. Every file is attested and verified before upload/publication.

Manual workflow dispatch produces a signed **candidate** Actions artifact without
publishing a release. A valid version-tag push publishes the same artifact format
as a GitHub release. For v0.1.0 the owner explicitly waived the two unverified external checks in Exit Gate 0.
This is an exception, not a passing result; see PHASE0-GATE.md. Future releases must reassess those checks.
The source includes Dockerfiles; this workflow does not sign container images or
claim reproducible dependency resolution of upstream Docker/npm/pip packages.

After downloading artifacts using a trusted current GitHub CLI with `gh attestation verify` support:

```sh
gh attestation verify ai-workspace-v0.1.0.tar.gz --repo Inclined88888/ai-workspace \
  --signer-workflow Inclined88888/ai-workspace/.github/workflows/release.yml
gh attestation verify ws --repo Inclined88888/ai-workspace \
  --signer-workflow Inclined88888/ai-workspace/.github/workflows/release.yml
sha256sum -c SHA256SUMS
```

Use the actual downloaded version filename. Verify the SHA256SUMS attestation too
before trusting it; a checksum alone is not a signature. The workflow uploads
`provenance.sigstore.json`, which can be supplied with `--bundle` for local bundle
verification (trust material may still need downloading). Check the verified source
commit and signer identity. Reject altered artifacts or an unexpected signer.

Rollback means deploying a previously verified source/image and restoring its
compatible data backup. Do not delete existing volumes. No database migration is
introduced by this Phase 0 work.

## Update channels

`ai-workspace --update` re-runs the official installer at the newest release for this install's channel; the data
folder is never touched. `ai-workspace --update --check` only reports.

* `stable` (default): the newest GitHub release whose tag is exactly `vX.Y.Z`. Pre releases and `desktop-v*` tags
  are never offered.
* `edge`: every commit on `main`. Switch with `ai-workspace --update --channel edge` (and back with `stable`).

The channel is saved per install in `~/.ai-workspace/install.json` and survives updates. `ai-workspace --status`
checks GitHub for a newer stable release; a plain `ai-workspace` launch makes no network request and only repeats
the result of the last check ("Update available"). The desktop app has its own signed updater
(docs/desktop-release.md).
