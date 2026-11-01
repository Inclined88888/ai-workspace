"""Tool results too long to send inline are kept in full, not cut off.

Before this, assistant.run_tool cut every result at 20,000 characters and the model never learned the rest existed:
a long email thread, web page or file listing silently lost its end. Now an oversized result is stored in
`tool_results` and the model receives its start and its end (where totals and final lines usually are), the size,
and an id. The read_tool_result tool reads any part of it by offset, or finds the lines that mention a phrase.

Results belong to their conversation: reads are scoped to the conversation that produced them and the rows are
deleted with it. A result from an untrusted tool (web, email) stays untrusted when it is read back.
"""

from __future__ import annotations

import secrets

from . import db

INLINE_CHARS = 20_000        # results up to this size are sent whole, exactly as before
HEAD_CHARS = 14_000          # an oversized result shows its start ...
TAIL_CHARS = 4_000           # ... and its end
READ_CHARS = 12_000          # most one read_tool_result call returns (stays inline after JSON escaping)
STORE_CHARS = 2_000_000      # most kept per result (the same ceiling files.py keeps per file)
FIND_HITS = 20
FIND_CONTEXT = 300


def fit(conversation_id: int | None, tool: str, out: str, untrusted: bool) -> str:
    """The text to give the model for one tool result: unchanged when it fits, else head + tail + how to read more."""
    if len(out) <= INLINE_CHARS:
        return out
    if tool == "read_tool_result":  # a read-back is never stored again; it is already a window onto a stored result
        return out[:INLINE_CHARS] + "\n[... cut; read a smaller part with a later offset ...]"
    if not conversation_id:  # nowhere to keep it: still say plainly that it was cut
        return (out[:HEAD_CHARS] + f"\n\n[... {len(out) - HEAD_CHARS - TAIL_CHARS:,} characters not shown; this result "
                f"could not be stored ...]\n\n" + out[-TAIL_CHARS:])
    rid = secrets.token_hex(6)
    kept = out[:STORE_CHARS]
    db.run("INSERT INTO tool_results(id, conversation_id, tool, untrusted, content, created_at) VALUES (?, ?, ?, ?, ?, ?)",
           rid, conversation_id, tool, int(untrusted), kept, db.now())
    lost = f" Only the first {STORE_CHARS:,} characters were kept." if len(out) > STORE_CHARS else ""
    hidden = len(kept) - HEAD_CHARS - TAIL_CHARS
    return (f"[Result {rid} from {tool} is {len(out):,} characters; showing the first {HEAD_CHARS:,} and the last "
            f"{TAIL_CHARS:,}.{lost} Read the rest with read_tool_result(result_id=\"{rid}\", offset=...) or search it "
            f"with read_tool_result(result_id=\"{rid}\", find=\"...\"). Do not guess what the hidden part says.]\n"
            + kept[:HEAD_CHARS]
            + f"\n\n[... {hidden:,} characters hidden, from offset {HEAD_CHARS:,} to {len(kept) - TAIL_CHARS:,}; "
              f"read_tool_result(result_id=\"{rid}\", offset={HEAD_CHARS}) continues here ...]\n\n"
            + kept[-TAIL_CHARS:])


def _row(conversation_id: int | None, result_id: str) -> dict | None:
    if not conversation_id or not isinstance(result_id, str) or not result_id.isalnum():
        return None
    return db.one("SELECT id, tool, untrusted, content FROM tool_results WHERE id = ? AND conversation_id = ?",
                  result_id.strip().lower(), conversation_id)


def read(conversation_id: int | None, result_id: str, offset: int = 0, length: int = READ_CHARS,
         find: str = "") -> dict:
    """One part of a stored result, or the passages that mention `find`. Raises LookupError for an unknown id."""
    row = _row(conversation_id, result_id)
    if not row:
        raise LookupError(f"No stored result {result_id!r} in this conversation.")
    text = row["content"]
    base = {"result_id": row["id"], "tool": row["tool"], "total_chars": len(text), "untrusted": bool(row["untrusted"])}
    if find:
        needle, low, hits, at = find.lower(), text.lower(), [], 0
        while len(hits) < FIND_HITS:
            i = low.find(needle, at)
            if i < 0:
                break
            start = max(0, i - FIND_CONTEXT)
            hits.append({"offset": start, "text": text[start:i + len(find) + FIND_CONTEXT]})
            at = i + len(needle)
        return {**base, "find": find, "matches": hits, "more_matches": len(hits) == FIND_HITS}
    try:
        offset, length = max(0, int(offset)), max(1, min(int(length), READ_CHARS))
    except (TypeError, ValueError):
        raise ValueError("offset and length must be whole numbers") from None
    end = min(len(text), offset + length)
    return {**base, "offset": offset, "end": end, "text": text[offset:end], "more": end < len(text),
            **({"next_offset": end} if end < len(text) else {})}
