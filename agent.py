"""
Hindi Auto-Blogger Agent
========================
Automatically writes SEO-optimised Hindi articles and publishes
them to WordPress or Blogger using Gemini AI + Google Search.

Platforms : WordPress (recommended) | Blogger
Schedule  : Runs via APScheduler or GitHub Actions cron
Images    : Pollinations.ai (free, no API key needed)
"""

import json
import logging
import os
import re
import signal
import sys
import time
import urllib.parse
from datetime import datetime
from pathlib import Path

import requests
import google.generativeai as genai
from apscheduler.schedulers.background import BackgroundScheduler
from dotenv import load_dotenv
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials

# ── Load .env ─────────────────────────────────────────────────────────────────
load_dotenv()

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  [%(levelname)-7s]  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.FileHandler("agent.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("agent")

# ── Configuration ─────────────────────────────────────────────────────────────
PLATFORM        = os.getenv("PLATFORM", "wordpress").lower()  # "wordpress" or "blogger"
GEMINI_API_KEY  = os.getenv("GEMINI_API_KEY", "")
INTERVAL_HOURS  = float(os.getenv("INTERVAL_HOURS", "6"))
AUTO_PUBLISH    = os.getenv("AUTO_PUBLISH", "true").lower() == "true"
DRAFTS_DIR      = Path(os.getenv("DRAFTS_DIR", "drafts"))

# WordPress
WP_URL          = os.getenv("WP_URL", "").rstrip("/")
WP_USERNAME     = os.getenv("WP_USERNAME", "")
WP_APP_PASSWORD = os.getenv("WP_APP_PASSWORD", "")

# Blogger
BLOG_ID              = os.getenv("BLOG_ID", "")
GOOGLE_CLIENT_ID     = os.getenv("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "")
GOOGLE_REFRESH_TOKEN = os.getenv("GOOGLE_REFRESH_TOKEN", "")

DRAFTS_DIR.mkdir(exist_ok=True)

# ── Categories & Sources ──────────────────────────────────────────────────────
CATEGORIES = ["science", "technology", "automobile"]

SOURCES = {
    "science": [
        "Nature", "Science Magazine", "NASA", "ISRO", "New Scientist", "Cell"
    ],
    "technology": [
        "MIT Technology Review", "IEEE Spectrum", "Wired", "TechCrunch", "Ars Technica"
    ],
    "automobile": [
        "Autocar India", "MotorTrend", "Car and Driver",
        "Tata Motors Blog", "Mahindra", "Hyundai India"
    ],
}

# ── State ─────────────────────────────────────────────────────────────────────
cat_index = 0
stats = {"generated": 0, "published": 0, "errors": 0, "skipped": 0}

genai.configure(api_key=GEMINI_API_KEY)

# ── Prompts ───────────────────────────────────────────────────────────────────
SYSTEM_PROMPT = """You are an expert Hindi content writer and SEO specialist writing for Indian readers.
Write engaging, factual, deeply informative articles in Hindi (Devanagari script).
Professional tone. Return ONLY raw JSON — no markdown, no backticks, no extra text."""

ARTICLE_PROMPT = """Write a comprehensive SEO-optimised Hindi blog article about the latest trending
development in {category}. Use reputed sources: {sources}.

Return ONLY this JSON (no markdown, no code fences):
{{
  "title": "Hindi title with primary SEO keyword",
  "english_title": "4-6 lowercase English words with hyphens for URL slug",
  "meta_description": "Hindi meta description under 150 chars",
  "focus_keyword": "Primary Hindi SEO keyword",
  "keywords": ["kw1","kw2","kw3","kw4","kw5","kw6","kw7"],
  "article": "Full Hindi article. Use ## for H2 headings, ### for H3. Minimum 900 words. Include facts, data, expert opinions, India angle, strong conclusion.",
  "image_prompt": "Photorealistic 16:9 scene description in English. Max 100 words.",
  "sources": ["Source Name: Article headline"],
  "tags": ["tag1","tag2","tag3","tag4","tag5"],
  "category": "{category}",
  "read_time": "X मिनट",
  "trend_score": 90,
  "excerpt": "2-sentence Hindi teaser for social sharing"
}}"""


# ── Article Generation ────────────────────────────────────────────────────────
def generate_article(category: str) -> dict:
    sources_str = ", ".join(SOURCES.get(category, SOURCES["technology"]))
    prompt = ARTICLE_PROMPT.format(category=category, sources=sources_str)

    log.info("  Calling Gemini API...")
    model = genai.GenerativeModel(
        model_name="gemini-flash-latest",
        system_instruction=SYSTEM_PROMPT,
    )
    response = model.generate_content(prompt)
    raw = response.text.strip()
    clean = re.sub(r"```(?:json)?\s*|\s*```", "", raw).strip()

    try:
        return json.loads(clean)
    except json.JSONDecodeError:
        match = re.search(r"\{[\s\S]*\}", clean)
        if match:
            return json.loads(match.group())
        raise ValueError(f"JSON parse failed. Raw:\n{clean[:300]}")


# ── Image URL (Pollinations.ai — free) ────────────────────────────────────────
def get_image_url(prompt: str) -> str:
    safe = urllib.parse.quote(prompt[:200])
    seed = int(time.time()) % 99999
    return (
        f"https://image.pollinations.ai/prompt/{safe}"
        f"?width=1024&height=576&model=flux&nologo=true&seed={seed}"
    )


# ── Slug helper ───────────────────────────────────────────────────────────────
def make_slug(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower().strip())
    return slug.strip("-")[:60]


# ── HTML Formatter ────────────────────────────────────────────────────────────
def format_html(article: dict, image_url: str) -> str:
    body = article.get("article", "")
    body = re.sub(
        r"^## (.+)$",
        r'<h2 style="font-size:1.35em;margin:1.5em 0 .5em;color:#1a1a2e">\1</h2>',
        body, flags=re.M,
    )
    body = re.sub(
        r"^### (.+)$",
        r'<h3 style="font-size:1.15em;margin:1.2em 0 .4em">\1</h3>',
        body, flags=re.M,
    )
    body = re.sub(r"^[-*] (.+)$", r"<li>\1</li>", body, flags=re.M)
    body = re.sub(r"\n{2,}", "</p><p style='margin:.9em 0;line-height:1.9'>", body)

    title     = article.get("title", "")
    meta_desc = article.get("meta_description", "")[:150]
    excerpt   = article.get("excerpt", "")

    excerpt_html = (
        '<p style="font-style:italic;color:#555;border-left:3px solid #3b5bdb;'
        f'padding-left:12px;margin-top:24px">{excerpt}</p>'
    ) if excerpt else ""

    # JSON-LD schema — Google reads this directly (more reliable than meta tags)
    schema = json.dumps({
        "@context": "https://schema.org",
        "@type": "Article",
        "headline": title,
        "description": meta_desc,
        "inLanguage": "hi",
        "image": image_url,
        "author": {"@type": "Organization", "name": "Hindi Auto Blogger"},
    }, ensure_ascii=False)

    return (
        f'<script type="application/ld+json">\n{schema}\n</script>\n'
        f'<article style="font-family:Arial,sans-serif;line-height:1.9;'
        f'color:#222;max-width:800px;margin:0 auto">\n'
        f'<img src="{image_url}" alt="{title}" '
        f'style="width:100%;border-radius:10px;margin-bottom:22px;display:block" '
        f'loading="lazy">\n'
        f'<p style="font-size:1.05em;line-height:1.9">{body}</p>\n'
        f'{excerpt_html}\n'
        f'</article>'
    )


# ── WordPress Publisher ───────────────────────────────────────────────────────
def get_or_create_wp_tags(names: list, auth: tuple) -> list:
    ids = []
    for name in names:
        try:
            r = requests.post(
                f"{WP_URL}/wp-json/wp/v2/tags",
                auth=auth, json={"name": name}, timeout=15,
            )
            if r.ok:
                ids.append(r.json().get("id"))
            else:
                search = requests.get(
                    f"{WP_URL}/wp-json/wp/v2/tags",
                    auth=auth, params={"search": name}, timeout=15,
                )
                if search.ok and search.json():
                    ids.append(search.json()[0]["id"])
        except Exception as e:
            log.warning(f"  Tag '{name}': {e}")
    return [i for i in ids if i]


def publish_to_wordpress(article: dict, html: str) -> str:
    auth      = (WP_USERNAME, WP_APP_PASSWORD)
    title     = article.get("title", "")
    eng_title = article.get("english_title", "").strip()
    slug      = make_slug(eng_title) if eng_title else ""
    raw_desc  = article.get("meta_description", "")
    meta_desc = (
        raw_desc[:147].rsplit(" ", 1)[0].rstrip(".,;:") + "..."
        if len(raw_desc) > 150 else raw_desc
    )
    focus_kw = article.get("focus_keyword", "")

    log.info("  Creating tags...")
    tag_ids = get_or_create_wp_tags(article.get("tags", []), auth)

    payload = {
        "title":   title,
        "content": html,
        "status":  "publish",
        "slug":    slug,
        "tags":    tag_ids,
        # Yoast SEO + RankMath fields — auto-fills Search Description & Focus Keyword
        "meta": {
            "_yoast_wpseo_metadesc":       meta_desc,
            "_yoast_wpseo_focuskw":        focus_kw,
            "_yoast_wpseo_title":          title,
            "rank_math_description":       meta_desc,
            "rank_math_focus_keyword":     focus_kw,
        },
    }

    r = requests.post(
        f"{WP_URL}/wp-json/wp/v2/posts",
        auth=auth, json=payload, timeout=30,
    )
    r.raise_for_status()
    result = r.json()
    post_url = result.get("link", "Published")

    log.info(f"  Slug      : {slug} ✓")
    log.info(f"  Tags      : {len(tag_ids)} set ✓")
    log.info(f"  Search Desc: '{meta_desc[:55]}' ✓")
    log.info(f"  Focus KW  : '{focus_kw}' ✓")
    return post_url


# ── Blogger Publisher ─────────────────────────────────────────────────────────
def get_blogger_token() -> str:
    creds = Credentials(
        token=None,
        refresh_token=GOOGLE_REFRESH_TOKEN,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=GOOGLE_CLIENT_ID,
        client_secret=GOOGLE_CLIENT_SECRET,
        scopes=["https://www.googleapis.com/auth/blogger"],
    )
    creds.refresh(Request())
    return creds.token


def publish_to_blogger(article: dict, html: str) -> str:
    token = get_blogger_token()
    hdr   = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    hindi_title = article.get("title", "")
    eng_title   = article.get("english_title", "").strip()
    slug        = make_slug(eng_title) if eng_title else ""
    raw_desc    = article.get("meta_description", "")
    meta_desc   = (
        raw_desc[:147].rsplit(" ", 1)[0].rstrip(".,;:") + "..."
        if len(raw_desc) > 150 else raw_desc
    )

    # Step 1: Create post with English slug title → proper URL
    create_title = slug if slug else eng_title if eng_title else hindi_title
    r = requests.post(
        f"https://www.googleapis.com/blogger/v3/blogs/{BLOG_ID}/posts/",
        headers=hdr,
        json={
            "kind": "blogger#post", "blog": {"id": BLOG_ID},
            "title": create_title, "content": html,
            "labels": article.get("tags", []),
        },
        timeout=30,
    )
    r.raise_for_status()
    post_id  = r.json().get("id", "")
    post_url = r.json().get("url", "Published")
    log.info(f"  Post ID : {post_id}")

    # Step 2: GET full post → update title + location → PUT back
    if post_id:
        try:
            get_r = requests.get(
                f"https://www.googleapis.com/blogger/v3/blogs/{BLOG_ID}/posts/{post_id}",
                headers=hdr, timeout=30,
            )
            if get_r.ok:
                post_data = get_r.json()
                post_data["title"]    = hindi_title
                post_data["location"] = {
                    "name": "India", "lat": 20.5937, "lng": 78.9629, "span": "30.0 50.0"
                }
                put_r = requests.put(
                    f"https://www.googleapis.com/blogger/v3/blogs/{BLOG_ID}/posts/{post_id}",
                    headers=hdr, json=post_data, timeout=30,
                )
                if put_r.ok:
                    post_url = put_r.json().get("url", post_url)
                    log.info(f"  Title    : '{hindi_title[:50]}' ✓")
                    log.info(f"  Location : India ✓")
                    log.info(f"  SEO      : JSON-LD schema in HTML ✓")
        except Exception as e:
            log.warning(f"  Update error: {e}")

    log.info(f"  Slug     : {slug}")
    return post_url


# ── Save draft locally ────────────────────────────────────────────────────────
def save_draft(article: dict, image_url: str):
    article["image_url"] = image_url
    ts   = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = DRAFTS_DIR / f"{ts}_{article.get('category', 'x')}.json"
    path.write_text(
        json.dumps(article, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    log.info(f"  Draft saved → {path}")


# ── Main Cycle ────────────────────────────────────────────────────────────────
def run_cycle() -> bool:
    global cat_index
    category = CATEGORIES[cat_index % len(CATEGORIES)]
    cat_index += 1

    sep = "=" * 55
    log.info(sep)
    log.info(
        f"CYCLE  |  {category.upper()}  |  "
        f"{PLATFORM.upper()}  |  "
        f"{datetime.now():%Y-%m-%d %H:%M:%S}"
    )
    log.info(sep)

    try:
        # Step 1 — Generate article
        log.info("[1/3] Generating Hindi article...")
        article = generate_article(category)
        log.info(f"  Title     : {article.get('title', '')[:55]}")
        log.info(f"  Eng slug  : {article.get('english_title', '')}")
        log.info(f"  Focus KW  : {article.get('focus_keyword', '')}")
        log.info(f"  Keywords  : {', '.join(article.get('keywords', []))}")
        log.info(f"  Sources   : {' | '.join(article.get('sources', []))}")
        log.info(f"  Tags      : {', '.join(article.get('tags', []))}")
        stats["generated"] += 1

        # Step 2 — Generate image
        log.info("[2/3] Generating image URL...")
        image_url = get_image_url(
            article.get("image_prompt", f"{category} india technology")
        )
        log.info(f"  Image     : {image_url[:70]}...")

        # Step 3 — Publish or save draft
        if AUTO_PUBLISH:
            log.info(f"[3/3] Publishing to {PLATFORM.upper()}...")
            html = format_html(article, image_url)

            if PLATFORM == "wordpress" and WP_URL and WP_USERNAME and WP_APP_PASSWORD:
                url = publish_to_wordpress(article, html)
            elif PLATFORM == "blogger" and BLOG_ID:
                url = publish_to_blogger(article, html)
            else:
                log.warning(f"  Credentials missing — saving draft")
                save_draft(article, image_url)
                stats["skipped"] += 1
                return True

            log.info(f"  Live URL  → {url}")
            stats["published"] += 1
        else:
            log.info("[3/3] AUTO_PUBLISH=false — saving draft")
            save_draft(article, image_url)
            stats["skipped"] += 1

    except Exception as e:
        log.error(f"Cycle failed: {e}", exc_info=True)
        stats["errors"] += 1
        return False

    log.info(f"Stats: {stats}")
    return True


# ── Entry Point ───────────────────────────────────────────────────────────────
def main():
    if not GEMINI_API_KEY:
        log.error("GEMINI_API_KEY missing — set it in .env or GitHub Secrets")
        sys.exit(1)

    log.info(f"Hindi Auto-Blogger Agent starting")
    log.info(f"  Platform      : {PLATFORM.upper()}")
    log.info(f"  Interval      : every {INTERVAL_HOURS}h")
    log.info(f"  Auto-publish  : {AUTO_PUBLISH}")
    log.info(f"  Categories    : {', '.join(CATEGORIES)}")

    # Run first cycle immediately
    run_cycle()

    # Schedule recurring cycles
    scheduler = BackgroundScheduler(timezone="Asia/Kolkata")
    scheduler.add_job(
        run_cycle,
        trigger="interval",
        hours=INTERVAL_HOURS,
        id="content_cycle",
        max_instances=1,
        coalesce=True,
    )
    scheduler.start()
    log.info(f"Scheduler running — next cycle in {INTERVAL_HOURS}h")

    def _shutdown(sig, frame):
        log.info("Shutdown signal — stopping agent")
        scheduler.shutdown(wait=False)
        sys.exit(0)

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    while True:
        time.sleep(60)


if __name__ == "__main__":
    main()
