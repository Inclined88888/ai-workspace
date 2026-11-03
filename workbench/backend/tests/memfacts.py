"""What the assistant remembers now lives in the encrypted learn store; tests read it through these helpers."""



def _k():
    from app.adapt import knowledge  # imported late: the app needs the test environment first

    return knowledge


def like(text: str) -> list[dict]:
    return [f for f in _k().all_facts() if text in f["content"]]


def exact(text: str) -> list[dict]:
    return [f for f in _k().all_facts() if f["content"] == text]


def drop(text: str) -> None:
    for f in like(text):
        _k().delete(f["id"])
