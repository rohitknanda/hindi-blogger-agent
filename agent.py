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
AMAZON_TAG      = os.getenv("AMAZON_TAG", "")
IMAGE_PROVIDER  = os.getenv("IMAGE_PROVIDER", "pollinations").lower()

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
# Strictly 3 niches only — no General Knowledge, no news/politics
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
SYSTEM_PROMPT = """You are an expert Hindi science journalist and SEO specialist at Vigyan Ki Duniya.
Write in a warm, conversational, human voice — NOT like AI. Add personal observations, real-world analogies, and unique Indian perspective.
STRICT RULES:
1. Only cover: Science, Technology, Automobile — NO politics, NO LPG rules, NO general utility content
2. Only write about events from the LAST 30 DAYS — never older topics
3. Write like a real journalist: include surprising facts, emotional hooks, India-specific impact
4. Vary sentence length — mix short punchy sentences with longer explanations
5. Add rhetorical questions to engage readers
6. Never sound like a template or AI — be original and specific
7. Return ONLY raw JSON — no markdown, no backticks, no extra text"""

ARTICLE_PROMPT = """Today's date is {today}. Write a comprehensive SEO-optimised Hindi blog article
about a RECENT trending development in {category} from the LAST 30 DAYS ONLY.
Use reputed sources: {sources}.
STRICT RULE: The topic MUST be from {month_year} or at most one month before. No older news.
WRITING STYLE:
- Start with a relatable story, surprising fact, or thought-provoking question
- Use "आप" and "हम" to directly address Indian readers
- Include at least 2 India-specific implications (ISRO, Indian scientists, Indian consumers)
- Add one expert quote or research citation
- Vary tone: explain technical terms simply using everyday analogies
- End with a strong CTA question that invites comments
- Do NOT write about: politics, government schemes, LPG prices, daily news, or non-science topics

Return ONLY this JSON (no markdown, no code fences):
{{
  "title": "Hindi title under 65 chars — use power words like 'खुलासा', 'चौंकाने वाला', 'पहली बार', 'क्रांति'. Must include main SEO keyword and create curiosity.",
  "english_title": "4-6 lowercase English words with hyphens for URL slug",
  "meta_description": "Hindi meta description under 150 chars",
  "focus_keyword": "Primary Hindi SEO keyword",
  "keywords": ["kw1","kw2","kw3","kw4","kw5","kw6","kw7"],
  "highlights": ["Key fact 1 in Hindi (under 15 words)", "Key fact 2", "Key fact 3", "Key fact 4", "Key fact 5"],
  "faq": [{{"q": "Hindi question about the topic?", "a": "Detailed Hindi answer in 2-3 sentences."}}, {{"q": "Second common question?", "a": "Answer."}}, {{"q": "Third question?", "a": "Answer."}}, {{"q": "Fourth question?", "a": "Answer."}}],
  "article": "Full Hindi article. Use ## for H2 headings, ### for H3. MINIMUM 1200 words. Structure: 1) Hook paragraph with story/surprising fact, 2) Background/what is this, 3) The main discovery/news with data, 4) Expert opinions, 5) India angle and impact, 6) Future implications, 7) Strong conclusion with CTA. Add real statistics, research citations, and analogies. Sound like a human expert, not AI.",
  "image_prompt": "Photorealistic 16:9 scene description in English. Max 100 words.",
  "sources": ["Source Name: Article headline"],
  "tags": ["tag1","tag2","tag3","tag4","tag5"],
  "category": "{category}",
  "read_time": "X मिनट",
  "trend_score": 90,
  "excerpt": "2-sentence Hindi teaser for social sharing",
  "amazon_products": [
    {{"name": "Product name in Hindi (relevant to article topic)", "search": "English Amazon search query for this product", "reason": "1 line Hindi reason why reader should buy this"}},
    {{"name": "Second relevant product", "search": "English Amazon search query", "reason": "Hindi reason"}},
    {{"name": "Third relevant product", "search": "English Amazon search query", "reason": "Hindi reason"}}
  ]
}}"""


