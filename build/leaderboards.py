#!/usr/bin/env python3
"""
Build the static BPPA leaderboard pages for the WordPress.com site.

Free WordPress.com strips <script>/<iframe>, so every page is plain HTML: headings,
paragraphs and Gutenberg-style table figures (the theme styles them, stripes included).

Inputs (all in --data, default ./site):
    bppa_data.parquet            batting + batting-against, 1900 -> last folded season
    bppa_current_bat.csv / _pit  live current-season overlay (optional)
Outputs (--out, default ./wp):
    home.html  batting.html  pitching.html  team.html  about.html  + pages.json

Conventions follow project track 30: PA rebuilt, blank = 0, incomplete rows excluded,
season rows for player aggregates, team_row attribution for team totals, career
BPPA+ = 100 * Σbases / Σ(lg_bppa·PA) (inverted for pitchers), NgL excluded from BPPA+.
"""
from __future__ import annotations

import argparse
import datetime as dt
import html
import json
from pathlib import Path

import duckdb

EXPLORER_URL = "https://baseballproduction.github.io/bppa/"  # overwritten by --explorer

MIN_SEASON_PA = 502
MIN_CAREER_PA = {"BAT": 5000, "PIT": 5000}


# ----------------------------------------------------------------------------- data
def load(con: duckdb.DuckDBPyConnection, data: Path) -> int | None:
    pq = (data / "bppa_data.parquet").as_posix()
    con.sql(f"CREATE VIEW base AS SELECT * FROM read_parquet('{pq}')")
    con.sql("CREATE MACRO nz(x) AS COALESCE(TRY_CAST(x AS DOUBLE), 0)")
    max_pq = con.sql("SELECT max(year) FROM base").fetchone()[0]

    parts = ["""SELECT side, year, player, player_id, team, lg, row_type, pa, bases, bppa,
                       incomplete, team_row, is_ngl, lg_bppa, bppa_plus, hr, sb FROM base"""]
    live_year = None
    for side, fn, name_col, team_col, lg_expr in (
        ("BAT", "bppa_current_bat.csv", "Player", "Team", '"Lg"'),
        ("PIT", "bppa_current_pit.csv", "Pitcher", "Tm", "NULL"),
    ):
        f = data / fn
        if not f.exists():
            continue
        con.sql(f"""CREATE OR REPLACE VIEW raw_{side} AS
            SELECT * FROM read_csv('{f.as_posix()}', all_varchar=true, header=true, quote='"')""")
        con.sql(f"""CREATE OR REPLACE VIEW cur_{side} AS
            WITH r AS (
              SELECT '{side}' AS side, CAST("Year" AS INT) AS year, replace("{name_col}", chr(160), ' ') AS player,
                     "PlayerID" AS player_id, "{team_col}" AS team, {lg_expr} AS lg,
                     "RowType" AS row_type,
                     CAST(nz("AB")+nz("BB")+nz("HBP")+nz("SH")+nz("SF") AS INT) AS pa,
                     CAST(nz("H")+nz("2B")+2*nz("3B")+3*nz("HR") + nz("BB")+nz("SB")-nz("CS")+nz("HBP") AS INT) AS bases,
                     ("AB" IS NULL OR "AB" = '') AS no_ab,
                     CAST(nz("HR") AS INT) AS hr, CAST(nz("SB") AS INT) AS sb
              FROM raw_{side})
            SELECT side, year, player, player_id, team, lg, row_type, pa, bases,
                   CASE WHEN no_ab OR pa = 0 THEN NULL ELSE bases / pa END AS bppa,
                   (no_ab OR pa = 0) AS incomplete,
                   (team NOT SIMILAR TO '[0-9]TM') AS team_row, FALSE AS is_ngl, hr, sb
            FROM r WHERE year > {max_pq}""")
        n = con.sql(f"SELECT count(*), max(year) FROM cur_{side}").fetchone()
        if n[0]:
            live_year = n[1]
            parts.append(f"""
              SELECT c.side, c.year, c.player, c.player_id, c.team, c.lg, c.row_type, c.pa, c.bases,
                     c.bppa, c.incomplete, c.team_row, c.is_ngl, l.lg_bppa,
                     CASE WHEN c.bppa IS NULL THEN NULL
                          WHEN c.side = 'BAT' THEN 100 * c.bppa / l.lg_bppa
                          ELSE 100 * l.lg_bppa / c.bppa END AS bppa_plus, c.hr, c.sb
              FROM cur_{side} c JOIN (
                  SELECT year, sum(bases)::DOUBLE / sum(pa) AS lg_bppa FROM cur_{side}
                  WHERE row_type = 'season' AND NOT incomplete GROUP BY year) l USING (year)""")
    con.sql("CREATE VIEW allrows AS " + " UNION ALL ".join(parts))
    con.sql("""CREATE VIEW season AS SELECT * FROM allrows
               WHERE row_type = 'season' AND NOT incomplete""")
    return live_year


