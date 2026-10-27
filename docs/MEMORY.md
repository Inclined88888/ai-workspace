# How ROOKS finds memories

`workbench/backend/app/memory.py`

## What runs

1. **Words.** SQLite FTS5 over memory text with porter stemming, ranked by bm25. This was the only method before.
2. **Related words.** The same search, with the query widened in two ways:
   - by a small table of everyday equivalents (partner, wife, husband…);
   - by spelling correction against words that actually appear in your memories ("dentst" becomes "dentist").

   Common words like "my", "what" and "the" are ignored.
3. **Meaning (optional, off by default).** Uses an embedding model from a provider you already connected (for
   example Ollama with `nomic-embed-text`). Turn it on through the API: `PUT /api/memory/embeddings` with
   `{"model": "<provider id>/<model>"}`, which tests the model before saving it. Vectors are stored in SQLite
   (`memory_vectors`) and compared in Python. If the model is unreachable, keyword search still answers and the
   error shows in `GET /api/memory/status`.

The lists are merged by reciprocal rank fusion and weighted by confidence. Confidence by source:

| Source | Confidence |
|---|---|
| You | 1.0 |
| Assistant `remember` | 0.8 |
| Import | 0.7 |
| Learned automatically | 0.6 |

Near duplicates are folded. Each result says how it was found (`matched_by`).

## Corrections and provenance

- Each memory keeps its source, its conversation, its confidence and the wording it had before its last change.
- Editing a memory makes you the source, sets confidence to 1.0, and keeps the old wording.
  `POST /api/memories/{id}/revert` brings the old wording back.
- Learning can replace an outdated memory instead of adding a contradiction.
- Edits, reverts and deletions go into the activity log.
- Secrets and card numbers are refused on every write path (see `sensitive.py`).
- With Memory set to Off in Permissions, nothing is learned and nothing remembered is shown to the model.

## Benchmark

Reproduce with the tests in `tests/test_memory.py`. They use the sets in `tests/data/`. Scores are recall@5 (the
right memory is in the top 5) and mean reciprocal rank (MRR).

| Set | Queries | Keyword baseline | Hybrid without equivalents table | Hybrid (as shipped) |
|---|---|---|---|---|
| `memory_eval.json` (dev) | 40 | 0.62 (MRR 0.56) | 0.70 (MRR 0.63) | 1.00 (MRR 0.90) |
| `memory_eval_heldout.json` | 25 | 0.44 (MRR 0.42) | 0.48 (MRR 0.46) | 0.92 (MRR 0.85) |

**Read this before trusting the numbers.**

- **The sets are not independent.** Both were written by the same author as the retriever, before it was built.
  The equivalents table was written afterwards by someone who had read both sets, and it contains words from both.
  The "as shipped" column therefore overstates accuracy on wording nobody anticipated.
- **The ablation is the honest guide to the general gain.** The column without the equivalents table shows what the
  general mechanisms (spelling correction, stopwords, fusion) add on their own: a modest 0.04 to 0.08.
- **The table only covers anticipated wording.** It helps when users phrase things in common ways, and does nothing
  for wording it does not contain.
- **Embeddings are the general answer, but they are unmeasured.** Meaning-based search is not benchmarked here,
  because this environment has no embedding model. The test suite checks that it works end to end with a stand-in
  model that does not capture meaning.