# ── RSS Feeds — Google News (reliable, no auth needed) ───────────────────────
RSS_FEEDS = {
    "science": [
        "https://news.google.com/rss/search?q=science+discovery+research+2026&hl=en-IN&gl=IN&ceid=IN:en",
        "https://news.google.com/rss/search?q=ISRO+NASA+space+discovery+2026&hl=en-IN&gl=IN&ceid=IN:en",
        "https://news.google.com/rss/search?q=Nature+journal+scientific+breakthrough+June+2026&hl=en-IN&gl=IN&ceid=IN:en",
        "https://news.google.com/rss/search?q=biology+physics+chemistry+discovery+India&hl=en-IN&gl=IN&ceid=IN:en",
    ],
    "technology": [
        "https://news.google.com/rss/search?q=AI+artificial+intelligence+technology+2026&hl=en-IN&gl=IN&ceid=IN:en",
        "https://news.google.com/rss/search?q=IEEE+quantum+computing+semiconductor+2026&hl=en-IN&gl=IN&ceid=IN:en",
        "https://news.google.com/rss/search?q=Springer+Nature+technology+research+paper+2026&hl=en-IN&gl=IN&ceid=IN:en",
        "https://news.google.com/rss/search?q=India+technology+innovation+startup+2026&hl=en-IN&gl=IN&ceid=IN:en",
    ],
    "automobile": [
        "https://news.google.com/rss/search?q=electric+vehicle+EV+India+launch+2026&hl=en-IN&gl=IN&ceid=IN:en",
        "https://news.google.com/rss/search?q=SAE+automotive+engineering+technology+2026&hl=en-IN&gl=IN&ceid=IN:en",
        "https://news.google.com/rss/search?q=Tata+Mahindra+Hyundai+electric+car+India&hl=en-IN&gl=IN&ceid=IN:en",
        "https://news.google.com/rss/search?q=hydrogen+engine+autonomous+driving+2026&hl=en-IN&gl=IN&ceid=IN:en",
    ],
}


# ── RSS Feed Fetcher ─────────────────────────────────────────────────────────
def fetch_rss_headlines(category: str, max_items: int = 8) -> list:
    """
    Fetch latest headlines + abstracts from top journals via RSS.
    Returns list of dicts with title, summary, source, link.
    Falls back to empty list on any error.
    """
    import xml.etree.ElementTree as ET
    from datetime import timezone

    feeds = RSS_FEEDS.get(category, RSS_FEEDS["science"])
    items = []

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
        "Accept-Encoding": "gzip, deflate",
        "Connection": "keep-alive",
        "Cache-Control": "no-cache",
    }

    for feed_url in feeds:
        if len(items) >= max_items:
            break
        try:
            resp = requests.get(feed_url, headers=headers, timeout=10)
            if not resp.ok:
                continue

            root = ET.fromstring(resp.content)
            ns = {"atom": "http://www.w3.org/2005/Atom"}

            # Handle both RSS and Atom formats
            entries = root.findall(".//item") or root.findall(".//atom:entry", ns)

            for entry in entries[:3]:
                # RSS format
                title   = entry.findtext("title") or entry.findtext("atom:title", namespaces=ns) or ""
                summary = (entry.findtext("description") or
                          entry.findtext("summary") or
                          entry.findtext("atom:summary", namespaces=ns) or "")
                link    = (entry.findtext("link") or
                          entry.findtext("atom:link", namespaces=ns) or "")
                if hasattr(link, "attrib"):
                    link = link.get("href", "")

                title   = re.sub(r"<[^>]+>", "", title).strip()
                summary = re.sub(r"<[^>]+>", "", summary).strip()[:300]

                if title:
                    items.append({
                        "title":   title,
                        "summary": summary,
                        "source":  feed_url.split("/")[2],
                        "link":    link,
                    })
                if len(items) >= max_items:
                    break
        except Exception as e:
            log.debug(f"  RSS {feed_url}: {e}")
            continue

    log.info(f"  RSS: fetched {len(items)} headlines for '{category}'")
    return items