# ----------------------------------------------------------------------------- html
def f3(x):  # .914 style, like OBP
    if x is None:
        return "—"
    s = f"{x:.3f}"
    return s[1:] if s.startswith("0.") else s


def f0(x):
    return "—" if x is None else f"{x:,.0f}"


def table(cols: list[tuple[str, str]], rows: list[dict], caption: str | None = None) -> str:
    """cols = [(key, header)]; numeric cols right-aligned by the theme's has-text-align class."""
    th = "".join(f"<th>{html.escape(h)}</th>" for _, h in cols)
    body = []
    for r in rows:
        tds = "".join(f"<td>{html.escape(str(r[k]))}</td>" for k, _ in cols)
        body.append(f"<tr>{tds}</tr>")
    cap = f'<figcaption class="wp-element-caption">{html.escape(caption)}</figcaption>' if caption else ""
    return (f'<figure class="wp-block-table is-style-stripes"><table class="has-fixed-layout">'
            f"<thead><tr>{th}</tr></thead><tbody>{''.join(body)}</tbody></table>{cap}</figure>")


def h2(t):
    return f'<h2 class="wp-block-heading">{html.escape(t)}</h2>'


def h3(t):
    return f'<h3 class="wp-block-heading">{html.escape(t)}</h3>'


def p(t):  # t may contain trusted inline markup
    return f"<p>{t}</p>"


def button(label, url):
    return (f'<div class="wp-block-buttons"><div class="wp-block-button">'
            f'<a class="wp-block-button__link wp-element-button" href="{html.escape(url)}">'
            f"{html.escape(label)}</a></div></div>")


def rows_of(con, sql):
    cur = con.execute(sql)
    names = [d[0] for d in cur.description]
    return [dict(zip(names, r)) for r in cur.fetchall()]


def ranked(rows):
    for i, r in enumerate(rows, 1):
        r["rk"] = i
    return rows


# ----------------------------------------------------------------------------- queries
def single_season(con, side, order, n=50, metric="bppa", min_pa=MIN_SEASON_PA, year=None):
    desc = "DESC" if order == "high" else "ASC"
    yr = f"AND year = {year}" if year else ""
    return ranked(rows_of(con, f"""
        SELECT player, year, team, pa, bases, hr, sb, bppa, bppa_plus FROM season
        WHERE side = '{side}' AND pa >= {min_pa} AND {metric} IS NOT NULL {yr}
        ORDER BY {metric} {desc}, pa DESC LIMIT {n}"""))


