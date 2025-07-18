#!/usr/bin/env python3
"""
social_html_scraper.py – LinkedIn activity page → Excel

* Works both:
  • in the browser (PyScript / Pyodide) via `linkedin_html_to_excel()`
  • on the command‑line   →  `python social_html_scraper.py page.html`

The scraping logic is identical to the original CLI script; only the
interactive “wizard” was removed and a clean function API was added.
"""

from __future__ import annotations

import io, re, shlex, sys, argparse, importlib
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import pandas as pd
from bs4 import BeautifulSoup

# ─── optional progress bar ─────────────────────────────────────────────────
tqdm = importlib.import_module("tqdm") if importlib.util.find_spec("tqdm") else None

# ─── styling (CLI only) ───────────────────────────────────────────────────
CYAN, GREEN, YELLOW, RED, RESET = ("\033[96m", "\033[92m", "\033[93m", "\033[91m", "\033[0m")
BOLD = "\033[1m"; WAVE, FOLDER, SPARKLE, GO = "👋", "📂", "✨", "🚀"

# ─── regex helpers ────────────────────────────────────────────────────────
NBSP       = "\u00A0\u202F"
NUM_RE     = rf"(\d[\d.,{NBSP}]*)"
REACTIONS  = ("like|celebrate|support|love|insightful|curious|funny|"
              "applaud|praise|interest")
CLUSTER_RE = re.compile(rf"(?:{REACTIONS}\s+)+{NUM_RE}", re.I)
ID19_RE    = re.compile(r"(\d{19})")
HEADER_REPLY = re.compile(r"replied to\s+(.+?)’?(?:s)?\s+comment", re.I)
CTRL_MENU    = re.compile(r"post by (.+)", re.I)
LOADED_RE    = re.compile(r"Loaded\s+\d+\s+(Comments|Posts)\s+posts", re.I)

# ─── tiny helpers ─────────────────────────────────────────────────────────
def to_int(s: str) -> int:
    """Convert strings like '1 234', '2.5K', '3M' → int."""
    s = (s.replace("\u202f", "").replace("\u00a0", "").replace(",", "").upper())
    if s.endswith("K"): return int(float(s[:-1]) * 1_000)
    if s.endswith("M"): return int(float(s[:-1]) * 1_000_000)
    if s.endswith("B"): return int(float(s[:-1]) * 1_000_000_000)
    return int(re.sub(r"[^\d]", "", s) or 0)

def clean_path(raw: str) -> str:
    """Tolerate paths dragged into the terminal (with spaces etc.)."""
    raw = raw.rstrip()
    raw = raw[:-1] if raw.endswith("\\") else raw
    try:
        parts = shlex.split(raw)
        return parts[0] if parts else raw
    except ValueError:
        return raw.replace("\\ ", " ").strip("\"'")

id_from_url = lambda u: (m := ID19_RE.search(u)) and int(m[1])
id_to_dt    = lambda aid: datetime.utcfromtimestamp((aid >> 22) / 1000).replace(tzinfo=timezone.utc)
dt_parts    = lambda dt: (dt.strftime("%Y-%m-%d"), dt.strftime("%H:%M:%S"), dt.strftime("%A"))
auto_fn     = lambda m: f"linkedin_{m}_{datetime.now():%Y%m%d_%H%M%S}.xlsx"

# ─── likes extractor ──────────────────────────────────────────────────────
def extract_likes(tag) -> int:
    for e in tag.select('[aria-label*="reaction" i]'):
        t = e.get("aria-label") or e.get_text(" ", strip=True)
        if (m := re.search(NUM_RE, t)):
            return to_int(m[1])

    s = tag.select_one(".social-details-social-counts__reactions-count,"
                       ".social-details-social-counts__count")
    if s:
        txt = s.get_text(" ", strip=True)
        if (m := re.search(r"and\s+" + NUM_RE + r"\s+others", txt, re.I)):
            return to_int(m[1])
        if (m := re.search(NUM_RE, txt)):
            return to_int(m[1])

    cluster = tag.find(string=re.compile(rf"(?:{REACTIONS}\s+)+{NUM_RE}", re.I))
    if cluster and (m := re.search(NUM_RE, cluster)):
        return to_int(m[1])

    txt = tag.get_text(" ", strip=True)
    if (m := re.search(rf"{NUM_RE}(?=\s+(?:comments?|others?|reposts?|shares?))", txt, re.I)):
        return to_int(m[1])

    return 0