# ── Article Generation ────────────────────────────────────────────────────────
def generate_article(category: str, recent_titles: list = None,
                     rss_headlines: list = None) -> dict:
    sources_str = ", ".join(SOURCES.get(category, SOURCES["technology"]))
    today      = datetime.now().strftime("%B %d, %Y")
    month_year = datetime.now().strftime("%B %Y")

    # Build RSS context block — real fresh articles from top journals
    rss_block = ""
    if rss_headlines:
        hl_text = ""
        for h in rss_headlines[:6]:
            hl_text += f"- [{h['source']}] {h['title']}"
            if h.get("summary"):
                hl_text += f": {h['summary'][:150]}"
            hl_text += "\n"
        rss_block = (
            f"\n\nRECENT REAL ARTICLES from top journals (use these as your source):\n"
            f"{hl_text}"
            f"Write your article based on ONE of these real recent stories."
        )

    # Tell Gemini which topics to AVOID (already published)
    avoid_block = ""
    if recent_titles:
        titles_list = "\n".join(f"- {t}" for t in recent_titles[:20])
        avoid_block = (
            f"\n\nSTRICTLY AVOID these already-published topics:\n"
            f"{titles_list}\n"
            f"Pick a COMPLETELY DIFFERENT topic not in this list."
        )

    prompt = ARTICLE_PROMPT.format(
        category=category,
        sources=sources_str,
        today=today,
        month_year=month_year,
    ) + rss_block + avoid_block

    log.info("  Calling Gemini API...")
    # Try models in order — fallback if quota exceeded
    # Updated model list — June 2026
    # Only use models confirmed working with google-generativeai package
    models_to_try = [
        "gemini-flash-latest",            # primary — working ✓
        "gemini-2.0-flash",               # stable
        "gemini-2.0-flash-lite",          # lightweight
        "gemini-2.5-flash-preview-05-20", # latest preview
    ]
    response = None
    last_error = None
    for model_name in models_to_try:
        try:
            model = genai.GenerativeModel(
                model_name=model_name,
                system_instruction=SYSTEM_PROMPT,
            )
            response = model.generate_content(prompt)
            log.info(f"  Model used: {model_name} ✓")
            break
        except Exception as e:
            err_str = str(e).lower()
            if "quota" in err_str or "429" in err_str or "resource_exhausted" in err_str:
                log.warning(f"  {model_name} quota exceeded — trying next model...")
                last_error = e
                time.sleep(2)
                continue
            else:
                raise  # Non-quota error — re-raise immediately
    if response is None:
        raise ValueError(f"All models quota exceeded. Last error: {last_error}")
    raw = response.text.strip()
    clean = re.sub(r"```(?:json)?\s*|\s*```", "", raw).strip()

    try:
        article = json.loads(clean)
    except json.JSONDecodeError:
        match = re.search(r"\{[\s\S]*\}", clean)
        if match:
            article = json.loads(match.group())
        else:
            raise ValueError(f"JSON parse failed. Raw:\n{clean[:300]}")

    # Quality gate — reject off-topic or thin content
    banned_topics = ["lpg", "gas cylinder", "iran", "conflict", "war", "scheme",
                     "yojana", "election", "political", "government scheme"]
    title_lower = article.get("title", "").lower()
    category    = article.get("category", "")

    for banned in banned_topics:
        if banned in title_lower:
            log.warning(f"  Off-topic detected ('{banned}') — skipping")
            raise ValueError(f"Off-topic content detected: {article.get('title', '')}")

    if category not in ["science", "technology", "automobile"]:
        log.warning(f"  Wrong category '{category}' — setting to science")
        article["category"] = "science"

    word_count = len(article.get("article", "").split())
    if word_count < 600:
        log.warning(f"  Thin content: only {word_count} words — retrying...")
        raise ValueError(f"Thin content: {word_count} words (minimum 600)")

    log.info(f"  Word count: {word_count} words ✓")
    return article


# ── Image URL (Pollinations.ai — free) ────────────────────────────────────────
def get_image_pollinations(prompt: str) -> str:
    """
    Get article image via Pollinations.ai (AI-generated).
    Falls back to Picsum (beautiful stock photo) if Pollinations
    is unavailable from GitHub Actions.
    """
    clean = prompt[:80].strip().rstrip(".,; ")
    safe  = urllib.parse.quote(clean)
    seed  = int(time.time()) % 99999

    poll_url = (
        f"https://image.pollinations.ai/prompt/{safe}"
        f"?width=1280&height=720&model=flux&nologo=true&seed={seed}"
    )

    try:
        log.info("  Fetching image from Pollinations...")
        resp = requests.get(poll_url, timeout=45)
        if resp.ok and "image" in resp.headers.get("content-type", ""):
            log.info(f"  Pollinations image: {len(resp.content)//1024} KB ✓")
            # Upload to catbox.moe for stable short URL
            try:
                up = requests.post(
                    "https://catbox.moe/user/api.php",
                    data={"reqtype": "fileupload"},
                    files={"fileToUpload": (f"img-{seed}.jpg", resp.content, "image/jpeg")},
                    timeout=30,
                )
                if up.ok and up.text.strip().startswith("http"):
                    log.info(f"  Image hosted ✓ → {up.text.strip()[:60]}")
                    return up.text.strip()
            except Exception:
                pass
            return poll_url
        log.warning(f"  Pollinations returned: {resp.status_code} — using fallback")
    except Exception as e:
        log.warning(f"  Pollinations unavailable: {e} — using fallback")

    # Reliable fallback: Lorem Picsum (always works, beautiful photos)
    fallback = f"https://picsum.photos/seed/{seed}/1280/720"
    log.info(f"  Fallback image: {fallback}")
    return fallback


def get_image_imagen(prompt: str, slug: str) -> str:
    """
    Generate image via Google Imagen 3 using the genai.Client (new SDK
    interface). Requires a billing-enabled GEMINI_API_KEY.
    Uploads result to a free image host (0x0.st) and returns its URL.
    Returns "" on any failure so caller can fall back to Pollinations.
    """
    try:
        client = genai_client.Client(api_key=GEMINI_API_KEY)
        result = client.models.generate_images(
            model="imagen-3.0-generate-002",
            prompt=prompt[:480],
            config={"number_of_images": 1, "aspect_ratio": "16:9"},
        )

        if not result or not getattr(result, "generated_images", None):
            log.warning("  Imagen: no image returned")
            return ""

        img_bytes = result.generated_images[0].image.image_bytes

        upload = requests.post(
            "https://0x0.st",
            files={"file": (f"{slug}.png", img_bytes, "image/png")},
            timeout=60,
        )
        if upload.ok:
            url = upload.text.strip()
            log.info(f"  Imagen: image generated ✓ → {url[:60]}...")
            return url
        else:
            log.warning(f"  Imagen: upload failed ({upload.status_code})")
            return ""
    except Exception as e:
        log.warning(f"  Imagen failed: {e} — falling back to Pollinations")
        return ""