def career(con, side, n=50, by="bppa"):
    plus = ("100 * sum(bases) FILTER (WHERE bppa_plus IS NOT NULL) / "
            "sum(lg_bppa * pa) FILTER (WHERE bppa_plus IS NOT NULL)") if side == "BAT" else \
           ("100 * sum(lg_bppa * pa) FILTER (WHERE bppa_plus IS NOT NULL) / "
            "sum(bases) FILTER (WHERE bppa_plus IS NOT NULL)")
    order = "DESC" if (side == "BAT" or by == "bppa_plus") else "ASC"
    return ranked(rows_of(con, f"""
        SELECT any_value(player ORDER BY year DESC) AS player,
               min(year) || '–' || max(year) AS span, count(*) AS yrs,
               sum(pa) AS pa, sum(bases) AS bases, sum(hr) AS hr, sum(sb) AS sb,
               sum(bases)::DOUBLE / sum(pa) AS bppa, {plus} AS bppa_plus
        FROM season WHERE side = '{side}'
        GROUP BY player_id HAVING sum(pa) >= {MIN_CAREER_PA[side]}
           AND {by} IS NOT NULL
        ORDER BY {by} {order} LIMIT {n}"""))


def decade_leaders(con, side, n=5):
    order = "DESC" if side == "BAT" else "ASC"
    return rows_of(con, f"""
        WITH d AS (
          SELECT (year // 10) * 10 AS decade, player_id,
                 any_value(player ORDER BY year DESC) AS player,
                 sum(pa) AS pa, sum(bases)::DOUBLE / sum(pa) AS bppa
          FROM season WHERE side = '{side}' AND NOT is_ngl GROUP BY ALL HAVING sum(pa) >= 2500)
        SELECT decade, player, pa, bppa,
               row_number() OVER (PARTITION BY decade ORDER BY bppa {order}) AS rk
        FROM d QUALIFY rk <= {n} ORDER BY decade, rk""")


def year_by_year(con, side, n=10):
    """Top n per season; qualifier = 3.1 PA (hitters) / 4.2 BF (pitchers) per team game,
    team games approximated by the most G any player logged that year (NgL excluded)."""
    order = "DESC" if side == "BAT" else "ASC"
    k = 3.1 if side == "BAT" else 4.2
    return rows_of(con, f"""
        WITH q AS (SELECT year, max(TRY_CAST(g AS INT)) AS g FROM base
                   WHERE side = 'BAT' AND NOT is_ngl GROUP BY year
                   UNION ALL
                   SELECT year, max(g) FROM (SELECT year, max(pa) / 4.3 AS g FROM season
                   WHERE side = 'BAT' AND year NOT IN (SELECT year FROM base) GROUP BY year)
                   GROUP BY year)
        SELECT s.year, s.player, s.team, s.pa, s.bases, s.bppa, s.bppa_plus,
               row_number() OVER (PARTITION BY s.year ORDER BY s.bppa {order}) AS rk
        FROM season s JOIN q USING (year)
        WHERE s.side = '{side}' AND NOT s.is_ngl AND s.pa >= {k} * least(q.g, 162)
        QUALIFY rk <= {n} ORDER BY s.year DESC, rk""")


def team_seasons(con, side, n=25, year=None):
    order = "DESC" if side == "BAT" else "ASC"
    yr = f"AND year = {year}" if year else "AND NOT is_ngl"
    return ranked(rows_of(con, f"""
        SELECT team, year, count(DISTINCT player_id) AS players, sum(pa) AS pa, sum(bases) AS bases,
               sum(hr) AS hr, sum(bases)::DOUBLE / sum(pa) AS bppa,
               CASE WHEN '{side}' = 'BAT' THEN 100 * sum(bases) / sum(lg_bppa * pa)
                    ELSE 100 * sum(lg_bppa * pa) / sum(bases) END AS bppa_plus
        FROM allrows WHERE side = '{side}' AND team_row AND NOT incomplete {yr}
        GROUP BY team, year HAVING sum(pa) >= 4000
        ORDER BY bppa {order} LIMIT {n}"""))


def fmt(rows):
    out = []
    for r in rows:
        r = dict(r)
        for k in ("bppa",):
            if k in r:
                r[k] = f3(r[k])
        for k in ("pa", "bases", "hr", "sb", "bppa_plus", "players"):
            if k in r:
                r[k] = f0(r[k])
        out.append(r)
    return out


