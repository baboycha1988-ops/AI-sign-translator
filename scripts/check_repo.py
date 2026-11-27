#!/usr/bin/env python3
"""Pre-publication sanity check: required files exist, no secrets/junk/large files, all Python compiles."""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REQUIRED = [
    "README.md", ".gitignore", "requirements.txt", ".env.example", "STATUS.md",
    "db/migrations/0001_initial.sql", "db/tests/constraints.sql",
    "apps/api/app/main.py", "apps/api/requirements.txt", "packages/ml/signml/features.py",
    "packages/ml/requirements.txt", "packages/spec/golden_features.json",
    "scripts/run_tests.sh", "scripts/db_verify.sh", "scripts/check_schema.py",
    "docs/dataset-protocol.md", "docs/er-diagram.md", ".github/workflows/ci.yml",
]
FORBIDDEN_SUFFIX = {".mp4", ".webm", ".mov", ".avi", ".npz", ".onnx", ".pt", ".pth", ".task", ".tflite", ".pem", ".key"}
SKIP_DIRS = {".git", "__pycache__", "node_modules", ".venv"}
SECRET_PATTERNS = [
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"(?i)(api[_-]?key|secret|token|passwd|password)\s*[:=]\s*['\"][A-Za-z0-9_\-/+=]{16,}['\"]"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"ghp_[A-Za-z0-9]{30,}"),
]
MAX_BYTES = 2 * 1024 * 1024


def files():
    for p in ROOT.rglob("*"):
        if p.is_file() and not (set(p.relative_to(ROOT).parts) & SKIP_DIRS):
            yield p


def main():
    problems = []
    for r in REQUIRED:
        if not (ROOT / r).exists():
            problems.append(f"missing required file: {r}")
    n = 0
    for p in files():
        n += 1
        rel = p.relative_to(ROOT)
        if p.name == ".env":
            problems.append(f"{rel}: real .env must not be published")
        if p.suffix.lower() in FORBIDDEN_SUFFIX:
            problems.append(f"{rel}: data/model/secret file type must not be in git")
        if p.stat().st_size > MAX_BYTES:
            problems.append(f"{rel}: larger than {MAX_BYTES // 1024} KB")
        if p.suffix in {".py", ".sh", ".yml", ".yaml", ".md", ".txt", ".json", ".sql", ".html", ".example"} or p.name.startswith(".env"):
            text = p.read_text(encoding="utf-8", errors="ignore")
            for pat in SECRET_PATTERNS:
                if pat.search(text):
                    problems.append(f"{rel}: looks like a secret ({pat.pattern[:30]}...)")
            if p.suffix == ".py":
                try:
                    compile(text, str(rel), "exec")      # syntax check only, writes nothing
                except SyntaxError as e:
                    problems.append(f"{rel}: syntax error line {e.lineno}: {e.msg}")
    print(f"checked {n} files")
    if problems:
        print("PROBLEMS:")
        for x in problems:
            print("  -", x)
        return 1
    print("OK: required files present, no secrets/data files found, all Python files compile")
    return 0


if __name__ == "__main__":
    sys.exit(main())