def get_image_url(prompt: str, slug: str = "") -> str:
    """
    Get a hero image URL using the configured provider.
    IMAGE_PROVIDER=imagen  -> try Imagen 3, fall back to Pollinations on failure
    IMAGE_PROVIDER=pollinations (default) -> always use free Pollinations
    """
    if IMAGE_PROVIDER == "imagen":
        url = get_image_imagen(prompt, slug or "article")
        if url:
            return url
        log.info("  Using Pollinations fallback for image")
    return get_image_pollinations(prompt)


# ── Slug helper ───────────────────────────────────────────────────────────────
def make_slug(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower().strip())
    return slug.strip("-")[:60]



# ── Internal Linking ─────────────────────────────────────────────────────────
def fetch_existing_posts(max_posts=100):
    posts = []
    try:
        if PLATFORM == "blogger" and BLOG_ID:
            token = get_blogger_token()
            r = requests.get(
                f"https://www.googleapis.com/blogger/v3/blogs/{BLOG_ID}/posts",
                headers={"Authorization": f"Bearer {token}"},
                params={"maxResults": max_posts, "fields": "items(title,url)"},
                timeout=15,
            )
            if r.ok:
                for item in r.json().get("items", []):
                    posts.append({"title": item.get("title",""), "url": item.get("url","")})
        elif PLATFORM == "wordpress" and WP_URL:
            r = requests.get(
                f"{WP_URL}/wp-json/wp/v2/posts",
                auth=(WP_USERNAME, WP_APP_PASSWORD),
                params={"per_page": max_posts, "_fields": "title,link"},
                timeout=15,
            )
            if r.ok:
                for item in r.json():
                    posts.append({"title": item.get("title",{}).get("rendered",""), "url": item.get("link","")})
    except Exception as e:
        log.warning(f"  Internal linking fetch failed: {e}")
    log.info(f"  Fetched {len(posts)} posts for internal linking")
    return posts


def get_recent_topics(days: int = 7) -> list:
    """Fetch titles of posts published in last N days to avoid duplicates."""
    topics = []
    try:
        if PLATFORM == "blogger" and BLOG_ID:
            token = get_blogger_token()
            from datetime import timedelta
            start_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%dT00:00:00+05:30")
            r = requests.get(
                f"https://www.googleapis.com/blogger/v3/blogs/{BLOG_ID}/posts",
                headers={"Authorization": f"Bearer {token}"},
                params={
                    "maxResults": 100,
                    "fields": "items(title,published)",
                    "startDate": start_date,
                },
                timeout=15,
            )
            if r.ok:
                for item in r.json().get("items", []):
                    topics.append(item.get("title", "").lower())
        elif PLATFORM == "wordpress" and WP_URL:
            from datetime import timedelta
            after = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%dT00:00:00")
            r = requests.get(
                f"{WP_URL}/wp-json/wp/v2/posts",
                auth=(WP_USERNAME, WP_APP_PASSWORD),
                params={"per_page": 100, "after": after, "_fields": "title"},
                timeout=15,
            )
            if r.ok:
                for item in r.json():
                    topics.append(item.get("title", {}).get("rendered", "").lower())
    except Exception as e:
        log.warning(f"  Recent topics fetch failed: {e}")
    log.info(f"  Fetched {len(topics)} recent topics for duplicate check")
    return topics


def is_duplicate_topic(article: dict, recent_topics: list) -> bool:
    """Check if article topic is too similar to a recently published post."""
    if not recent_topics:
        return False

    new_title = article.get("title", "").lower()
    new_keywords = [k.lower() for k in article.get("keywords", [])]

    import re as _re
    stop = {"में", "की", "का", "के", "है", "और", "को", "से", "पर", "यह",
            "वह", "एक", "भी", "कि", "the", "a", "an", "of", "in", "to",
            "भारत", "india", "hindi", "2026", "2025", "नया", "नई", "के", "लिए"}

    def extract_words(text):
        # Split on spaces and common punctuation
        parts = text.replace(",", " ").replace("।", " ").replace("?", " ")
        parts = parts.replace("!", " ").replace(":", " ").replace(";", " ")
        words = parts.split()
        return {w.strip() for w in words if len(w.strip()) >= 3 and w.lower() not in stop}

    new_words = extract_words(new_title)
    # Add key words from keywords list
    for kw in new_keywords[:3]:
        new_words.update(extract_words(kw))

    for recent in recent_topics:
        recent_words = extract_words(recent)
        if not recent_words:
            continue
        # Calculate overlap score
        overlap = len(new_words & recent_words)
        min_size = min(len(new_words), len(recent_words))
        if min_size == 0:
            continue
        similarity = overlap / min_size
        if similarity >= 0.5:  # 50% word overlap = duplicate
            log.warning(f"  Duplicate detected! ({similarity:.0%} similar)")
            log.warning(f"    New   : {article.get('title', '')[:55]}")
            log.warning(f"    Recent: {recent[:55]}")
            return True

    return False