BAT_SEASON_COLS = [("rk", "#"), ("player", "Player"), ("year", "Year"), ("team", "Tm"),
                   ("pa", "PA"), ("bases", "Bases"), ("hr", "HR"), ("sb", "SB"),
                   ("bppa", "BPPA"), ("bppa_plus", "BPPA+")]
PIT_SEASON_COLS = [("rk", "#"), ("player", "Pitcher"), ("year", "Year"), ("team", "Tm"),
                   ("pa", "BF"), ("bases", "Bases allowed"), ("hr", "HR"),
                   ("bppa", "BPPA"), ("bppa_plus", "BPPA+")]
BAT_CAREER_COLS = [("rk", "#"), ("player", "Player"), ("span", "Years"), ("yrs", "Seasons"),
                   ("pa", "PA"), ("bases", "Bases"), ("hr", "HR"), ("sb", "SB"),
                   ("bppa", "BPPA"), ("bppa_plus", "BPPA+")]
PIT_CAREER_COLS = [("rk", "#"), ("player", "Pitcher"), ("span", "Years"), ("yrs", "Seasons"),
                   ("pa", "BF"), ("bases", "Bases allowed"), ("bppa", "BPPA"),
                   ("bppa_plus", "BPPA+")]
TEAM_COLS = [("rk", "#"), ("team", "Team"), ("year", "Year"), ("players", "Players"),
             ("pa", "PA"), ("bases", "Bases"), ("hr", "HR"), ("bppa", "BPPA"),
             ("bppa_plus", "BPPA+")]


def decade_block(con, side, label):
    rows = decade_leaders(con, side)
    out = []
    by = {}
    for r in rows:
        by.setdefault(r["decade"], []).append(r)
    for dec, rs in by.items():
        out.append(h3(f"{dec}s"))
        out.append(table([("rk", "#"), ("player", label), ("pa", "PA" if side == "BAT" else "BF"),
                          ("bppa", "BPPA")], fmt(rs)))
    return "\n".join(out)


