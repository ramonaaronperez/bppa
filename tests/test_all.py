"""python tests/test_all.py — offline checks: scraper parser on saved B-R HTML + BPPA anchors."""
import sys, re, html
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "scraper"), str(ROOT / "build")]
import bref_current as B
import duckdb, leaderboards as L

ok = True
def check(cond, msg):
    global ok
    print(("PASS " if cond else "FAIL ") + msg); ok &= bool(cond)

# 1. parser on real page excerpts (batting-pitching table lives inside an HTML comment)
for fname, page, tid, spec, hand in B.JOBS:
    rows, miss = B.parse((ROOT / f"tests/fixtures/{page}.html").read_text(), 2026, tid, spec, hand)
    check(len(rows) == 6 and not miss, f"{page}: 6 rows, all {len(spec)} columns resolved")
    check({r['RowType'] for r in rows} == {'season', 'stint'}, f"{page}: season + stint rows")
    check(all(' ' not in r[list(spec)[2]] for r in rows), f"{page}: no NBSP in names")
r = B.parse((ROOT / "tests/fixtures/standard-batting.html").read_text(), 2026, *B.JOBS[0][2:])[0][0]
check((r['Player'], r['Bats'], r['PlayerID']) == ('Pete Crow-Armstrong', 'L', 'crowape01'), "handedness marker stripped")

# 2. BPPA anchors (track 30) through the leaderboard data layer
con = duckdb.connect(); L.load(con, ROOT / "docs")
q = lambda s: con.sql(s).fetchone()
check(abs(q("SELECT bppa FROM season WHERE player_id='ruthba01' AND year=1921 AND side='BAT'")[0] - 0.880231) < 1e-6, "Ruth 1921 = .880231")
check(abs(q("SELECT bppa FROM season WHERE player_id='bondsba01' AND year=2001 AND side='BAT'")[0] - 0.914157) < 1e-6, "Bonds 2001 = .914157")
check(q("SELECT count(DISTINCT player_id), sum(pa), sum(bases) FROM allrows WHERE side='BAT' AND team_row AND NOT incomplete AND team='NYY' AND year=1927") == (24, 6222, 3296), "NYY 1927 team = 24 / 6222 / 3296")
a = q("SELECT count(*), sum(pa), sum(bases)::DOUBLE/sum(pa) FROM season WHERE side='BAT' AND player_id='aaronha01'")
check(a[0] == 23 and a[1] == 13940 and abs(a[2] - 0.606671) < 1e-6, "Aaron career 23 / 13,940 PA / .606671")
print("\nALL PASS" if ok else "\nFAILURES"); sys.exit(0 if ok else 1)