def add_internal_links(html, article, existing_posts):
    if not existing_posts:
        return html

    # Protect the <img ...> tag (and its alt/title attrs) from being
    # modified — internal links must only land inside <p>/body text.
    img_match = re.search(r"<img\b[^>]*>", html, flags=re.IGNORECASE)
    if img_match:
        placeholder = "@@IMG_TAG_PLACEHOLDER@@"
        img_tag = img_match.group(0)
        html = html.replace(img_tag, placeholder, 1)
    else:
        placeholder = None
        img_tag = None
    current_title = article.get("title","").lower()
    keywords = [k.strip().lower() for k in article.get("keywords",[])]
    links_added = 0
    max_links = 4
    linked_urls = set()

    for post in existing_posts:
        if links_added >= max_links:
            break
        post_title = post.get("title","").strip()
        post_url   = post.get("url","").strip()
        if not post_title or not post_url:
            continue
        if post_title.lower() == current_title:
            continue
        if post_url in linked_urls:
            continue

        # Split post title into individual words
        separators = re.compile(r"[\s,।?!:;]+")
        post_words = [w for w in separators.split(post_title) if len(w) >= 3]

        match_word = None

        # Strategy 1: post title word appears in article keywords
        for pw in post_words:
            pw_l = pw.lower()
            for kw in keywords:
                if pw_l in kw or kw in pw_l:
                    if pw_l in html.lower():
                        match_word = pw
                        break
            if match_word:
                break

        # Strategy 2: article keyword word appears in post title
        if not match_word:
            for kw in keywords:
                kw_parts = [w for w in separators.split(kw) if len(w) >= 4]
                for part in kw_parts:
                    if part.lower() in post_title.lower() and part.lower() in html.lower():
                        match_word = part
                        break
                if match_word:
                    break

        if not match_word:
            continue

        # Find in HTML body
        lower_html = html.lower()
        idx = lower_html.find(match_word.lower())
        if idx == -1:
            continue

        # Skip if inside anchor
        before = html[:idx]
        if before.count("<a ") > before.count("</a>"):
            continue

        original = html[idx: idx + len(match_word)]
        link_tag = (
            f'<a href="{post_url}"' +
            f' style="color:#3b5bdb;text-decoration:underline"' +
            f' title="{post_title}">{original}</a>'
        )
        html = html[:idx] + link_tag + html[idx + len(match_word):]
        linked_urls.add(post_url)
        links_added += 1
        log.info(f"  Linked: {match_word!r} → {post_url[:55]}")

    log.info(f"  Total internal links added: {links_added}")

    # Restore the protected <img> tag
    if placeholder and img_tag:
        html = html.replace(placeholder, img_tag, 1)

    return html

# ── Amazon Affiliate ─────────────────────────────────────────────────────────
# Fallback products by category — used when Gemini doesn't return amazon_products
CATEGORY_PRODUCTS = {
    "science": [
        {"name": "टेलीस्कोप (Beginner Telescope)", "search": "telescope for beginners india", "reason": "घर से तारे और ग्रह देखें — अंतरिक्ष विज्ञान का असली अनुभव"},
        {"name": "विज्ञान की किताबें (Hindi)", "search": "vigyan science books hindi", "reason": "हिंदी में विज्ञान की गहरी समझ पाएं"},
        {"name": "Science Experiment Kit", "search": "science experiment kit for students india", "reason": "घर पर science experiments करें"},
    ],
    "technology": [
        {"name": "Raspberry Pi (Mini Computer)", "search": "raspberry pi 4 india", "reason": "AI और coding सीखें — tech की दुनिया में कदम रखें"},
        {"name": "Arduino Starter Kit", "search": "arduino starter kit india", "reason": "Electronics और robotics सीखें घर पर"},
        {"name": "Tech Books (Programming)", "search": "python programming books hindi india", "reason": "Technology को professionally सीखें"},
    ],
    "automobile": [
        {"name": "Car Dash Camera (Dashcam)", "search": "car dash camera india 4k", "reason": "सफर को सुरक्षित बनाएं — हर moment record करें"},
        {"name": "EV Charging Cable", "search": "electric vehicle charging cable india", "reason": "Electric car चार्जिंग के लिए best cable"},
        {"name": "Car Accessories Kit", "search": "car accessories kit india 2024", "reason": "अपनी गाड़ी को और बेहतर बनाएं"},
    ],
}


