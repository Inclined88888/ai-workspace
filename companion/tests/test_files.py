"""File versioning, undo, verified writes and structured spreadsheet and document editing, on real files."""

import csv
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from rooks_companion.engine import Engine  # noqa: E402


@pytest.fixture()
def e(tmp_path):
    root = tmp_path / "work"
    root.mkdir()
    eng = Engine(None, {"folders": [str(root)], "versions_dir": str(tmp_path / "versions"), "stop_file": str(tmp_path / "STOP")},
                 journal_path=str(tmp_path / "journal.json"))
    eng.n = 0

    def act(kind, approved=False, **args):
        eng.n += 1
        return eng.execute({"id": eng.n, "kind": kind, "args": args, "approved": approved})
    eng.act = act
    return eng, root


def test_replacing_a_file_keeps_a_version_and_undo_restores_it(e):
    eng, root = e
    f = root / "notes.txt"
    f.write_text("original")
    r = eng.act("files_write", path=str(f), text="new")
    assert r.get("refused") and "needs your approval" in r["error"] and f.read_text() == "original"
    r = eng.act("files_write", approved=True, path=str(f), text="new", overwrite=True)
    assert r["ok"] and r["result"]["verified"] and f.read_text() == "new"
    hist = eng.act("files_history", path=str(f))["result"]["entries"]
    assert hist[-1]["summary"].startswith("replaced notes.txt")
    assert eng.act("files_undo", change=r["result"]["change"])["ok"] and f.read_text() == "original"
    assert eng.act("files_undo", change=r["result"]["change"]).get("refused")  # only once


def test_undo_refuses_when_the_file_changed_since(e):
    eng, root = e
    f = root / "a.txt"
    f.write_text("v1")
    r = eng.act("files_write", approved=True, path=str(f), text="v2", overwrite=True)
    f.write_text("v3 by the user")
    u = eng.act("files_undo", change=r["result"]["change"])
    assert u.get("refused") and "changed after ROOKS edited it" in u["error"] and f.read_text() == "v3 by the user"


def test_same_content_is_not_written_twice(e):
    eng, root = e
    f = root / "b.txt"
    assert eng.act("files_write", path=str(f), text="hello")["ok"]
    r = eng.act("files_write", path=str(f), text="hello")
    assert r["ok"] and "nothing written" in r["result"]["note"]
    assert len([x for x in eng.act("files_history")["result"]["entries"] if x.get("path") == str(f)]) == 1


def test_move_copy_mkdir_undo_and_nothing_deleted(e, tmp_path):
    eng, root = e
    (root / "in").mkdir()
    (root / "in" / "inv.txt").write_text("invoice")
    mk = eng.act("files_mkdir", path=str(root / "Invoices"))["result"]
    mv = eng.act("files_move", src=str(root / "in" / "inv.txt"), dst=str(root / "Invoices"))["result"]
    assert (root / "Invoices" / "inv.txt").exists()
    cp = eng.act("files_copy", src=str(root / "Invoices" / "inv.txt"), dst=str(root / "copy.txt"))["result"]
    assert eng.act("files_undo", change=cp["change"])["ok"] and not (root / "copy.txt").exists()
    assert list((tmp_path / "versions" / "undone").iterdir())  # the undone copy was kept, not deleted
    assert eng.act("files_undo", change=mv["change"])["ok"] and (root / "in" / "inv.txt").read_text() == "invoice"
    assert eng.act("files_undo", change=mk["change"])["ok"] and not (root / "Invoices").exists()


def write_csv(path, rows):
    with open(path, "w", newline="") as f:
        csv.writer(f).writerows(rows)


def test_spreadsheet_read_dedupe_update_into_a_new_file(e):
    eng, root = e
    src = root / "invoices.csv"
    write_csv(src, [["Invoice", "Client", "Amount"], ["INV-1", "Acme", "100"], ["INV-2", "Beta", "250"],
                    ["inv-1 ", "acme", "100"], ["INV-3", "Acme", "75"]])
    r = eng.act("sheet_read", path=str(src))["result"]
    assert r["columns"] == ["Invoice", "Client", "Amount"] and r["duplicate_rows"] == [{"row": 4, "same_as_row": 2}]
    d = eng.act("sheet_dedupe", path=str(src))
    assert d["ok"] and d["result"]["removed_rows"] == [4] and d["result"]["path"].endswith("invoices (deduplicated).csv")
    assert src.read_text().count("INV") == 3  # the original is untouched
    u = eng.act("sheet_update", path=d["result"]["path"], edits=[{"row": 3, "column": "Amount", "value": "260"}], output=str(root / "final.csv"))
    assert u["ok"] and u["result"]["verified"]
    rows = list(csv.reader(open(root / "final.csv")))
    assert rows[2] == ["INV-2", "Beta", "260"] and len(rows) == 4
    over = eng.act("sheet_update", path=str(src), edits=[{"row": 2, "column": "C", "value": "1"}], output=str(src))
    assert over.get("refused") and "approval" in over["error"]
    assert eng.act("sheet_update", path=str(src), edits=[{"row": 99, "column": "C", "value": "1"}]).get("refused")


def test_xlsx_and_docx(e):
    openpyxl = pytest.importorskip("openpyxl")
    docx = pytest.importorskip("docx")
    eng, root = e
    wb = openpyxl.Workbook()
    ws = wb.active
    for r in (["SKU", "Stock"], ["A1", 5], ["B2", 0], ["A1", 5]):
        ws.append(r)
    wb.save(root / "stock.xlsx")
    r = eng.act("sheet_dedupe", path=str(root / "stock.xlsx"))
    assert r["ok"], r
    ws2 = openpyxl.load_workbook(r["result"]["path"]).active
    assert [list(x) for x in ws2.iter_rows(values_only=True)] == [["SKU", "Stock"], ["A1", 5], ["B2", 0]]
    d = docx.Document()
    d.add_paragraph("Quarterly report")
    d.add_paragraph("Revenue: TBD")
    d.save(root / "report.docx")
    w = eng.act("doc_write", path=str(root / "report.docx"), replace=[{"find": "TBD", "replace": "$12,400"}])
    assert w["ok"], w
    assert "Revenue: $12,400" in eng.act("doc_read", path=w["result"]["path"])["result"]["content"]
    assert "Revenue: TBD" in eng.act("doc_read", path=str(root / "report.docx"))["result"]["content"]  # original kept


def test_text_document_section_edit_and_missing_text(e):
    eng, root = e
    (root / "plan.md").write_text("# Plan\n## Budget\nTBD\n")
    r = eng.act("doc_write", path=str(root / "plan.md"), replace=[{"find": "## Budget\nTBD", "replace": "## Budget\n$5,000"}])
    assert r["ok"] and "$5,000" in open(r["result"]["path"]).read()
    assert eng.act("doc_write", path=str(root / "plan.md"), replace=[{"find": "nope", "replace": "x"}]).get("refused")
