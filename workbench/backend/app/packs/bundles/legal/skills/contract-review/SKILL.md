---
name: legal-contract-review
description: Review a contract or legal document clause by clause, check defined terms, cross-references and citations, and compare versions.
---
# Contract and document review

Use this when the user shares a contract, lease, policy, judgment or other legal document.

1. Read it with read_file and keep the location of every passage you rely on.
2. Run legal_check_document on the file. It lists defined terms used but not defined, terms defined but not used, clause
   references that point nowhere, and citations whose format looks wrong.
3. For two versions, use compare_files (older first) and summarise what changed, quoting each change with its location.
4. Offer clauses from the user's clause library (legal_find_clause) when a clause is missing or weak.
5. Cite every point as [file, location]. Link legislation and cases to a public source when you can.

Always end with: "This is general information to help you review the document, not legal advice." Recommend a qualified
lawyer for decisions. Citation checks only check the format; say so.
