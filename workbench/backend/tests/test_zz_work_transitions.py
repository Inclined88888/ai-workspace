"""Runs last (by name): every status change any engine made during the whole test run was recorded by the work_events
triggers. Each must be a known status with a phase, and each move must be one the lifecycle in work.py allows. A new
native status or a move nobody declared fails here, instead of showing up as a wrong state in a client."""


def test_every_recorded_status_change_follows_the_lifecycle(client):
    from app import db, work

    rows = db.all("SELECT kind, ref_id, old_status, new_status FROM work_events ORDER BY id")
    assert rows, "the suite produced no status changes; the triggers are not installed"
    unknown, illegal = set(), []
    for r in rows:
        k = work.KINDS[r["kind"]]
        if r["new_status"] not in k.statuses or (r["old_status"] and r["old_status"] not in k.statuses):
            unknown.add((r["kind"], r["old_status"], r["new_status"]))
            continue
        if r["old_status"] is None:
            continue
        old, new = work.phase_of(r["kind"], r["old_status"])[0], work.phase_of(r["kind"], r["new_status"])[0]
        if not work.allowed(old, new):
            illegal.append((r["kind"], r["old_status"], r["new_status"], f"{old}→{new}"))
    kinds = {r["kind"] for r in rows}
    assert not unknown, f"statuses with no phase: {sorted(unknown, key=str)}"
    assert not illegal, f"moves the lifecycle does not allow: {sorted(set(illegal))}"
    assert {"research", "mission", "worker"} <= kinds, kinds
