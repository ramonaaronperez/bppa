#!/usr/bin/env python3
"""
BPPA current-season refresh — standalone (no browser, no local server).

Pulls the three Baseball-Reference league pages for the current season and writes
the overlay CSVs the explorer expects, with the exact track-50 headers:

    bppa_current_bat.csv     (38 cols)  <- /leagues/majors/{Y}-standard-batting.shtml
    bppa_current_pit.csv     (35 cols)  <- /leagues/majors/{Y}-batting-pitching.shtml
    bppa_current_pitstd.csv  (41 cols)  <- /leagues/majors/{Y}-standard-pitching.shtml

Rules carried over from the Chrome version (project tracks 10 / 50):
  * cells located by data-stat; the data-stat for each output column is resolved from
    the page's own header labels, so column order changes can't misalign anything
  * tables inside HTML comments are found too
  * thead rows skipped, norank (league avg) skipped, partial_table -> RowType 'stint'
  * non_qual -> Qualified 0 (batting-against page)
  * trailing '*' = L, '#' = S (else R); U+00A0 -> space
  * blanks stay blank

Exit codes: 0 ok / offseason / season not started, 2 scrape or validation failure.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import io
import re
import sys
import time
from pathlib import Path

import requests
from lxml import html as LH

BASE = "https://www.baseball-reference.com/leagues/majors/{y}-{page}.shtml"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/128.0 Safari/537.36 BPPA-refresh")
PACE_S = 4.0  # B-R throttles ~20 req/min; we make 3 requests total anyway

# output column -> accepted header labels on the live page (first match wins).
# Columns handled specially (Year, PlayerID, Bats/Throws, RowType, Qualified) map to None.
BAT = {
    "Year": None, "Rk": ["Rk"], "Player": ["Player"], "PlayerID": None, "Bats": None,
    "Age": ["Age"], "Team": ["Team", "Tm"], "Lg": ["Lg"], "WAR": ["WAR"], "G": ["G"],
    "PA": ["PA"], "AB": ["AB"], "R": ["R"], "H": ["H"], "2B": ["2B"], "3B": ["3B"],
    "HR": ["HR"], "RBI": ["RBI"], "SB": ["SB"], "CS": ["CS"], "BB": ["BB"], "SO": ["SO"],
    "BA": ["BA"], "OBP": ["OBP"], "SLG": ["SLG"], "OPS": ["OPS"], "OPS+": ["OPS+"],
    "rOBA": ["rOBA"], "Rbat+": ["Rbat+"], "TB": ["TB"], "GIDP": ["GIDP", "GDP"],
    "HBP": ["HBP"], "SH": ["SH"], "SF": ["SF"], "IBB": ["IBB"], "Pos": ["Pos", "Pos Summary"],
    "Awards": ["Awards"], "RowType": None,
}
PIT = {
    "Year": None, "Rk": ["Rk"], "Pitcher": ["Player", "Pitcher", "Name"], "PlayerID": None,
    "Throws": None, "Age": ["Age"], "Tm": ["Team", "Tm"], "IP": ["IP"], "PAu": ["PAu"],
    "G": ["G"], "PA": ["PA"], "AB": ["AB"], "R": ["R"], "H": ["H"], "2B": ["2B"],
    "3B": ["3B"], "HR": ["HR"], "SB": ["SB"], "CS": ["CS"], "BB": ["BB"], "SO": ["SO"],
    "BA": ["BA"], "OBP": ["OBP"], "SLG": ["SLG"], "OPS": ["OPS"], "BAbip": ["BAbip"],
    "TB": ["TB"], "GDP": ["GDP", "GIDP"], "HBP": ["HBP"], "SH": ["SH"], "SF": ["SF"],
    "IBB": ["IBB"], "ROE": ["ROE"], "Qualified": None, "RowType": None,
}
PITSTD = {
    "Year": None, "Player": ["Player"], "PlayerID": None, "Throws": None, "Rk": ["Rk"],
    "Age": ["Age"], "Team": ["Team", "Tm"], "Lg": ["Lg"], "W": ["W"], "L": ["L"],
    "W-L%": ["W-L%"], "ERA": ["ERA"], "G": ["G"], "GS": ["GS"], "GF": ["GF"], "CG": ["CG"],
    "SHO": ["SHO"], "SV": ["SV"], "IP": ["IP"], "H": ["H"], "R": ["R"], "ER": ["ER"],
    "HR": ["HR"], "BB": ["BB"], "IBB": ["IBB"], "SO": ["SO"], "HBP": ["HBP"], "BK": ["BK"],
    "WP": ["WP"], "BF": ["BF"], "ERA+": ["ERA+"], "FIP": ["FIP"], "WHIP": ["WHIP"],
    "H9": ["H9"], "HR9": ["HR9"], "BB9": ["BB9"], "SO9": ["SO9"], "SO/W": ["SO/W", "SO/BB"],
    "WAR": ["WAR"], "Awards": ["Awards"], "RowType": None,
}
# columns that must resolve or the run fails (formula inputs + identity)
REQUIRED = {"Rk", "Player", "Pitcher", "Age", "PA", "AB", "H", "2B", "3B", "HR", "BB",
            "HBP", "SB", "CS", "SH", "SF", "IP", "ER", "BF", "W", "L", "Team", "Tm"}

JOBS = [
    ("bppa_current_bat.csv", "standard-batting", "players_standard_batting", BAT, "Bats"),
    ("bppa_current_pit.csv", "batting-pitching", "players_batting_pitching", PIT, "Throws"),
    ("bppa_current_pitstd.csv", "standard-pitching", "players_standard_pitching", PITSTD, "Throws"),
]


def clean(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").replace(" ", " ")).strip()


def fetch(url: str, session: requests.Session) -> str | None:
    for attempt in range(4):
        r = session.get(url, timeout=60)
        if r.status_code == 200:
            return r.text
        if r.status_code == 404:
            return None
        wait = 60 if r.status_code == 429 else 10 * (attempt + 1)
        print(f"  HTTP {r.status_code} on {url}; retry in {wait}s", file=sys.stderr)
        time.sleep(wait)
    raise RuntimeError(f"giving up on {url} (last HTTP {r.status_code})")


def find_table(doc_text: str, table_id: str):
    doc = LH.fromstring(doc_text)
    t = doc.xpath(f'//table[@id="{table_id}"]')
    if t:
        return t[0]
    for c in doc.xpath("//comment()"):  # B-R hides some tables in comments
        if table_id in (c.text or ""):
            t = LH.fromstring(c.text).xpath(f'//table[@id="{table_id}"]')
            if t:
                return t[0]
    return None


def header_map(table) -> dict[str, str]:
    """header label -> data-stat, from the last header row (skips over_header rows)."""
    rows = table.xpath("./thead/tr")
    out: dict[str, str] = {}
    for th in rows[-1].xpath("./th|./td"):
        ds, lab = th.get("data-stat"), clean(th.text_content())
        if ds and lab and lab not in out:
            out[lab] = ds
    return out


def parse(page_text: str, year: int, table_id: str, spec: dict, hand_col: str):
    table = find_table(page_text, table_id)
    if table is None:
        return None, []
    hmap = header_map(table)
    colds: dict[str, str | None] = {}
    missing = []
    for col, labels in spec.items():
        if labels is None:
            continue
        ds = next((hmap[l] for l in labels if l in hmap), None)
        colds[col] = ds
        if ds is None:
            missing.append(col)
    hard = [c for c in missing if c in REQUIRED]
    if hard:
        raise RuntimeError(f"{table_id}: required columns not on page: {hard}; "
                           f"page labels = {list(hmap)}")
    name_col = "Pitcher" if "Pitcher" in spec else "Player"

    rows = []
    for tr in table.xpath("./tbody/tr"):
        cls = tr.get("class") or ""
        if "thead" in cls or "norank" in cls or "league_average" in cls:
            continue
        cells, pid = {}, ""
        for c in tr:
            ds = c.get("data-stat")
            if not ds:
                continue
            cells[ds] = clean(c.text_content())
            if c.get("data-append-csv") and not pid:
                pid = c.get("data-append-csv")
        rec = {}
        for col in spec:
            ds = colds.get(col)
            rec[col] = cells.get(ds, "") if ds else ""
        name = rec[name_col]
        if not name or name.lower() in ("player", "name") or not pid:
            continue  # spacer / totals rows
        hand = "R"
        if name.endswith("*"):
            hand, name = "L", name[:-1]
        elif name.endswith("#"):
            hand, name = "S", name[:-1]
        elif name.endswith("?"):
            hand, name = "", name[:-1]
        rec[name_col] = clean(name)
        rec[hand_col] = hand
        rec["Year"] = str(year)
        rec["PlayerID"] = pid
        rec["RowType"] = "stint" if "partial_table" in cls else "season"
        if "Qualified" in spec:
            rec["Qualified"] = "0" if "non_qual" in cls else "1"
        if "SO/W" in spec and not rec["SO/W"]:
            try:
                so, bb = float(rec["SO"] or 0), float(rec["BB"] or 0)
                rec["SO/W"] = f"{so / bb:.2f}" if bb else ""
            except ValueError:
                pass
        rows.append(rec)
    return rows, missing


def to_csv(rows: list[dict], cols: list[str]) -> str:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=cols, lineterminator="\n", quoting=csv.QUOTE_MINIMAL)
    w.writeheader()
    w.writerows(rows)
    return buf.getvalue()


def validate(fname: str, rows: list[dict]) -> list[str]:
    errs = []
    seasons = [r for r in rows if r["RowType"] == "season"]
    if len(seasons) < 300:
        errs.append(f"{fname}: only {len(seasons)} season rows (expected 700+ mid-season)")
    ids = [r["PlayerID"] for r in seasons]
    if len(ids) != len(set(ids)):
        errs.append(f"{fname}: duplicate PlayerIDs among season rows")
    return errs


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--year", type=int, default=dt.date.today().year)
    ap.add_argument("--out", type=Path, default=Path("site"))
    ap.add_argument("--force", action="store_true", help="run even in the offseason")
    a = ap.parse_args()

    if not a.force and dt.date.today().month in (12, 1, 2):
        print("offseason — nothing to do")
        return 0

    a.out.mkdir(parents=True, exist_ok=True)
    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"})

    results, problems = {}, []
    for i, (fname, page, tid, spec, hand) in enumerate(JOBS):
        if i:
            time.sleep(PACE_S)
        url = BASE.format(y=a.year, page=page)
        print(f"GET {url}")
        text = fetch(url, s)
        if text is None:
            print(f"{a.year} not published yet (404) — exiting quietly")
            return 0
        rows, soft_missing = parse(text, a.year, tid, spec, hand)
        if rows is None:
            print(f"#{tid} missing — season not started? exiting quietly")
            return 0
        if soft_missing:
            print(f"  note: not on page, left blank: {soft_missing}")
        problems += validate(fname, rows)
        results[fname] = (rows, list(spec))

    if problems:
        print("VALIDATION FAILED — existing files left untouched:\n  " + "\n  ".join(problems),
              file=sys.stderr)
        return 2

    for fname, (rows, cols) in results.items():
        (a.out / fname).write_text(to_csv(rows, cols), encoding="utf-8", newline="")
        n_st = sum(r["RowType"] == "stint" for r in rows)
        print(f"wrote {fname}: {len(rows)} rows ({len(rows) - n_st} season / {n_st} stint), "
              f"{len(cols)} cols")
    return 0


if __name__ == "__main__":
    sys.exit(main())
