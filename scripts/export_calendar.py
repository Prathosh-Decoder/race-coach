"""Export the day list from a calendar page (the HTML that built the .ics files) to JSON.

    python scripts/export_calendar.py "~/Desktop/Race to 21.1.html" seed/raw/prathosh_calendar.json

Runs the page's own `const P=[...]` data block in Node, so the numbers match the page exactly.
"""
import os
import re
import subprocess
import sys
import tempfile


def extract(html_path: str) -> str:
    s = open(os.path.expanduser(html_path), encoding="utf-8").read()
    js = max(re.findall(r"<script[^>]*>(.*?)</script>", s, re.S), key=lambda x: "const P=[" in x)
    i = js.find("const P=[")
    if i < 0:
        raise SystemExit("No `const P=[` day list found in this page.")
    depth, end = 0, None
    for j in range(i + len("const P="), len(js)):
        if js[j] == "[":
            depth += 1
        elif js[j] == "]":
            depth -= 1
            if depth == 0:
                end = j + 1
                break
    prog = js[:i] + js[i:end] + ";\nconsole.log(JSON.stringify(P,null,1));"
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as f:
        f.write(prog)
    try:
        return subprocess.check_output(["node", f.name], text=True)
    finally:
        os.unlink(f.name)


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit(__doc__)
    out = extract(sys.argv[1])
    open(sys.argv[2], "w").write(out)
    print(f"Wrote {sys.argv[2]}")
