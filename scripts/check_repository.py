"""Check package boundaries and prevent credential/runtime files entering Git."""

import ast
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN = re.compile(r"(^|/)(?:\.env|.*(?:cookies?|token|secret).*\.json|.*\.(?:pem|key))$", re.I)


def check() -> list[str]:
    failures = []
    tracked = (
        subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True).stdout.decode().split("\0")
    )
    for name in tracked:
        if name and (FORBIDDEN.search(name) or name.startswith(("var/", "temp/", "output/", ".venv/", "venv/"))):
            failures.append(f"Private runtime/credential path tracked: {name}")
    package = ROOT / "src/autoclip"
    legacy = {p.stem for p in package.glob("*/*.py") if p.name != "__init__.py"}
    for source in package.rglob("*.py"):
        tree = ast.parse(source.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            imports = (
                [a.name for a in node.names]
                if isinstance(node, ast.Import)
                else [node.module]
                if isinstance(node, ast.ImportFrom)
                else []
            )
            if any(name in legacy for name in imports):
                failures.append(f"Unqualified internal import: {source.relative_to(ROOT)}:{node.lineno}")
            if isinstance(node, ast.Call) and ast.unparse(node.func) == "sys.path.insert":
                failures.append(f"sys.path mutation inside package: {source.relative_to(ROOT)}")
    return failures


if __name__ == "__main__":
    errors = check()
    print("\n".join(errors) if errors else "Package boundaries and tracked-file safety passed.")
    sys.exit(bool(errors))