def int_before(txt: str, kw: str) -> int:
    m = re.search(rf"{NUM_RE}\s*(?:[•·]?\s*)?(?:{kw})", txt, re.I)
    return to_int(m[1]) if m else 0

# ─── URL helpers ──────────────────────────────────────────────────────────
urn2url = lambda u: f"https://www.linkedin.com/feed/update/{u.split('?')[0]}/" if u and u.startswith("urn:li:") else u or ""
def post_url(tag):
    if (u := tag.get("data-urn") or tag.get("data-entity-urn")):
        return urn2url(u)
    for a in tag.select("a[href]"):
        h = a["href"]
        full = ("https://www.linkedin.com" + h) if h.startswith("/") else h
        if "/feed/update/" in h or "/posts/" in h:
            return full.split("?")[0]
    return ""

# ─── Post format detection ───────────────────────────────────────────────
IMG_SELECT = (".feed-shared-image__image, .feed-shared-external-image__image, "
              ".ivm-view-attr__img--centered, .document-page__image")

def content_images(tag):
    imgs = []
    for img in tag.select(IMG_SELECT):
        # skip avatar thumbnails inside actor header
        if img.find_parent(class_=re.compile(r"update-components-actor")):
            continue
        imgs.append(img)
    return imgs

def detect_format(tag, caption):
    txt_all    = tag.get_text(" ", strip=True).lower()
    header     = tag.select_one(".update-components-header__text-view")
    header_txt = header.get_text(" ", strip=True).lower() if header else ""

    # 1. Poll
    if "author can see how you vote" in txt_all or "poll closed" in txt_all:
        return "Poll"

    # 2. Straight repost
    if "reposted this" in header_txt:
        return "Repost"

    # 3. Repost with copy
    if len(tag.select(".update-components-actor__meta")) > 1:
        return "Repost with copy"

    # 4. LinkedIn Article
    art = tag.select_one(".feed-shared-article")
    if art:
        a   = art.select_one("a[href]")
        href = a["href"] if a else ""
        dom = urlparse(href).netloc.lower()
        if "linkedin.com" in dom and ("/pulse/" in href or "/articles/" in href):
            return "LinkedIn Article"
    for a in tag.select("a[href]"):
        href = a["href"]
        dom  = urlparse(href).netloc.lower()
        if "linkedin.com" in dom and ("/pulse/" in href or "/articles/" in href):
            return "LinkedIn Article"

    # 5. Carousel / Document
    if ("document has finished loading" in txt_all
        or "download document" in txt_all
        or tag.select_one(".feed-shared-document, .feed-shared-document-view, .feed-shared-carousel")):
        return "Carousel"

    # 6. External Link
    ext = [href for a in tag.select("a[href]")
           if (href := a["href"]).startswith("http") and "linkedin.com" not in urlparse(href).netloc.lower()]
    if ext and not tag.select("video, .feed-shared-document, .feed-shared-carousel"):
        return "Link"

    # 7. Video
    if tag.select_one("video"):
        return "Video"

    # 8. Images
    imgs = content_images(tag)
    if imgs:
        return "Image(s)" if len(imgs) > 1 else "Image"

    # 9. Text
    return "Text"

# ─── Post parsing / scraping ─────────────────────────────────────────────
def parse_post(tag, today):
    caption = " ".join(x.get_text(" ", strip=True) for x in tag.select("span.break-words")).strip()
    url = post_url(tag)
    if (aid := id_from_url(url)):
        date, time, day = dt_parts(id_to_dt(aid))
    else:
        date, time, day = today.strftime("%Y-%m-%d"), "", today.strftime("%A")
    raw = " ".join(tag.stripped_strings)
    return dict(
        caption  = caption,
        date     = date,
        time     = time,
        day      = day,
        likes    = extract_likes(tag),
        comments = int_before(raw, "comments?"),
        shares   = int_before(raw, "reposts?|shares?"),
        format   = detect_format(tag, caption),
        url      = url
    )

def scrape_posts_from_soup(soup: BeautifulSoup):
    today = datetime.today()
    for post in soup.select(".feed-shared-update-v2"):
        yield parse_post(post, today)