# ----------------------------------------------------------------------------- pages
def build(con, live_year, explorer, stamp):
    last = con.sql("SELECT max(year) FROM allrows").fetchone()[0]
    first = con.sql("SELECT min(year) FROM allrows").fetchone()[0]
    through = f"{first}–{last}" + (f" ({live_year} updated daily)" if live_year else "")
    upd = p(f"<em>Data: Baseball-Reference, {through}. Last updated {stamp}.</em>")
    cta = button("Open the full BPPA Explorer →", explorer)
    pages = {}

    # ---- home
    cur = live_year or last
    min_live, min_live_p = (502, 502) if not live_year else _live_min_pa(con, live_year)
    home = [
        p("<strong>BPPA — Bases per Plate Appearance</strong> measures how many bases a hitter "
          "produces every time he steps to the plate: total bases, plus walks, hit-by-pitches and "
          "stolen bases, minus times caught stealing, divided by plate appearances. For pitchers "
          "the same formula counts the bases they <em>allow</em>, so lower is better. "
          '<a href="/about/">How it works →</a>'),
        cta,
        h2(f"{cur} leaders — hitters"),
        table(BAT_SEASON_COLS, fmt(single_season(con, "BAT", "high", 25, year=cur, min_pa=min_live)),
              f"Min {min_live} PA"),
        h2(f"{cur} leaders — pitchers (lowest BPPA allowed)"),
        table(PIT_SEASON_COLS, fmt(single_season(con, "PIT", "low", 25, year=cur, min_pa=min_live_p)),
              f"Min {min_live_p} batters faced (about 1 IP per team game)"),
        h2(f"{cur} teams"),
        table(TEAM_COLS, fmt(team_seasons(con, "BAT", 30, year=cur)), "Team offense, by BPPA"),
        upd,
    ]
    pages["home"] = "\n".join(home)

    # ---- batting
    pages["batting"] = "\n".join([
        p("The best hitters by BPPA — single seasons and careers — plus BPPA+, which compares a "
          "hitter to his league that year (100 = league average, 150 = 50% better). BPPA+ is the "
          "fair way to compare Babe Ruth to Barry Bonds."),
        cta,
        h2("Best single seasons (BPPA)"),
        table(BAT_SEASON_COLS, fmt(single_season(con, "BAT", "high", 50)), f"Min {MIN_SEASON_PA} PA"),
        h2("Best single seasons (BPPA+, era-adjusted)"),
        table(BAT_SEASON_COLS, fmt(single_season(con, "BAT", "high", 25, metric="bppa_plus")),
              f"Min {MIN_SEASON_PA} PA · Negro Leagues excluded from BPPA+"),
        h2("Best careers (BPPA)"),
        table(BAT_CAREER_COLS, fmt(career(con, "BAT", 50)), f"Min {MIN_CAREER_PA['BAT']:,} career PA"),
        h2("Best careers (BPPA+, era-adjusted)"),
        table(BAT_CAREER_COLS, fmt(career(con, "BAT", 25, by="bppa_plus")),
              f"Min {MIN_CAREER_PA['BAT']:,} career PA"),
        h2("Leaders by decade"),
        p("Top five per decade, minimum 2,500 PA within the decade."),
        decade_block(con, "BAT", "Player"),
        upd,
    ])

    # ---- pitching
    pages["pitching"] = "\n".join([
        p("For pitchers, BPPA is the bases a pitcher gives up per batter faced — "
          "<strong>lower is better</strong>. BPPA+ flips it so higher is better on both sides "
          "(100 = league average), the same convention as ERA+."),
        cta,
        h2("Best single seasons (lowest BPPA allowed)"),
        table(PIT_SEASON_COLS, fmt(single_season(con, "PIT", "low", 50)), f"Min {MIN_SEASON_PA} batters faced"),
        h2("Best single seasons (BPPA+, era-adjusted)"),
        table(PIT_SEASON_COLS, fmt(single_season(con, "PIT", "high", 25, metric="bppa_plus")),
              f"Min {MIN_SEASON_PA} batters faced"),
        h2("Best careers (lowest BPPA allowed)"),
        table(PIT_CAREER_COLS, fmt(career(con, "PIT", 50)), f"Min {MIN_CAREER_PA['PIT']:,} batters faced"),
        h2("Best careers (BPPA+, era-adjusted)"),
        table(PIT_CAREER_COLS, fmt(career(con, "PIT", 25, by="bppa_plus")),
              f"Min {MIN_CAREER_PA['PIT']:,} batters faced"),
        h2("Leaders by decade"),
        p("Top five per decade, minimum 2,500 batters faced within the decade."),
        decade_block(con, "PIT", "Pitcher"),
        upd,
    ])

    # ---- team
    pages["team"] = "\n".join([
        p("Team BPPA adds up every player's plate appearances for that club — traded players count "
          "only for the games they played there."),
        cta,
        h2("Best team offenses ever (BPPA)"),
        table(TEAM_COLS, fmt(team_seasons(con, "BAT", 25))),
        h2("Best team pitching staffs ever (lowest BPPA allowed)"),
        table([c if c[0] != "pa" else ("pa", "BF") for c in TEAM_COLS if c[0] != "hr"],
              fmt(team_seasons(con, "PIT", 25))),
        upd,
    ])

    # ---- year by year
    yb = [p("The top ten hitters and pitchers by BPPA in every season. To qualify: 3.1 plate "
            "appearances per team game for hitters, about one inning per team game for pitchers. "
            "Search this page with Ctrl+F / ⌘F to jump to a year."), cta]
    bat_y, pit_y = year_by_year(con, "BAT"), year_by_year(con, "PIT")
    by_b, by_p = {}, {}
    for r in bat_y:
        by_b.setdefault(r["year"], []).append(r)
    for r in pit_y:
        by_p.setdefault(r["year"], []).append(r)
    for y in sorted(set(by_b) | set(by_p), reverse=True):
        yb.append(h2(str(y)))
        if y in by_b:
            yb.append(table([("rk", "#"), ("player", "Hitter"), ("team", "Tm"), ("pa", "PA"),
                             ("bases", "Bases"), ("bppa", "BPPA"), ("bppa_plus", "BPPA+")],
                            fmt(by_b[y])))
        if y in by_p:
            yb.append(table([("rk", "#"), ("player", "Pitcher"), ("team", "Tm"), ("pa", "BF"),
                             ("bases", "Bases allowed"), ("bppa", "BPPA"), ("bppa_plus", "BPPA+")],
                            fmt(by_p[y])))
    yb.append(upd)
    pages["year-by-year"] = "\n".join(yb)

    # ---- about
    pages["about"] = "\n".join([
        p("I created BPPA to answer one question: <em>every time this player comes to the plate, how "
          "many bases does he produce?</em> Batting average ignores walks and power; slugging "
          "ignores walks and speed. BPPA counts all of it."),
        h2("The formula"),
        p("<strong>Bases</strong> = Total Bases + Walks + Hit-by-Pitch + Stolen Bases − Caught Stealing<br>"
          "<strong>Plate appearances</strong> = AB + BB + HBP + SH + SF<br>"
          "<strong>BPPA</strong> = Bases ÷ Plate appearances"),
        h2("Example: Barry Bonds, 2001"),
        p("411 total bases + 177 walks + 9 HBP + 13 steals − 3 caught stealing = <strong>607 bases</strong> "
          "in 664 plate appearances → <strong>BPPA .914</strong>, the best season on record."),
        h2("BPPA+"),
        p("BPPA+ compares a player to the league average that season: 100 is average, 150 is 50% "
          "better. Hitters: 100 × BPPA ÷ league BPPA. Pitchers: 100 × league BPPA ÷ BPPA, so "
          "higher is better for both. Career BPPA+ is weighted by plate appearances. "
          "Negro League seasons show raw BPPA but are left out of league baselines."),
        h2("PRO and P/PA"),
        p("The companion stat weights each base by how hard it is to earn: singles, walks and HBP "
          "0.25, doubles 0.50, triples 0.75, home runs 1.00, steals +0.25 and caught stealing "
          "−0.2875. PRO is the total; P/PA is PRO per plate appearance. It was this site's original "
          "\"PPA\" stat and is still in the explorer."),
        h2("Notes"),
        p("Caught stealing wasn't recorded before 1912 and sacrifice flies before 1954, so raw BPPA "
          "slightly favors early eras — use BPPA+ to compare across eras. Leaderboards require "
          f"{MIN_SEASON_PA} PA for a season. Data from Baseball-Reference."),
        cta,
        upd,
    ])
    return pages


