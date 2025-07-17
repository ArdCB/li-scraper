"""
social_html_scraper.py – LinkedIn activity page → Excel (browser or CLI)
"""

from __future__ import annotations
import io, re, sys, argparse, shlex, importlib
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import pandas as pd
from bs4 import BeautifulSoup

# tqdm stays optional
tqdm = importlib.import_module("tqdm") if importlib.util.find_spec("tqdm") else None

# ── regex & helpers (same as your original) ───────────────────────────────
NBSP = "\u00A0\u202F"
NUM_RE = r"(\d[\d.,{NBSP}]*)".replace("{NBSP}", NBSP)
REACTIONS = ("like|celebrate|support|love|insightful|curious|funny|"
             "applaud|praise|interest")
CLUSTER_RE = re.compile(rf"(?:{REACTIONS}\s+)+{NUM_RE}", re.I)
ID19_RE = re.compile(r"(\d{19})")
HEADER_REPLY = re.compile(r"replied to\s+(.+?)’?(?:s)?\s+comment", re.I)
CTRL_MENU = re.compile(r"post by (.+)", re.I)
LOADED_RE = re.compile(r"Loaded\s+\d+\s+(Comments|Posts)\s+posts", re.I)

def to_int(s: str) -> int:
    s = (s.replace("\u202f", "").replace("\u00a0", "").replace(",", "").upper())
    if s.endswith("K"): return int(float(s[:-1]) * 1_000)
    if s.endswith("M"): return int(float(s[:-1]) * 1_000_000)
    if s.endswith("B"): return int(float(s[:-1]) * 1_000_000_000)
    return int(re.sub(r"[^\d]", "", s) or 0)

id_from_url = lambda u: (m := ID19_RE.search(u)) and int(m[1])
id_to_dt    = lambda aid: datetime.utcfromtimestamp((aid >> 22) / 1000).replace(tzinfo=timezone.utc)
dt_parts    = lambda dt: (dt.strftime("%Y-%m-%d"), dt.strftime("%H:%M:%S"), dt.strftime("%A"))
auto_fn     = lambda m: f"linkedin_{m}_{datetime.now():%Y%m%d_%H%M%S}.xlsx"

# (likes extractor, post/ comment parsing etc. are unchanged – edited only for brevity)
# …………………………………………… (full unchanged sections omitted for clarity) …………………………………………

# ---------------- Public API ----------------
def detect_mode(html_text: str) -> str:
    """Return 'comments' or 'posts' by inspecting the HTML."""
    m = LOADED_RE.search(html_text)
    return "comments" if m and m[1].lower().startswith("comment") else "posts"

def linkedin_html_to_excel(html_text: str, mode_hint: str | None = None) -> bytes:
    """
    Convert one LinkedIn activity HTML page to an Excel file in memory.
    Returns the XLSX bytes ready for download or saving.
    """
    mode = mode_hint or detect_mode(html_text)
    soup = BeautifulSoup(html_text, "html.parser")
    rows = list(scrape_comments(soup) if mode == "comments" else scrape_posts(soup))
    if not rows:
        raise ValueError("No data found – did you save the full page?")
    df = pd.DataFrame(rows)
    if mode == "comments":
        df = df.drop_duplicates(subset=["comment", "date"])
    mem = io.BytesIO()
    df.to_excel(mem, index=False)
    return mem.getvalue()

# --------------- Optional CLI wrapper ---------------
def _cli():
    ap = argparse.ArgumentParser(description="LinkedIn HTML → Excel")
    ap.add_argument("input_path", help="Path to one .html file")
    ap.add_argument("-m","--mode", choices=["posts","comments"])
    ap.add_argument("-o","--output", help="Output .xlsx filename")
    args = ap.parse_args()

    html = Path(args.input_path).read_text(encoding="utf-8", errors="ignore")
    data = linkedin_html_to_excel(html, args.mode)
    out  = args.output or auto_fn(args.mode or detect_mode(html))
    Path(out).write_bytes(data)
    print("Saved →", out)

if __name__ == "__main__":
    if len(sys.argv) > 1:
        _cli()
