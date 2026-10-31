"""Word handling shared by dedupe, the blind index and matching (same tiny stemmer as adapt.knowledge)."""
import re

STOP = set("a an the and or of to in on for with at by from is are was were be been it this that my your our i me you "
           "we they he she them his her their as do does did not no yes so if but than then there here have has had "
           "will would can could should about into over under very just also what who where when which how".split())


def stem(w: str) -> str:
    if len(w) > 4 and w.endswith("ies"):
        w = w[:-3] + "y"
    elif len(w) > 5 and w.endswith("ing"):
        w = w[:-3]
    elif len(w) > 4 and w.endswith("ed"):
        w = w[:-2]
    elif len(w) > 3 and w.endswith("s") and not w.endswith("ss"):
        w = w[:-1]
    return w[:-1] if len(w) > 3 and w.endswith("e") else w


def words(text: str) -> list[str]:
    out = []
    for w in re.findall(r"[a-z0-9][a-z0-9'+#.@-]*", (text or "").lower()):
        w = w.strip(".'-")
        if len(w) < 2 or w in STOP:
            continue
        out.append(stem(w))
    return out


def norm_name(name: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9@. ]", " ", (name or "").lower()).split())