# ─── Comment parsing ─────────────────────────────────────────────────────
def parse_comment(upd):
    header    = upd.select_one(".update-components-header__text-view")
    commenter = header.select_one("a").get_text(strip=True) if header else ""

    url = post_url(upd)
    date = time = day = ""
    if (aid := id_from_url(url)):
        date, time, day = dt_parts(id_to_dt(aid))

    ents = [e for e in upd.select(".comments-comment-entity")
            if (a := e.select_one(".comments-comment-meta__description-title")) and
               a.get_text(strip=True).lower() == commenter.lower()]
    ent = (ents[-1] if HEADER_REPLY.search(header.get_text(" ", strip=True) if header else "")
           else ents[0] if ents else None)

    comment = ent.select_one(".comments-comment-item__main-content").get_text(" ", strip=True) if ent else ""
    likes   = extract_likes(ent) if ent else 0

    if header and (m := HEADER_REPLY.search(header.get_text(" ", strip=True))):
        pname  = m[1].strip()
        ctype  = f"Reply to {pname}"
        target = ""
        for e in upd.select(".comments-comment-entity"):
            actor = e.select_one(".comments-comment-meta__description-title")
            if actor and actor.get_text(strip=True).lower() == pname.lower():
                target = e.select_one(".comments-comment-item__main-content").get_text(" ", strip=True)
                break
    else:
        ctrl = upd.select_one(".feed-shared-control-menu__trigger")
        author = None
        if ctrl and (aria := ctrl.get("aria-label", "")) and (m2 := CTRL_MENU.search(aria)):
            author = m2[1].strip()
        if author and author.lower() == commenter.lower():
            ctype = "Direct comment on their own post"
        elif author:
            ctype = f"Direct comment on {author}'s post"
        else:
            ctype = "Direct comment on post"

        pc = upd.select_one(".feed-shared-update-v2__description, "
                            ".feed-shared-text-view, .update-components-text")
        target = pc.get_text(" ", strip=True) if pc else ""

    return dict(
        comment        = comment,
        date           = date,
        time           = time,
        day            = day,
        likes          = likes,
        type           = ctype,
        in_response_to = target,
        url            = url
    )

def scrape_comments_from_soup(soup: BeautifulSoup):
    upds = [u for u in soup.select(".feed-shared-update-v2")
            if (h := u.select_one(".update-components-header__text-view")) and
               ("commented on" in h.text or "replied to" in h.text)]
    for upd in upds:
        yield parse_comment(upd)

# ─── Detect page type ────────────────────────────────────────────────────
def detect_mode(html_text: str) -> str:
    """Return 'comments' or 'posts' by inspecting the HTML."""
    m = LOADED_RE.search(html_text)
    return "comments" if m and m[1].lower().startswith("comment") else "posts"

# ─── Public browser API ──────────────────────────────────────────────────
def linkedin_html_to_excel(html_text: str, mode_hint: str | None = None) -> bytes:
    """
    Convert one LinkedIn activity HTML page to an Excel workbook (bytes).
    Ready for download in a browser or write‑out on disk.
    """
    mode = (mode_hint or detect_mode(html_text)).lower()
    soup = BeautifulSoup(html_text, "html.parser")

    if mode == "comments":
        rows = list(scrape_comments_from_soup(soup))
    else:
        rows = list(scrape_posts_from_soup(soup))

    if not rows:
        raise ValueError("No data found – did you save the whole activity page?")

    df = pd.DataFrame(rows)
    if mode == "comments":
        df = df.drop_duplicates(subset=["comment", "date"])

    mem = io.BytesIO()
    df.to_excel(mem, index=False)
    return mem.getvalue()

# ─── Optional command‑line entry point ───────────────────────────────────
def _cli():
    ap = argparse.ArgumentParser(description="LinkedIn HTML → Excel")
    ap.add_argument("input_path", help="Path to one .html file")
    ap.add_argument("-m", "--mode", choices=["posts", "comments"])
    ap.add_argument("-o", "--output", help="Output .xlsx filename")
    args = ap.parse_args()

    html = Path(args.input_path).read_text(encoding="utf-8", errors="ignore")
    data = linkedin_html_to_excel(html, args.mode)
    out  = args.output or auto_fn(args.mode or detect_mode(html))
    Path(out).write_bytes(data)
    print(f"{SPARKLE}  {GREEN}Saved → {out}{RESET}")

if __name__ == "__main__":
    if len(sys.argv) > 1:
        _cli()
