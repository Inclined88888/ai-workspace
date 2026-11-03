"""Count words, sentences and reading time for a text file."""
import re
import sys
from pathlib import Path


def main() -> None:
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        print("usage: count.py FILE")
        return
    text = Path(sys.argv[1]).read_text(encoding="utf-8")
    words = len(text.split())
    sentences = len([s for s in re.split(r"[.!?]+", text) if s.strip()])
    print(f"words={words} sentences={sentences} reading_minutes={max(1, round(words / 230))}")


if __name__ == "__main__":
    main()
