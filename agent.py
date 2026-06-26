#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
agent.py — Grounded Hindi Auto-Blogger for vigyankiduniya.com

KEY CHANGE vs the old agent:
    The old flow was: pick a topic -> ask Gemini to "write an article".
    That produced fabricated names, dates, quotes and stats (the #1 AdSense risk),
    plus 3-6 posts/day (the scaled-content-abuse risk).

    New flow:
        1. fetch_sources()  -> pull REAL, recent items from trusted RSS feeds
        2. if no fresh source for today's category -> EXIT WITHOUT POSTING
        3. generate_article(sources) -> Gemini writes ONLY from those sources,
           forbidden from inventing names/dates/quotes/stats, and must cite them
        4. publish with a real "Sources" section linking back to originals

This is the AdSense-safe default: slower cadence + grounded, citable content.

Env vars (same secrets as before):
    GEMINI_API_KEY, PLATFORM=blogger, BLOG_ID,
    GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, GOOGLE_REFRESH_TOKEN,
    AMAZON_TAG (optional), IMAGE_PROVIDER (optional)

requirements.txt:
    google-generativeai
    requests
    feedparser
"""

import os
import re
import sys
import json
import html
import time
import random
import datetime
import urllib.parse

import requests
import feedparser
import google.generativeai as genai

# ----------------------------------------------------------------------------
# CONFIG
# ----------------------------------------------------------------------------

GEMINI_API_KEY      = os.environ["GEMINI_API_KEY"]
BLOG_ID             = os.environ["BLOG_ID"]
GOOGLE_CLIENT_ID    = os.environ["GOOGLE_CLIENT_ID"]
GOOGLE_CLIENT_SECRET= os.environ["GOOGLE_CLIENT_SECRET"]
GOOGLE_REFRESH_TOKEN= os.environ["GOOGLE_REFRESH_TOKEN"]
AMAZON_TAG          = os.environ.get("AMAZON_TAG", "").strip()
IMAGE_PROVIDER      = os.environ.get("IMAGE_PROVIDER", "pollinations").strip()

# Model fallback chain (quota management) — same idea as before
MODEL_CHAIN = ["gemini-2.0-flash", "gemini-1.5-flash", "gemini-1.5-flash-8b"]

# How recent a source must be to count as "fresh". Older than this = skip.
MAX_SOURCE_AGE_HOURS = 48

# Stateless rotation across GitHub Actions runs
RUN_NUMBER = int(os.environ.get("GITHUB_RUN_NUMBER", random.randint(0, 9999)))

genai.configure(api_key=GEMINI_API_KEY)

# ----------------------------------------------------------------------------
# SOURCE FEEDS  (the grounding layer)
# Each category maps to real RSS feeds. Google News RSS lets us target Hindi
# science/tech queries; the rest are primary / reputable outlets.
# Add or swap feeds freely — just keep them real and reputable.
# ----------------------------------------------------------------------------

def gnews(query, lang="hi", country="IN"):
    q = urllib.parse.quote(query)
    return (f"https://news.google.com/rss/search?q={q}"
            f"&hl={lang}-{country}&gl={country}&ceid={country}:{lang}")

CATEGORY_FEEDS = {
    "science": [
        gnews("विज्ञान खोज ISRO OR research"),
        "https://www.isro.gov.in/media_isro/rss/LatestUpdates.xml",
        "http://export.arxiv.org/rss/physics",
    ],
    "technology": [
        gnews("technology भारत AI OR semiconductor OR gadget"),
        "https://news.google.com/rss/search?q=technology+india&hl=en-IN&gl=IN&ceid=IN:en",
    ],
    "automobile": [
        gnews("electric vehicle भारत OR EV battery OR Tata Motors"),
        "https://news.google.com/rss/search?q=electric+vehicle+india&hl=en-IN&gl=IN&ceid=IN:en",
    ],
}
CATEGORY_ORDER = ["science", "technology", "automobile"]


def pick_category():
    return CATEGORY_ORDER[RUN_NUMBER % len(CATEGORY_ORDER)]


def _entry_age_hours(entry):
    for key in ("published_parsed", "updated_parsed"):
        t = entry.get(key)
        if t:
            published = datetime.datetime(*t[:6], tzinfo=datetime.timezone.utc)
            now = datetime.datetime.now(datetime.timezone.utc)
            return (now - published).total_seconds() / 3600.0
    return None


def fetch_sources(category, want=3):
    """Return a list of fresh source dicts, or [] if nothing recent is found."""
    collected = []
    seen_titles = set()

    for feed_url in CATEGORY_FEEDS[category]:
        try:
            parsed = feedparser.parse(feed_url)
        except Exception as e:
            print(f"  [feed error] {feed_url}: {e}")
            continue

        for entry in parsed.entries:
            title = entry.get("title", "").strip()
            link = entry.get("link", "").strip()
            if not title or not link:
                continue

            age = _entry_age_hours(entry)
            if age is not None and age > MAX_SOURCE_AGE_HOURS:
                continue

            key = title.lower()[:80]
            if key in seen_titles:
                continue
            seen_titles.add(key)

            summary = re.sub(r"<[^>]+>", "", entry.get("summary", "")).strip()
            collected.append({
                "title": title,
                "link": link,
                "summary": summary[:600],
                "age_hours": round(age, 1) if age is not None else None,
            })
            if len(collected) >= want:
                break
        if len(collected) >= want:
            break

    return collected


# ----------------------------------------------------------------------------
# DUPLICATE DETECTION  (unchanged idea: don't re-cover what we just posted)
# ----------------------------------------------------------------------------

def get_recent_post_titles(access_token, days=7):
    try:
        since = (datetime.datetime.utcnow() - datetime.timedelta(days=days)
                 ).strftime("%Y-%m-%dT%H:%M:%SZ")
        url = (f"https://www.googleapis.com/blogger/v3/blogs/{BLOG_ID}/posts"
               f"?startDate={since}&maxResults=50&fetchBodies=false")
        r = requests.get(url, headers={"Authorization": f"Bearer {access_token}"},
                         timeout=30)
        r.raise_for_status()
        return [p.get("title", "") for p in r.json().get("items", [])]
    except Exception as e:
        print(f"  [recent posts warning] {e}")
        return []


def is_duplicate(candidate_title, recent_titles):
    cand = set(re.findall(r"[\w\u0900-\u097F]+", candidate_title.lower()))
    for t in recent_titles:
        words = set(re.findall(r"[\w\u0900-\u097F]+", t.lower()))
        if words and len(cand & words) / max(len(cand | words), 1) > 0.55:
            return True
    return False


# ----------------------------------------------------------------------------
# ARTICLE GENERATION  (constrained / grounded prompt)
# ----------------------------------------------------------------------------

SYSTEM_RULES = """आप एक तथ्य-आधारित हिंदी विज्ञान/टेक पत्रकार हैं।

सख्त नियम (इनका उल्लंघन न करें):
1. केवल नीचे दिए गए SOURCES में मौजूद तथ्यों का उपयोग करें।
2. किसी भी व्यक्ति का नाम, पद, उद्धरण (quote), तारीख, स्थान, संख्या या आँकड़ा
   तब तक न लिखें जब तक वह SOURCES में स्पष्ट रूप से मौजूद न हो।
3. यदि कोई जानकारी SOURCES में नहीं है, तो उसे छोड़ दें — अनुमान या कल्पना न करें।
4. कोई फर्जी "विशेषज्ञ का बयान" या "सूत्रों के अनुसार" मत गढ़िए।
5. शीर्षक सीधा और वर्णनात्मक हो। ये शब्द कभी इस्तेमाल न करें:
   "खुलासा", "तबाही", "तहलका", "उड़ी नींद", "मचाया", "चौंकाने वाला", "धमाका"।
6. भाषा सरल, साफ़ और भरोसेमंद हो — clickbait नहीं।
7. लेख 600–900 शब्दों का हो।
"""

GENERATION_PROMPT = """{rules}

SOURCES (केवल इन्हीं से लिखें):
{sources_block}

अब इस विषय पर एक मूल हिंदी लेख लिखें और केवल नीचे दिए गए JSON फॉर्मैट में उत्तर दें
(कोई markdown, कोई बैकटिक नहीं):

{{
  "title": "सीधा, वर्णनात्मक शीर्षक (clickbait शब्दों के बिना)",
  "meta_description": "150 अक्षरों तक का सटीक विवरण",
  "labels": ["3 से 5 प्रासंगिक हिंदी/अंग्रेज़ी टैग"],
  "key_highlights": ["3-5 मुख्य बिंदु, हर एक SOURCES पर आधारित"],
  "body_html": "<p>...</p> साफ़ HTML में लेख। केवल <p>, <h2>, <h3>, <ul>, <li>, <blockquote> का उपयोग करें। कोई काल्पनिक आँकड़ा नहीं।",
  "faqs": [{{"q": "प्रश्न", "a": "उत्तर (केवल SOURCES से)"}}],
  "amazon_relevant": false,
  "amazon_keyword": "यदि और केवल यदि कोई उत्पाद लेख से सीधे मेल खाता हो तो keyword, वरना खाली"
}}
"""


def build_sources_block(sources):
    lines = []
    for i, s in enumerate(sources, 1):
        lines.append(f"[{i}] {s['title']}\n    {s['summary']}\n    URL: {s['link']}")
    return "\n\n".join(lines)


def generate_article(sources):
    sources_block = build_sources_block(sources)
    prompt = GENERATION_PROMPT.format(rules=SYSTEM_RULES, sources_block=sources_block)

    last_err = None
    for model_name in MODEL_CHAIN:
        try:
            model = genai.GenerativeModel(model_name)
            resp = model.generate_content(
                prompt,
                generation_config={"temperature": 0.6, "max_output_tokens": 4096},
            )
            raw = resp.text.strip()
            raw = re.sub(r"^```(json)?|```$", "", raw, flags=re.MULTILINE).strip()
            data = json.loads(raw)
            print(f"  [model] generated with {model_name}")
            return data
        except Exception as e:
            last_err = e
            print(f"  [model fallback] {model_name} failed: {e}")
            time.sleep(2)
    raise RuntimeError(f"All models failed: {last_err}")


# ----------------------------------------------------------------------------
# GUARDRAIL: reject fabricated-looking output
# A cheap last line of defence — if the body invents specific stats/quotes that
# don't appear in any source, flag and skip rather than publish.
# ----------------------------------------------------------------------------

CLICKBAIT_WORDS = ["खुलासा", "तबाही", "तहलका", "मचाया", "मचाई",
                   "चौंकाने", "धमाका", "उड़ी नींद"]

def passes_guardrail(article, sources):
    title = article.get("title", "")
    for w in CLICKBAIT_WORDS:
        if w in title:
            print(f"  [guardrail] clickbait word '{w}' in title -> skip")
            return False

    body = article.get("body_html", "")
    if len(re.sub(r"<[^>]+>", "", body)) < 400:
        print("  [guardrail] body too short -> skip")
        return False

    # Quotes ("...") in the body must have support in source text.
    source_text = " ".join(s["summary"] + " " + s["title"] for s in sources).lower()
    quotes = re.findall(r"[\"“]([^\"”]{15,})[\"”]", body)
    for q in quotes:
        words = [w for w in re.findall(r"[\w\u0900-\u097F]+", q.lower()) if len(w) > 3]
        if words:
            overlap = sum(1 for w in words if w in source_text) / len(words)
            if overlap < 0.3:
                print("  [guardrail] unsupported quote detected -> skip")
                return False
    return True


# ----------------------------------------------------------------------------
# HTML ASSEMBLY  (Key Highlights, body, citations, FAQ, gated affiliate)
# ----------------------------------------------------------------------------

def render_key_highlights(items):
    if not items:
        return ""
    lis = "".join(f"<li>{html.escape(i)}</li>" for i in items)
    return ('<div class="key-highlights"><strong>💡 मुख्य बातें</strong>'
            f'<ul>{lis}</ul></div>')


def render_sources(sources):
    """The citation block — this is what turns 'AI filler' into a 'sourced write-up'."""
    lis = "".join(
        f'<li><a href="{html.escape(s["link"])}" target="_blank" '
        f'rel="nofollow noopener">{html.escape(s["title"])}</a></li>'
        for s in sources
    )
    return ('<div class="sources"><h3>स्रोत (Sources)</h3>'
            f'<ul>{lis}</ul>'
            '<p><em>यह लेख उपरोक्त सार्वजनिक स्रोतों पर आधारित है।</em></p></div>')


def render_faqs(faqs):
    if not faqs:
        return ""
    blocks = "".join(
        f'<h3>{html.escape(f["q"])}</h3><p>{html.escape(f["a"])}</p>'
        for f in faqs if f.get("q") and f.get("a")
    )
    return f'<div class="faq"><h2>अक्सर पूछे जाने वाले सवाल</h2>{blocks}</div>' if blocks else ""


def render_affiliate(article):
    """Only render if the model marked it relevant AND we have a tag AND a keyword."""
    if not AMAZON_TAG:
        return ""
    if not article.get("amazon_relevant"):
        return ""
    kw = (article.get("amazon_keyword") or "").strip()
    if not kw:
        return ""
    q = urllib.parse.quote(kw)
    link = f"https://www.amazon.in/s?k={q}&tag={AMAZON_TAG}&linkCode=ur2"
    return ('<div class="affiliate"><h3>संबंधित उत्पाद</h3>'
            f'<p><a href="{link}" target="_blank" rel="nofollow noopener sponsored">'
            f'{html.escape(kw)} — Amazon पर देखें →</a></p>'
            '<p><small>* Affiliate link — आपको कोई extra charge नहीं।</small></p></div>')


def build_jsonld(article, image_url, post_url, sources):
    """Single @graph block (avoids the Blogger JSON corruption issue)."""
    today = datetime.date.today().isoformat()
    graph = [
        {
            "@type": "Article",
            "headline": article["title"],
            "description": article.get("meta_description", ""),
            "image": image_url,
            "datePublished": today,
            "dateModified": today,
            "author": {"@type": "Person", "name": "रोहित कुमार"},
            "publisher": {
                "@type": "Organization",
                "name": "विज्ञान की दुनिया",
            },
            "citation": [s["link"] for s in sources],
        }
    ]
    faqs = article.get("faqs") or []
    if faqs:
        graph.append({
            "@type": "FAQPage",
            "mainEntity": [
                {"@type": "Question", "name": f["q"],
                 "acceptedAnswer": {"@type": "Answer", "text": f["a"]}}
                for f in faqs if f.get("q") and f.get("a")
            ],
        })
    return ('<script type="application/ld+json">'
            + json.dumps({"@context": "https://schema.org", "@graph": graph},
                         ensure_ascii=False)
            + "</script>")


def assemble_html(article, image_url, sources):
    parts = []
    if image_url:
        parts.append(f'<img src="{image_url}" alt="{html.escape(article["title"])}" '
                     f'width="1280" height="720"/>')
    parts.append(render_key_highlights(article.get("key_highlights")))
    parts.append('<!--more-->')
    parts.append(article.get("body_html", ""))
    parts.append(render_affiliate(article))
    parts.append(render_faqs(article.get("faqs")))
    parts.append(render_sources(sources))           # citations always present
    parts.append(build_jsonld(article, image_url, "", sources))
    return "\n".join(p for p in parts if p)


# ----------------------------------------------------------------------------
# IMAGE  (Pollinations -> catbox; Picsum fallback) — descriptive, not sensational
# ----------------------------------------------------------------------------

def make_image(title):
    if IMAGE_PROVIDER == "none":
        return ""
    try:
        prompt = urllib.parse.quote(f"editorial illustration, {title}, clean, realistic")
        poll = (f"https://image.pollinations.ai/prompt/{prompt}"
                f"?width=1280&height=720&model=flux&nologo=true&seed={random.randint(1,99999)}")
        img = requests.get(poll, timeout=60)
        if img.status_code == 200 and len(img.content) > 5000:
            up = requests.post("https://catbox.moe/user/api.php",
                               data={"reqtype": "fileupload"},
                               files={"fileToUpload": ("img.jpg", img.content, "image/jpeg")},
                               timeout=60)
            if up.status_code == 200 and up.text.startswith("http"):
                return up.text.strip()
            return poll
    except Exception as e:
        print(f"  [image warning] {e}")
    return f"https://picsum.photos/seed/{random.randint(1,99999)}/1280/720"


# ----------------------------------------------------------------------------
# BLOGGER PUBLISH  (OAuth refresh-token flow)
# ----------------------------------------------------------------------------

def get_access_token():
    r = requests.post("https://oauth2.googleapis.com/token", data={
        "client_id": GOOGLE_CLIENT_ID,
        "client_secret": GOOGLE_CLIENT_SECRET,
        "refresh_token": GOOGLE_REFRESH_TOKEN,
        "grant_type": "refresh_token",
    }, timeout=30)
    r.raise_for_status()
    return r.json()["access_token"]


def publish(access_token, title, html_body, labels):
    url = f"https://www.googleapis.com/blogger/v3/blogs/{BLOG_ID}/posts/"
    payload = {"kind": "blogger#post", "title": title,
               "content": html_body, "labels": labels}
    r = requests.post(url, headers={
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }, data=json.dumps(payload), timeout=60)
    r.raise_for_status()
    return r.json().get("url", "(published)")


# ----------------------------------------------------------------------------
# MAIN
# ----------------------------------------------------------------------------

def main():
    category = pick_category()
    print(f"== Category this run: {category} ==")

    # 1. GROUNDING GATE — no real source, no post.
    sources = fetch_sources(category)
    if not sources:
        print("No fresh, recent source found for this category. "
              "Exiting WITHOUT posting (this is intentional).")
        sys.exit(0)
    print(f"Found {len(sources)} source(s):")
    for s in sources:
        print(f"  - ({s['age_hours']}h) {s['title']}")

    token = get_access_token()

    # 2. Duplicate guard
    recent = get_recent_post_titles(token)

    # 3. Generate from sources only
    article = generate_article(sources)

    # 4. Guardrails
    if not passes_guardrail(article, sources):
        print("Generated article failed guardrail. Exiting without posting.")
        sys.exit(0)
    if is_duplicate(article["title"], recent):
        print(f"Too similar to a recent post: {article['title']}. Skipping.")
        sys.exit(0)

    # 5. Image + assemble + publish
    image_url = make_image(article["title"])
    html_body = assemble_html(article, image_url, sources)
    labels = article.get("labels", [])[:5] + [category]

    post_url = publish(token, article["title"], html_body, labels)
    print(f"PUBLISHED: {article['title']}\n{post_url}")


if __name__ == "__main__":
    main()
