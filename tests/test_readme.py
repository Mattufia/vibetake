# /// script
# requires-python = ">=3.10,<3.14"
# ///
"""README.md and its Spanish copy README.es.md keep the same structure.

ponytail: compares shape (headings, code, tables, list items, test commands), not
meaning; a sentence changed in one and not the other still needs a human eye.
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def shape(text: str) -> dict:
    code = re.findall(r"```.*?```", text, re.S)
    prose = re.sub(r"```.*?```", "", text, flags=re.S)
    return {
        "heading levels": [len(m) for m in re.findall(r"^(#+) ", prose, re.M)],
        "code blocks": len(code),
        "commands in code": [l.split(" #")[0].strip() for b in code for l in b.splitlines() if l.startswith(("/", "uv "))],
        "table rows": len(re.findall(r"^\|", prose, re.M)),
        "list items": len(re.findall(r"^\s*(?:-|\d+\.) ", prose, re.M)),
        "test commands": [l.split("#")[0].strip() for l in text.splitlines() if l.startswith("uv run tests/")],
    }


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    en = (ROOT / "README.md").read_text(encoding="utf-8")
    es = (ROOT / "README.es.md").read_text(encoding="utf-8")
    a, b = shape(en), shape(es)
    checks = {f"same {k}": a[k] == b[k] for k in a}
    checks["English links to Spanish"] = "(README.es.md)" in en
    checks["Spanish links to English"] = "(README.md)" in es
    tests = {p.name for p in (ROOT / "tests").glob("test_*.py")}
    listed = {Path(c.split()[-1]).name for c in a["test commands"]}
    checks["every test listed"] = tests == listed
    for name, ok in checks.items():
        print(f"{'PASS' if ok else 'FAIL'}  {name}")
        if not ok and name.startswith("same "):
            k = name[5:]
            print(f"      en: {a[k]}\n      es: {b[k]}")
    sys.exit(0 if all(checks.values()) else 1)


if __name__ == "__main__":
    main()
