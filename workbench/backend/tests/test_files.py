"""Files: every kind of file gives something useful; analysis, compare/redline, outputs and the Python sandbox."""

import pytest

from make_fixtures import build


@pytest.fixture(scope="module")
def fx(tmp_path_factory):
    return build(tmp_path_factory.mktemp("fx"))


def test_every_kind_of_file_is_read(fx):
    from app import files

    expect = {"budget.xlsx": "Timber", "sales.csv": "North", "contract_v1.docx": "30 days", "report.pdf": "passed inspection",
              "update.pptx": "Revenue up 12%", "notes.md": "Alex to order pipe", "data.json": "Tower B", "orders.xml": "sku",
              "analysis.ipynb": "np.arange", "paper.tex": "x^2-5x+6", "script.py": "Ledger", "readings.parquet": "temp",
              "experiment.h5": "signal", "galaxy.fits": "M31", "photo.png": "64", "voice.wav": "", "plan.dxf": "KITCHEN",
              "mail.eml": "variation 4 is approved", "bundle.zip": "notes.md", "mystery.bin": "PART-NUMBER-XJ9"}
    for name, needle in expect.items():
        d = files.extract(fx[name])
        blob = " ".join(c["text"] for x in files.walk(d) for c in x["chunks"]) + " " + str(d["meta"]) + " " + str(d["tables"]) + " " + str([c["name"] for c in d["children"]])
        assert needle in blob, (name, d["kind"], blob[:300])
        assert d["kind"] != "error", (name, d["notes"])


def test_citations_point_to_page_and_paragraph(fx):
    from app import files

    pdf = files.extract(fx["report.pdf"])
    assert pdf["chunks"][0]["loc"].startswith("page 1")
    doc = files.extract(fx["contract_v1.docx"])
    assert any("¶" in c["loc"] or "paragraph" in c["loc"] for c in doc["chunks"])
    assert "not legal advice" in str(doc["meta"]["how_to_answer"]).lower()
    tex = files.extract(fx["paper.tex"])
    assert "sympy" in str(tex["meta"]["how_to_answer"]).lower() or "latex" in str(tex["meta"]["how_to_answer"]).lower()


def test_spreadsheet_keeps_formulas_and_zip_is_safe(fx):
    from app import files

    x = files.extract(fx["budget.xlsx"])
    assert "=SUM(C2:C6)" in str(x)
    z = files.extract(fx["bundle.zip"])
    names = [c["name"] for c in z["children"]]
    assert any("notes.md" in n for n in names) and not any("evil" in n for n in names)
    eml = files.extract(fx["mail.eml"])
    assert any("variation.csv" in c["name"] for c in eml["children"])


def test_library_read_search_analyse_compare_and_outputs(client, fx):
    from app import analysis

    ids = {n: analysis.add(n, src=fx[n])["id"] for n in ("budget.xlsx", "contract_v1.docx", "contract_v2.docx", "sales.csv")}
    r = analysis.read(ids["contract_v1.docx"])
    assert "Payment" in str(r) and "cite" in str(r).lower()
    hits = analysis.search("liability")
    assert hits and "contract" in str(hits[0]).lower()
    a = analysis.analyse_spreadsheet(ids["budget.xlsx"])
    assert "Cost" in str(a) and "duplicate" in str(a).lower()
    assert a["sheets"][0]["chart"]["file"]
    s = analysis.analyse_spreadsheet(ids["sales.csv"])
    assert "revenue" in str(s)
    c = analysis.compare(ids["contract_v1.docx"], ids["contract_v2.docx"])
    assert "<del>" in str(c) and "<ins>" in str(c) and "14" in str(c)
    w = analysis.make_word("Changes", "# Summary\n\nPayment terms shortened.", compare_with=c)
    assert w["name"].endswith(".docx")
    x = analysis.make_excel("Out", {"Sheet": [{"a": 1, "b": 2}]})
    p = analysis.make_pdf("Report", "Hello **world**")
    for o in (w, x, p):
        assert client.get(f"/api/files/{o['id']}/download").status_code == 200


def test_upload_and_api(client, fx):
    r = client.post("/api/files/upload", params={"name": "update.pptx"}, content=fx["update.pptx"].read_bytes())
    assert r.status_code == 200, r.text
    fid = r.json()["id"]
    d = client.get(f"/api/files/{fid}").json()
    assert d["extract"]["kind"] .lower().startswith(("pptx", "powerpoint"))
    assert client.get("/api/files").json()
    caps = client.get("/api/files/capabilities").json()
    assert caps["readers"]["Excel"] and caps["readers"]["Maths (sympy)"]
    assert client.delete(f"/api/files/{fid}").status_code == 200


def test_sandbox_computes_charts_and_maths(client, fx):
    from app import analysis, compute

    fid = analysis.add("sales.csv", src=fx["sales.csv"])["id"]
    code = """
import pandas as pd, sympy as sp, matplotlib.pyplot as plt
df = pd.read_csv('inputs/sales.csv', sep=';')
print('TOTAL', df.revenue.sum())
x = sp.symbols('x')
print('ROOTS', sp.solve(x**2 - 5*x + 6, x))
df.groupby('region').units.sum().plot(kind='bar')
"""
    r = compute.run(code, [fid])
    assert r["status"] == "ok", r
    assert "TOTAL 825.0" in r["stdout"] and "ROOTS [2, 3]" in r["stdout"]
    assert any(o["name"].endswith(".png") for o in r["outputs"])


def test_sandbox_blocks_network_and_private_files(client):
    from app import compute

    r = compute.run("import socket\nsocket.create_connection(('example.com', 80))")
    assert r["status"] != "ok"
    from app import config

    r = compute.run(f"print(open({str(config.DB_PATH)!r}, 'rb').read(20))")
    assert r["status"] != "ok"
    r = compute.run("import subprocess\nsubprocess.run(['ls'])")
    assert r["status"] != "ok"
