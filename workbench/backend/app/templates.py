"""Business workflow templates: ready-made mission plans you fill in and review before anything runs.

A template only writes the plan. The mission then runs like any other: you review the steps, every permission and
approval rule applies, nothing is sent (email is saved as drafts), spreadsheets and documents are written to new files
unless you approve overwriting (and a version is kept), and each step is checked against its "done when".
Templates that work on files need the ROOKS Companion with the folder allowed; the others work in ROOKS alone.
"""

import re

from fastapi import HTTPException

from . import missions

TEMPLATES: list[dict] = [
    {"id": "spreadsheet-cleanup", "title": "Clean up a spreadsheet", "needs": "companion",
     "about": "Find duplicate rows and save a cleaned copy next to the original, then check the copy.",
     "inputs": [{"name": "path", "label": "Spreadsheet (.csv or .xlsx) in an allowed folder", "example": "~/Documents/customers.csv"},
                {"name": "columns", "label": "Columns that identify a duplicate (optional, comma separated)", "example": "Email", "optional": True}],
     "goal": "Clean up {path}: remove duplicate rows into a new file and confirm the result",
     "steps": [
         {"title": "Read {path} and report its columns, row count and duplicate rows", "done_when": "sheet_read returned the columns, row count and duplicates"},
         {"title": "Save a copy without duplicate rows (judged by: {columns}) to a new file next to {path}",
          "done_when": "sheet_dedupe reported the new file path and how many rows it removed", "after": [1]},
         {"title": "Read the new file and confirm it has no duplicates and the expected number of rows",
          "done_when": "sheet_read of the new file shows no duplicates and the row count equals the original minus the removed rows", "after": [2]}]},
    {"id": "invoice-check", "title": "Check invoices against a spreadsheet", "needs": "companion",
     "about": "Read each invoice in a folder and compare amounts and numbers with your records sheet. Reports mismatches; changes nothing.",
     "inputs": [{"name": "folder", "label": "Folder of invoices (text, .md or .docx)", "example": "~/Documents/Invoices"},
                {"name": "sheet", "label": "Records spreadsheet", "example": "~/Documents/invoices.csv"}],
     "goal": "Check the invoices in {folder} against {sheet} and list every mismatch",
     "steps": [
         {"title": "List the invoice files in {folder}", "done_when": "files_list returned the invoice file names"},
         {"title": "Read {sheet} and note the invoice number, client and amount columns", "done_when": "sheet_read returned the rows"},
         {"title": "Read each invoice file and extract its number, client, date and total", "done_when": "every listed invoice was read with doc_read or files_read", "after": [1]},
         {"title": "Compare each invoice with the matching sheet row and write a note titled 'Invoice check' listing matches, mismatches and invoices missing from the sheet",
          "done_when": "a note titled 'Invoice check' exists and every invoice is listed in it", "after": [2, 3]}]},
    {"id": "document-edit", "title": "Process a document", "needs": "companion",
     "about": "Apply your instructions to a document and save the result as a new version file, then check it.",
     "inputs": [{"name": "path", "label": "Document (.txt, .md or .docx)", "example": "~/Documents/contract.docx"},
                {"name": "instructions", "label": "What to change", "example": "Replace 'Acme Pty Ltd' with 'Acme Group Pty Ltd' everywhere"}],
     "goal": "In {path}: {instructions}",
     "steps": [
         {"title": "Read {path}", "done_when": "doc_read returned its text"},
         {"title": "Apply the change ({instructions}) and save it as a new file next to the original",
          "done_when": "doc_write reported the new file path and the number of replacements or the text added", "after": [1]},
         {"title": "Read the new file and confirm the change is there and nothing else changed",
          "done_when": "doc_read of the new file shows the change", "after": [2]}]},
    {"id": "email-drafts", "title": "Draft email replies", "needs": "email",
     "about": "Find the emails that need an answer and save draft replies for you to review. Nothing is sent.",
     "inputs": [{"name": "query", "label": "Which emails", "example": "from:clients unanswered this week"},
                {"name": "tone", "label": "Tone and anything to include (optional)", "example": "brief, friendly, offer a call", "optional": True}],
     "goal": "Draft replies to emails matching '{query}' ({tone}); save drafts only",
     "steps": [
         {"title": "Search email for: {query}", "done_when": "search_email returned the matching messages"},
         {"title": "Read the messages that need a reply", "done_when": "read_email was used for each one", "after": [1]},
         {"title": "Save a draft reply to each ({tone}). Do not send anything", "done_when": "save_email_draft succeeded for each reply", "after": [2]}]},
    {"id": "follow-ups", "title": "Follow up on open items", "needs": "",
     "about": "Collect overdue tasks and upcoming deadlines, draft follow-ups and make a short list for today.",
     "inputs": [{"name": "days", "label": "Look ahead how many days", "example": "7"}],
     "goal": "Find what needs following up in the next {days} days and prepare it",
     "steps": [
         {"title": "Get the agenda for the next {days} days, including overdue tasks", "done_when": "get_agenda returned events and tasks"},
         {"title": "Write a note titled 'Follow-ups' listing each item, who it is with and the next action",
          "done_when": "a note titled 'Follow-ups' exists", "after": [1]},
         {"title": "Create a task for each follow-up that has no task yet", "done_when": "create_task succeeded for each new follow-up, or the note says none were needed", "after": [2]}]},
    {"id": "recurring-report", "title": "Weekly report", "needs": "",
     "about": "Write a report from your tasks, calendar and notes. You can then schedule it (the schedule asks for approval).",
     "inputs": [{"name": "topic", "label": "Report about", "example": "the Smith renovation project"}],
     "goal": "Write a weekly report about {topic}",
     "steps": [
         {"title": "Gather this week's tasks, events and notes about {topic}", "done_when": "list_tasks, get_agenda and search_notes were used"},
         {"title": "Write a note titled 'Weekly report: {topic}' with done, in progress, blocked and next week",
          "done_when": "a note with that title exists", "after": [1]}]},
    {"id": "inventory", "title": "Inventory check", "needs": "companion",
     "about": "Read your stock sheet, find items at or under their reorder level and save a reorder list as a new file.",
     "inputs": [{"name": "path", "label": "Stock spreadsheet", "example": "~/Documents/stock.csv"},
                {"name": "rule", "label": "Reorder rule", "example": "quantity at or below the Reorder column"}],
     "goal": "Find items to reorder in {path} ({rule}) and save a reorder list",
     "steps": [
         {"title": "Read {path} and identify the item, quantity and reorder columns", "done_when": "sheet_read returned the rows"},
         {"title": "List every item where {rule}", "done_when": "the reply lists the items with their quantities, quoted from the sheet", "after": [1]},
         {"title": "Save the reorder list as a new CSV file next to {path}", "done_when": "files_write reported the new file", "after": [2]},
         {"title": "Read the new file and confirm it lists exactly those items", "done_when": "files_read of the new file matches the list", "after": [3]}]},
]
BY_ID = {t["id"]: t for t in TEMPLATES}


def listing() -> list[dict]:
    return [{k: t[k] for k in ("id", "title", "about", "inputs", "needs")} for t in TEMPLATES]


def _fill(text: str, values: dict) -> str:
    return re.sub(r"\{(\w+)\}", lambda m: values.get(m.group(1)) or "(not given)", text)


def start(tid: str, values: dict, project_id: int | None = None, auto_start: bool = False, **budget) -> dict:
    t = BY_ID.get(tid)
    if not t:
        raise HTTPException(404, "Unknown template")
    clean = {}
    for i in t["inputs"]:
        v = str(values.get(i["name"], "")).strip()[:500]
        if not v and not i.get("optional"):
            raise HTTPException(400, f"{i['label']} is required.")
        clean[i["name"]] = v or ("all columns" if i["name"] == "columns" else "")
    steps = [{"title": _fill(s["title"], clean), "done_when": _fill(s["done_when"], clean), "needs_tool": True, "after": s.get("after", [])}
             for s in t["steps"]]
    return missions.create(_fill(t["goal"], clean), project_id=project_id, steps=steps, template=tid, auto_start=auto_start,
                           review_unverified=True, **budget)
