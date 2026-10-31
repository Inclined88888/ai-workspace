"""Assistant tools for files, analysis and work sites. assistant.py adds these to its registry with one line."""

from __future__ import annotations

import json

from . import analysis, compute, sites


def build(Tool, S, I, B) -> list:  # noqa: N803 - the registry's own constructors
    def ids(v) -> list[int]:
        if isinstance(v, list):
            return [int(x) for x in v]
        return [int(x) for x in str(v or "").replace("#", "").replace(" ", "").split(",") if x.strip().isdigit()]

    return [
        # Files: any type. Extracts are third-party content (a document can carry injected instructions).
        Tool("list_files", "List files the user added on the Files page (ids, names, kinds, one-line summaries).", {},
             lambda: [{k: f[k] for k in ("id", "name", "kind", "summary", "source", "created_at")} for f in analysis.listing(100)]),
        Tool("read_file", "Read any file the user added (Excel, Word, PDF, PowerPoint, CSV, email, zip, drawings, images, code, "
             "notebooks, LaTeX, scientific data...). Returns passages with their location for citing, tables and notes on anything "
             "unreadable. Use part for long files and query to jump to relevant passages.",
             {"file": S("file id from list_files (or a path on this computer in the desktop app)"), "part": I(), "query": S()},
             lambda file, part=1, query="": analysis.read(file, part, query), ("file",), untrusted=True),
        Tool("search_files", "Find passages across the user's files, e.g. a clause, a figure or a name. Returns quotes with file and location.",
             {"query": S(), "file_ids": S("optional, comma separated")}, lambda query, file_ids="": analysis.search(query, ids(file_ids) or None),
             ("query",), untrusted=True),
        Tool("analyse_spreadsheet", "Statistics for every column of a spreadsheet or table file (sums, averages, ranges, dates, most "
             "common values, duplicates, formulas) plus an automatic chart.", {"file": S(), "sheet": S()},
             lambda file, sheet="": analysis.analyse_spreadsheet(file, sheet), ("file",), untrusted=True),
        Tool("compare_files", "Compare two versions of a document or spreadsheet: every changed passage with a redline and its "
             "location in both, and changed cells.", {"a": S("older file id"), "b": S("newer file id")},
             lambda a, b: analysis.compare(a, b), ("a", "b"), untrusted=True),
        Tool("describe_image", "Look at a picture or scanned page from a file (photos, plans, screenshots, scans) with the current model.",
             {"file": S(), "question": S()}, analysis.describe_image, ("file",), untrusted=True),
        Tool("run_python", "Run Python in a local sandbox for real calculations: pandas, numpy, matplotlib, sympy (exact maths, "
             "step checks). Files listed in file_ids are in ./inputs/<name>; save outputs to ./out/; open matplotlib figures are "
             "saved as charts automatically. No network. Print results.",
             {"code": S(), "file_ids": S("comma separated file ids")}, lambda code, file_ids="": compute.run(code, ids(file_ids)), ("code",),
             effect="write"),
        Tool("make_document", "Create a new file for the user: format docx (Word report), pdf, or xlsx (rows as JSON list of objects). "
             "body is Markdown for docx/pdf.", {"title": S(), "format": S("docx, pdf or xlsx"), "body": S(), "rows": S("JSON array for xlsx")},
             lambda title, format="docx", body="", rows="": _make(title, format, body, rows), ("title",), effect="write"),
        Tool("edit_spreadsheet", "Save a copy of an Excel file with cell edits applied. edits: JSON list of {sheet, cell, value}.",
             {"file": S(), "title": S(), "edits": S()}, lambda file, edits, title="Edited": analysis.make_excel(title, None, file, json.loads(edits)),
             ("file", "edits"), effect="write"),
        # Work sites: any website the user added on the Sites page
        Tool("list_sites", "Websites the user works in (added on the Sites page), with what was found on each.", {},
             lambda: [{k: s[k] for k in ("id", "name", "url", "status", "status_detail", "last_checked", "records", "last_digest")} |
                      {"collections": [c["name"] for c in s["structure"].get("collections", [])],
                       "forms": [f"{i}: {f['name']} ({f['submit']})" for i, f in enumerate(s["structure"].get("forms", []))][:20]}
                      for s in sites.listing()]),
        Tool("check_site", "Read a work site now (read only) and get what changed since last time.", {"site_id": I()},
             lambda site_id: sites.check(site_id), ("site_id",), untrusted=True),
        Tool("site_records", "Records found on a work site (jobs, shifts, tasks, tickets, invoices...), with status, dates, who, "
             "overdue flags and links.", {"site_id": I(), "collection": S()},
             lambda site_id, collection="": sites.records(site_id, collection)[:300], ("site_id",), untrusted=True),
        Tool("site_schedule", "The work site's dated items as a timeline; with add_to_calendar true they are added to the user's calendar.",
             {"site_id": I(), "add_to_calendar": B()}, _schedule, ("site_id",), untrusted=True),
        Tool("watch_site", "Set up a recurring check from the user's own words, e.g. 'check my portal every morning at 7 and tell me "
             "which jobs are overdue'. Read only.", {"request": S(), "site_id": I()},
             lambda request, site_id=None: sites.watch_from_text(request, site_id), ("request",),
             when=lambda ctx: ctx.channel != "schedule", effect="persistent"),
        Tool("site_file", "Download a file the work site links to into Files, then read it with read_file.", {"site_id": I(), "url": S()},
             lambda site_id, url: sites.fetch_file(site_id, url), ("site_id", "url"), untrusted=True),
        Tool("site_action", "Change something on a work site by filling one of its forms (update a status, reply, submit a timesheet). "
             "form is the form number or name from list_sites; values maps field names or labels to values. ALWAYS needs the user's "
             "approval; never available in unattended runs.", {"site_id": I(), "form": S(), "values": S("JSON object")},
             _act, ("site_id", "form", "values"), effect="external", untrusted=True, always_ask=True,
             when=lambda ctx: ctx.channel not in ("schedule", "agent")),
    ]


def _make(title: str, fmt: str, body: str, rows: str):
    if fmt == "xlsx":
        data = json.loads(rows) if rows else [{"Notes": line} for line in body.splitlines() if line.strip()]
        return analysis.make_excel(title, {"Sheet1": data} if isinstance(data, list) else data)
    if fmt == "pdf":
        return analysis.make_pdf(title, body)
    return analysis.make_word(title, body)


async def _schedule(site_id: int, add_to_calendar: bool = False):
    out = {"items": sites.timeline(site_id)[:200], "ics": f"/api/sites/{site_id}/calendar.ics"}
    if add_to_calendar:
        out["calendar"] = await sites.to_calendar(site_id)
    return out


async def _act(site_id: int, form: str, values: str):
    """Runs only after the user approved this exact call in the app (always_ask)."""
    vals = json.loads(values) if isinstance(values, str) else values
    a = sites.propose(site_id, form, vals)
    return await sites.decide(a["id"], True)
