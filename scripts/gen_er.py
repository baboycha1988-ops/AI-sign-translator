#!/usr/bin/env python3
"""Generate docs/er-diagram.md (Mermaid) from the SQL migration, so the diagram can never drift from the schema."""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from check_schema import split_statements, strip_comments, table_body, top_level_items  # noqa: E402

SKIP = ("CHECK", "UNIQUE", "PRIMARY", "FOREIGN", "CONSTRAINT")


def main(src="db/migrations/0001_initial.sql", dst="docs/er-diagram.md"):
    sql = strip_comments(Path(src).read_text(encoding="utf-8"))
    ents, rels = {}, []
    for st in split_statements(sql):
        m = re.match(r"CREATE TABLE (\w+)\s*\(", st)
        if not m:
            continue
        name, cols = m.group(1), []
        for item in top_level_items(table_body(st)):
            tok = item.split()
            if tok[0].upper() in SKIP:
                fk = re.search(r"FOREIGN KEY\s*\(([^)]*)\)\s*REFERENCES\s+(\w+)", item)
                if fk:
                    rels.append((fk.group(2), name, fk.group(1).replace(" ", ""), False))
                continue
            typ = re.sub(r"\(.*", "", tok[1]) if len(tok) > 1 else "text"
            keys = []
            if "PRIMARY KEY" in item:
                keys.append("PK")
            r = re.search(r"REFERENCES\s+(\w+)", item)
            if r:
                keys.append("FK")
                rels.append((r.group(1), name, tok[0], "NOT NULL" not in item and "PRIMARY KEY" not in item))
            cols.append(f"    {typ} {tok[0]}" + (f" {','.join(keys)}" if keys else ""))
        ents[name] = cols
    out = ["# ER-диаграмма (сгенерирована из db/migrations/0001_initial.sql)", "",
           "Не редактируйте вручную: `python3 scripts/gen_er.py`.", "", "```mermaid", "erDiagram"]
    for p, c, label, optional in rels:
        out.append(f"  {p} {'|o' if optional else '||'}--o{{ {c} : \"{label}\"")
    for n, cols in ents.items():
        out += [f"  {n} {{"] + cols + ["  }"]
    out.append("```")
    Path(dst).write_text("\n".join(out) + "\n", encoding="utf-8")
    print(f"entities: {len(ents)}  relationships: {len(rels)}  -> {dst}")


if __name__ == "__main__":
    main(*sys.argv[1:])
