#!/usr/bin/env python3
"""
Push the generated leaderboard pages to baseballproduction.wordpress.com via the
WordPress.com REST API (works on the free plan — it only writes normal page content).

Credentials come from environment variables (GitHub Actions secrets):
    WPCOM_CLIENT_ID, WPCOM_CLIENT_SECRET   from https://developer.wordpress.com/apps/
    WPCOM_USER                             WordPress.com username
    WPCOM_APP_PASSWORD                     an Application Password (wordpress.com/me/security)
A fresh token is requested every run (password grant), so nothing expires.

Usage
    python build/publish_wp.py                 # upsert the 7 BPPA pages (nightly)
    python build/publish_wp.py --dry-run       # show what would change
    python build/publish_wp.py --front-page    # one-time: make "BPPA" the site's home page
    python build/publish_wp.py --retire-old    # one-time: move every OTHER page to Draft
    python build/publish_wp.py --restore-old   # undo --retire-old (republish them)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import requests

SITE = os.environ.get("WPCOM_SITE", "baseballproduction.wordpress.com")
API = f"https://public-api.wordpress.com/rest/v1.1/sites/{SITE}"
TOKEN_URL = "https://public-api.wordpress.com/oauth2/token"

# key (generated file) -> (slug, title). 'about' reuses the existing page ID 1 slug.
PAGES = {
    "home":         ("bppa", "BPPA — Bases per Plate Appearance"),
    "batting":      ("batting-leaders", "Batting Leaders"),
    "pitching":     ("pitching-leaders", "Pitching Leaders"),
    "team":         ("team-leaders", "Team Leaders"),
    "year-by-year": ("year-by-year", "Year by Year"),
    "about":        ("about", "About BPPA"),
}
# never draft these (site verification / utility pages)
KEEP = {"google78c0e5a0545190b1", "thank-you"}
RETIRED_LOG = Path("wp/retired_pages.json")


def token() -> str:
    need = ["WPCOM_CLIENT_ID", "WPCOM_CLIENT_SECRET", "WPCOM_USER", "WPCOM_APP_PASSWORD"]
    miss = [k for k in need if not os.environ.get(k)]
    if miss:
        sys.exit(f"missing secrets: {', '.join(miss)}")
    r = requests.post(TOKEN_URL, data={
        "client_id": os.environ["WPCOM_CLIENT_ID"],
        "client_secret": os.environ["WPCOM_CLIENT_SECRET"],
        "grant_type": "password",
        "username": os.environ["WPCOM_USER"],
        "password": os.environ["WPCOM_APP_PASSWORD"],
    }, timeout=30)
    if r.status_code != 200:
        sys.exit(f"token request failed: HTTP {r.status_code} {r.text[:300]}")
    return r.json()["access_token"]


class WP:
    def __init__(self, tok: str | None, dry: bool):
        self.s = requests.Session()
        if tok:
            self.s.headers["Authorization"] = f"Bearer {tok}"
        self.dry = dry

    def get(self, path, **params):
        r = self.s.get(API + path, params=params, timeout=60)
        return r

    def post(self, path, data):
        if self.dry:
            print(f"  [dry-run] POST {path} {list(data)}")
            return {"ID": 0}
        r = self.s.post(API + path, json=data, timeout=120)
        if r.status_code >= 300:
            raise RuntimeError(f"POST {path}: HTTP {r.status_code} {r.text[:400]}")
        return r.json()

    def all_pages(self, status="publish"):
        out, page = [], 1
        while True:
            r = self.get("/posts", type="page", status=status, number=100, page=page,
                         fields="ID,slug,title,status")
            r.raise_for_status()
            d = r.json()
            out += d["posts"]
            if len(out) >= d["found"] or not d["posts"]:
                return out
            page += 1

    def by_slug(self, slug):
        r = self.get(f"/posts/slug:{slug}", type="page", fields="ID,slug,status,content")
        return r.json() if r.status_code == 200 else None


def upsert(wp: WP, src: Path):
    ids = {}
    for key, (slug, title) in PAGES.items():
        body = (src / f"{key}.html").read_text(encoding="utf-8")
        cur = wp.by_slug(slug)
        if cur and cur.get("content", "").strip() == body.strip() and cur.get("status") == "publish":
            print(f"= {slug} unchanged (ID {cur['ID']})")
            ids[key] = cur["ID"]
            continue
        data = {"title": title, "content": body, "status": "publish", "type": "page"}
        if cur:
            res = wp.post(f"/posts/{cur['ID']}", data)
            print(f"~ updated {slug} (ID {cur['ID']})")
        else:
            res = wp.post("/posts/new", {**data, "slug": slug})
            print(f"+ created {slug} (ID {res.get('ID')})")
        ids[key] = res.get("ID") or (cur or {}).get("ID")
    return ids


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", type=Path, default=Path("wp"))
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--front-page", action="store_true")
    ap.add_argument("--retire-old", action="store_true")
    ap.add_argument("--restore-old", action="store_true")
    a = ap.parse_args()

    wp = WP(token(), a.dry_run)

    if a.restore_old:
        ids = json.loads(RETIRED_LOG.read_text())
        for pid in ids:
            wp.post(f"/posts/{pid}", {"status": "publish"})
        print(f"republished {len(ids)} pages")
        return

    ids = upsert(wp, a.src)

    if a.front_page:
        wp.post("/settings", {"show_on_front": "page", "page_on_front": ids["home"]})
        print(f"front page set to ID {ids['home']}")

    if a.retire_old:
        ours = {slug for slug, _ in PAGES.values()} | KEEP
        old = [p for p in wp.all_pages() if p["slug"] not in ours]
        print(f"moving {len(old)} old pages to Draft (reversible with --restore-old)")
        done = []
        for p in old:
            wp.post(f"/posts/{p['ID']}", {"status": "draft"})
            done.append(p["ID"])
        if not a.dry_run:
            RETIRED_LOG.write_text(json.dumps(done))
        print(f"drafted {len(done)} pages; IDs saved to {RETIRED_LOG}")


if __name__ == "__main__":
    main()