def build_amazon_box(products: list, amazon_tag: str = None) -> str:
    """Build an Amazon affiliate product recommendation box."""
    if amazon_tag is None:
        amazon_tag = os.getenv("AMAZON_TAG", "")
    if not products or not amazon_tag:
        return ""

    items_html = ""
    for p in products[:3]:
        name   = _clean(p.get("name", ""))
        search = p.get("search", "").strip()
        reason = _clean(p.get("reason", ""))
        if not name or not search:
            continue
        # Amazon search URL with affiliate tag
        search_url = (
            f"https://www.amazon.in/s?k={requests.utils.quote(search)}"
            f"&tag={amazon_tag}&linkCode=ur2"
        )
        items_html += (
            '<div style="display:flex;align-items:flex-start;gap:12px;'
            'padding:10px 0;border-bottom:1px solid #e8edf5">'
            '<div style="font-size:22px;flex-shrink:0">🛒</div>'
            '<div style="flex:1">'
            f'<div style="font-size:14px;font-weight:600;color:#1a1a2e;margin-bottom:3px">{name}</div>'
            f'<div style="font-size:12px;color:#555;margin-bottom:6px">{reason}</div>'
            f'<a href="{search_url}" target="_blank" rel="nofollow sponsored" '
            f'style="display:inline-block;background:#FF9900;color:#fff;'
            f'font-size:12px;font-weight:600;padding:5px 14px;border-radius:4px;'
            f'text-decoration:none">Amazon पर देखें →</a>'
            '</div></div>'
        )

    if not items_html:
        return ""

    return (
        '<div style="background:#fff8f0;border:1.5px solid #FF9900;'
        'border-radius:10px;padding:16px 20px;margin-top:28px;font-family:Arial,sans-serif">'
        '<div style="font-size:15px;font-weight:700;color:#cc7700;margin-bottom:12px">'
        '🛍️ इस विषय से जुड़े उत्पाद खरीदें (Amazon India)</div>'
        + items_html +
        '<div style="font-size:10px;color:#999;margin-top:10px">'
        '* Affiliate links — आपको कोई extra charge नहीं, हमें थोड़ा commission मिलता है</div>'
        '</div>'
    )


# ── HTML Formatter ────────────────────────────────────────────────────────────
def _clean(text: str) -> str:
    """Sanitize text — removes HTML tags and chars that break JSON/HTML attrs."""
    if not text:
        return ""
    text = re.sub(r"<[^>]+>", "", str(text))  # strip any HTML tags
    return (text
            .replace("\\", "")
            .replace('"', "'")
            .replace("\n", " ")
            .replace("\r", "")
            .replace("\t", " ")
            .strip())


