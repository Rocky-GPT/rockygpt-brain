"""Read-only query of the campus data: python3 q.py "<SQL>" (rows printed one per line)."""
import sqlite3, sys
db = sqlite3.connect("file:/mnt/project-files/night-fix/campus-data-v2-20261102040147.sqlite?mode=ro", uri=True)
for row in db.execute(sys.argv[1]):
    print(" | ".join("" if v is None else str(v) for v in row))
