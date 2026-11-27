#!/usr/bin/env python3
"""Static structural check of db/migrations/*.sql (no PostgreSQL required).

What it verifies:
  * all 17 tables required by the project spec exist (+ module 20 tables);
  * every REFERENCES / FOREIGN KEY target table and column exists and the
    target table is created BEFORE the referencing table;
  * plain-column indexes point at real columns;
  * parentheses, quotes and $$ blocks are balanced in every statement.

What it does NOT verify: SQL syntax details or runtime behaviour. For that
run the migration and db/tests/constraints.sql on a real PostgreSQL.
"""
import re
import sys
from pathlib import Path

REQUIRED = {
    "users", "profiles", "roles", "subscriptions", "translation_history",
    "sign_dictionary", "sign_categories", "datasets", "dataset_samples",
    "ai_models", "model_metrics", "feedback", "reports", "notifications",
    "api_keys", "system_settings", "audit_logs",
}
MODULE20 = {
    "sign_proposals", "sign_languages", "sign_translations", "signers",
    "consents", "dataset_versions", "dataset_version_samples", "model_signs",
    "jobs", "plans", "permissions", "role_permissions", "user_roles",
    "refresh_tokens", "languages",
}


def strip_comments(sql: str) -> str:
    return re.sub(r"--[^\n]*", "", sql)


def split_statements(sql: str):
    """Split on ';' outside of quotes and $$ blocks."""
    out, buf, i, n = [], [], 0, len(sql)
    in_q = in_dollar = False
    while i < n:
        if not in_q and sql.startswith("$$", i):
            in_dollar = not in_dollar
            buf.append("$$")
            i += 2
            continue
        c = sql[i]
        if not in_dollar and c == "'":
            in_q = not in_q
        if c == ";" and not in_q and not in_dollar:
            out.append("".join(buf).strip())
            buf = []
        else:
            buf.append(c)
        i += 1
    if "".join(buf).strip():
        out.append("".join(buf).strip())
    if in_q or in_dollar:
        raise SystemExit("ERROR: unbalanced quote or $$ block")
    return [s for s in out if s]


def top_level_items(body: str):
    items, depth, cur, in_q = [], 0, [], False
    for ch in body:
        if ch == "'":
            in_q = not in_q
        if not in_q:
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
            elif ch == "," and depth == 0:
                items.append("".join(cur).strip())
                cur = []
                continue
        cur.append(ch)
    if "".join(cur).strip():
        items.append("".join(cur).strip())
    return items


def table_body(stmt: str):
    start = stmt.index("(")
    depth = 0
    for i in range(start, len(stmt)):
        if stmt[i] == "(":
            depth += 1
        elif stmt[i] == ")":
            depth -= 1
            if depth == 0:
                return stmt[start + 1:i]
    raise SystemExit("ERROR: unbalanced parentheses in: " + stmt[:60])


def main(path: str) -> int:
    sql = strip_comments(Path(path).read_text(encoding="utf-8"))
    stmts = split_statements(sql)
    tables, errors = {}, []
    refs = []   # (referencing_table, target_table, [target_cols])
    idx = []    # (table, [cols])

    for st in stmts:
        # balanced parentheses outside $$ blocks / quotes
        plain = re.sub(r"\$\$.*?\$\$", "", st, flags=re.S)
        plain = re.sub(r"'(?:[^']|'')*'", "''", plain)
        if plain.count("(") != plain.count(")"):
            errors.append("unbalanced parentheses: " + st[:70].replace("\n", " "))

        m = re.match(r"CREATE TABLE (\w+)\s*\(", st)
        if m:
            name = m.group(1)
            if name in tables:
                errors.append(f"table {name} created twice")
            cols = set()
            for item in top_level_items(table_body(st)):
                head = item.split()[0].upper()
                if head in ("CHECK", "UNIQUE", "PRIMARY", "FOREIGN", "CONSTRAINT"):
                    fk = re.search(r"FOREIGN KEY\s*\(([^)]*)\)\s*REFERENCES\s+(\w+)\s*\(([^)]*)\)", item)
                    if fk:
                        refs.append((name, fk.group(2), [c.strip() for c in fk.group(3).split(",")]))
                    continue
                cols.add(item.split()[0])
                r = re.search(r"REFERENCES\s+(\w+)\s*\((\w+)\)", item)
                if r:
                    refs.append((name, r.group(1), [r.group(2)]))
            tables[name] = cols
            continue

        m = re.match(r"CREATE (?:UNIQUE )?INDEX \w+ ON (\w+)\s*\((.*)\)", st, flags=re.S)
        if m:
            parts = [p.strip() for p in top_level_items(m.group(2))]
            cols = [p.split()[0] for p in parts if re.fullmatch(r"\w+(\s+(ASC|DESC))?", p.split(" WHERE")[0].strip())]
            idx.append((m.group(1), cols))

    for src, tgt, cols in refs:
        if tgt not in tables:
            errors.append(f"{src}: references unknown or later table {tgt}")
            continue
        for c in cols:
            if c not in tables[tgt]:
                errors.append(f"{src}: references missing column {tgt}.{c}")
    for t, cols in idx:
        if t not in tables:
            errors.append(f"index on unknown table {t}")
            continue
        for c in cols:
            if c not in tables[t]:
                errors.append(f"index on {t}: missing column {c}")

    missing = REQUIRED - tables.keys()
    if missing:
        errors.append("required tables missing: " + ", ".join(sorted(missing)))
    missing20 = MODULE20 - tables.keys()
    if missing20:
        errors.append("module-20 tables missing: " + ", ".join(sorted(missing20)))

    print(f"statements: {len(stmts)}  tables: {len(tables)}  foreign keys: {len(refs)}  indexes: {len(idx)}")
    print(f"spec tables present: {len(REQUIRED & tables.keys())}/{len(REQUIRED)}")
    if errors:
        print("FAILED:")
        for e in errors:
            print("  -", e)
        return 1
    print("OK: structural checks passed (this is NOT a substitute for running on PostgreSQL)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "db/migrations/0001_initial.sql"))