def _live_min_pa(con, year):
    """3.1 PA per team game, B-R's qualifying rule, from the most games any team has played."""
    g = con.sql(f"""SELECT max(g) FROM (SELECT team, max(TRY_CAST("G" AS INT)) AS g
                    FROM raw_BAT WHERE "RowType" <> 'x' GROUP BY team)""").fetchone()[0] or 162
    g = min(g, 162)
    return int(3.1 * g), int(4.2 * g)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", type=Path, default=Path("site"))
    ap.add_argument("--out", type=Path, default=Path("wp"))
    ap.add_argument("--explorer", default=EXPLORER_URL)
    a = ap.parse_args()
    con = duckdb.connect()
    live = load(con, a.data)
    stamp = dt.datetime.now(dt.timezone.utc).astimezone(
        dt.timezone(dt.timedelta(hours=-4))).strftime("%B %-d, %Y")
    pages = build(con, live, a.explorer, stamp)
    a.out.mkdir(parents=True, exist_ok=True)
    for k, v in pages.items():
        (a.out / f"{k}.html").write_text(v, encoding="utf-8")
    (a.out / "pages.json").write_text(json.dumps({k: f"{k}.html" for k in pages}, indent=1))
    print("built:", ", ".join(f"{k} ({len(v)//1024} KB)" for k, v in pages.items()))


if __name__ == "__main__":
    main()