def format_html(article: dict, image_url: str) -> str:
    body = article.get("article", "")
    # Strip author signatures Gemini sometimes adds
    import re as _re
    body = _re.sub(r"[-—–]+\s*रोहित कुमार[^\n]*\n?", "", body)
    body = _re.sub(r"[-—–]+\s*Rohit Kumar[^\n]*\n?", "", body, flags=_re.IGNORECASE)
    body = _re.sub(r"[-—–]+\s*विज्ञान की दुनिया[^\n]*\n?", "", body)
    # Convert markdown bold/italic to HTML
    body = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", body)
    body = re.sub(r"\*(.+?)\*", r"<em>\1</em>", body)
    body = re.sub(r"__(.+?)__", r"<strong>\1</strong>", body)
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
    excerpt    = article.get("excerpt", "")
    highlights = article.get("highlights", [])
    faq_items  = article.get("faq", [])
    category   = article.get("category", "science")
    amazon_products = article.get("amazon_products", [])

    # Highlight box — shown after first paragraph
    if highlights:
        hl_items = ""
        for h in highlights[:5]:
            h_clean = str(h).replace('"', "'").replace("<", "").replace(">", "")
            hl_items += (
                '<li style="padding:6px 0;border-bottom:1px solid #e8f0fe;'
                'font-size:14px;color:#222;list-style:none">'
                '<span style="color:#3b5bdb;margin-right:8px;font-weight:700">&#9658;</span>'
                + h_clean +
                '</li>'
            )
        highlight_box = (
            '<div style="background:#f0f4ff;border:1.5px solid #3b5bdb;'
            'border-radius:10px;padding:16px 20px;margin:20px 0;font-family:Arial,sans-serif">'
            '<div style="font-size:15px;font-weight:700;color:#3b5bdb;margin-bottom:10px">'
            '&#128161; मुख्य बातें (Key Highlights)</div>'
            '<ul style="list-style:none;margin:0;padding:0">'
            + hl_items +
            '</ul></div>'
        )
    else:
        highlight_box = ""

    excerpt_html = (
        '<p style="font-style:italic;color:#555;border-left:3px solid #3b5bdb;'
        f'padding-left:12px;margin-top:24px">{excerpt}</p>'
    ) if excerpt else ""

    # FAQ section HTML
    if faq_items:
        faq_rows = ""
        for item in faq_items[:5]:
            q = item.get("q", "")
            a = item.get("a", "")
            if q and a:
                faq_rows += (
                    '<div style="border-bottom:1px solid #e0e0e0;padding:14px 0">'
                    f'<div style="font-weight:700;font-size:15px;color:#1a1a2e;margin-bottom:6px">'
                    f'&#10067; {q}</div>'
                    f'<div style="font-size:14px;color:#444;line-height:1.8">{a}</div>'
                    '</div>'
                )
        faq_html = (
            '<div style="background:#fff;border:1.5px solid #3b5bdb;border-radius:10px;'
            'padding:20px 22px;margin-top:28px;font-family:Arial,sans-serif">'
            '<div style="font-size:17px;font-weight:700;color:#3b5bdb;margin-bottom:4px">'
            '&#10067; अक्सर पूछे जाने वाले सवाल (FAQ)</div>'
            f'{faq_rows}'
            '</div>'
        )
        # FAQ JSON-LD schema for Google rich results
        faq_schema = ''  # Now included in @graph above
    else:
        faq_html = ""
        faq_schema = ""

    # Amazon affiliate product box
    # Use Gemini products if available, else use category fallback
    if not amazon_products:
        amazon_products = CATEGORY_PRODUCTS.get(category, CATEGORY_PRODUCTS["science"])
        log.info(f"  Amazon: using category fallback for '{category}'")
    amazon_box = build_amazon_box(amazon_products)  # reads AMAZON_TAG from env
    if amazon_box:
        log.info(f"  Amazon box: {len(amazon_products)} products added ✓")

    # Initialize @graph with Article node
    graph_nodes = [
        {
            "@type": "Article",
            "headline": _clean(title)[:110],
            "description": _clean(meta_desc)[:150],
            "inLanguage": "hi",
            "image": {"@type": "ImageObject", "url": image_url, "width": 1280, "height": 720},
            "author": {"@type": "Person", "name": "Vigyan Ki Duniya",
                       "url": "https://www.vigyankiduniya.com/p/about-us.html"},
            "publisher": {"@type": "Organization", "name": "Vigyan Ki Duniya",
                          "url": "https://www.vigyankiduniya.com"},
            "datePublished": datetime.now().strftime("%Y-%m-%d"),
            "dateModified": datetime.now().strftime("%Y-%m-%d"),
        }
    ]

    # Add FAQ node to @graph if available
    if faq_items:
        graph_nodes.append({
            "@type": "FAQPage",
            "mainEntity": [
                {
                    "@type": "Question",
                    "name": _clean(item.get("q", ""))[:200],
                    "acceptedAnswer": {
                        "@type": "Answer",
                        "text": _clean(item.get("a", ""))[:500],
                    },
                }
                for item in faq_items[:5]
                if item.get("q") and item.get("a")
            ],
        })

    # Build single combined JSON-LD schema
    schema = json.dumps(
        {"@context": "https://schema.org", "@graph": graph_nodes},
        ensure_ascii=True,
        separators=(",", ":"),
    )

    bio_html = ""

    # Split body at first paragraph for <!--more--> tag
    if '</p>' in body:
        split_at = body.find('</p>') + 4
        intro = body[:split_at]
        rest  = body[split_at:]
    else:
        intro = body[:400]
        rest  = body[400:]

    return (
        f'<script type="application/ld+json">\n{schema}\n</script>\n'
        f'<article style="font-family:Arial,sans-serif;line-height:1.9;'
        f'color:#222;max-width:800px;margin:0 auto">\n'
        f'<img src="{image_url}" alt="{_clean(title)}" '
        f'style="width:100%;border-radius:10px;margin-bottom:22px;display:block" '
        f'loading="lazy">\n'
        f'<p style="font-size:1.05em;line-height:1.9">{intro}</p>\n'
        '<!--more-->\n'
        f'{highlight_box}\n'
        f'<p style="font-size:1.05em;line-height:1.9">{rest}</p>\n'
        f'{excerpt_html}\n'
        f'{faq_html}\n'
        f'{amazon_box}\n'
        f'</article>'
    )
    # Prepend FAQ schema if available


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

    # ── STEP 1: Create with English slug title → proper URL ───────────────────
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
    log.info(f"  Post ID  : {post_id}")
    log.info(f"  URL      : {post_url}")

    if not post_id:
        return post_url

    base_url = f"https://www.googleapis.com/blogger/v3/blogs/{BLOG_ID}/posts/{post_id}"

    # ── STEP 2: PATCH title + location (separate from search desc) ────────────
    try:
        p1 = requests.patch(
            base_url, headers=hdr,
            json={
                "title": hindi_title,
                "location": {"name": "India", "lat": 20.5937, "lng": 78.9629, "span": "30.0 50.0"},
            },
            timeout=30,
        )
        if p1.ok:
            post_url = p1.json().get("url", post_url)
            log.info(f"  Title    : '{hindi_title[:50]}' ✓")
            log.info(f"  Location : India ✓")
        else:
            log.warning(f"  Title/location PATCH: {p1.status_code}")
    except Exception as e:
        log.warning(f"  Title/location error: {e}")

    # ── STEP 3: Dedicated PATCH for Search Description only ───────────────────
    # Must be a separate call — combining with other fields causes Blogger to ignore it
    if meta_desc:
        try:
            # Try format 1: searchDescription key
            p2 = requests.patch(
                base_url, headers=hdr,
                json={"customMetaData": json.dumps({"searchDescription": meta_desc})},
                timeout=30,
            )
            if p2.ok:
                # Verify it was saved
                verify = requests.get(base_url, headers=hdr, timeout=15)
                if verify.ok:
                    saved = verify.json().get("customMetaData", "")
                    if meta_desc[:20] in saved:
                        log.info(f"  Search Desc: SET ✓ — '{meta_desc[:55]}'")
                    else:
                        # Try format 2: direct string value
                        p3 = requests.patch(
                            base_url, headers=hdr,
                            json={"customMetaData": meta_desc},
                            timeout=30,
                        )
                        log.info(f"  Search Desc: format2 attempt → {p3.status_code}")
            else:
                log.warning(f"  Search Desc PATCH: {p2.status_code} — {p2.text[:80]}")
        except Exception as e:
            log.warning(f"  Search Desc error: {e}")

    log.info(f"  Slug     : {slug}")
    log.info(f"  Meta desc: '{meta_desc[:55]}'")
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

    # Persistent category rotation across GitHub Actions runs
    # Priority: env var CATEGORY → GITHUB_RUN_NUMBER → time-based rotation
    forced_cat = os.getenv("CATEGORY", "").strip().lower()
    if forced_cat in CATEGORIES:
        category = forced_cat
        log.info(f"  Category forced by env: {category}")
    else:
        # Use GitHub run number if available (increments each run)
        run_num = int(os.getenv("GITHUB_RUN_NUMBER", "0"))
        if run_num > 0:
            category = CATEGORIES[run_num % len(CATEGORIES)]
            log.info(f"  Category from run #{run_num}: {category}")
        else:
            # Fallback: time-based — changes every 6 hours
            hour_block = (datetime.now().hour // 6) + (datetime.now().day * 4)
            category = CATEGORIES[hour_block % len(CATEGORIES)]
            log.info(f"  Category from time-block: {category}")

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
        # Fetch recent topics for duplicate detection
        log.info("  Fetching recent posts for duplicate check...")
        recent_topics = get_recent_topics(days=30)

        # Fetch fresh headlines from top journals
        log.info("  Fetching RSS headlines from top journals...")
        rss_headlines = fetch_rss_headlines(category)

        article = None
        for attempt in range(1, 5):  # Up to 4 attempts
            try:
                article = generate_article(
                    category,
                    recent_titles=recent_topics,
                    rss_headlines=rss_headlines,
                )
                # Check for duplicate topic
                if is_duplicate_topic(article, recent_topics):
                    log.warning(f"  Attempt {attempt}/4: Duplicate topic — retrying...")
                    if attempt == 4:
                        log.warning("  All attempts produced duplicates — skipping cycle")
                        stats["skipped"] += 1
                        return True
                    article = None
                    continue
                break
            except ValueError as ve:
                log.warning(f"  Attempt {attempt}/4 failed: {ve}")
                if attempt == 4:
                    raise
        if not article:
            raise ValueError("All 4 generation attempts failed")
        log.info(f"  Title     : {article.get('title', '')[:55]}")
        log.info(f"  Eng slug  : {article.get('english_title', '')}")
        log.info(f"  Focus KW  : {article.get('focus_keyword', '')}")
        log.info(f"  Keywords  : {', '.join(article.get('keywords', []))}")
        log.info(f"  Sources   : {' | '.join(article.get('sources', []))}")
        log.info(f"  Tags      : {', '.join(article.get('tags', []))}")
        stats["generated"] += 1

        # Step 2 — Generate image
        log.info("[2/3] Generating image URL...")
        slug_for_img = make_slug(article.get("english_title", "")) or "article"
        image_url = get_image_url(
            article.get("image_prompt", f"{category} india technology"),
            slug=slug_for_img,
        )
        log.info(f"  Image     : {image_url[:70]}...")

        # Step 3 — Publish or save draft
        if AUTO_PUBLISH:
            log.info(f"[3/3] Publishing to {PLATFORM.upper()}...")
            html = format_html(article, image_url)

            # Internal linking
            log.info("  Fetching existing posts for internal linking...")
            existing = fetch_existing_posts()
            html = add_internal_links(html, article, existing)

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