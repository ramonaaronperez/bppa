#!/usr/bin/env python3
"""explorer/bppa_lite.html -> docs/index.html, the public (read-only) copy for GitHub Pages.
Drops the Fixes tab (it needs Ramon's local save server) and adds a link back to the site."""
from pathlib import Path

src = Path("explorer/bppa_lite.html").read_text(encoding="utf-8")
out = src.replace('  <button data-tab="fixes">Fixes</button>\n', "")
assert out != src, "Fixes tab button not found — bppa_lite.html layout changed"
out = out.replace("<title>BPPA Explorer</title>",
                  "<title>BPPA Explorer · baseball production</title>")
out = out.replace('<nav class="tabs" id="tabs">',
                  '<nav class="tabs" id="tabs">\n  <a href="https://baseballproduction.wordpress.com/" '
                  'style="align-self:center;margin-right:12px;text-decoration:none">← baseball production</a>', 1)
Path("docs/index.html").write_text(out, encoding="utf-8")
print("docs/index.html written,", len(out), "bytes")
