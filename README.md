# 🤖 Hindi Auto-Blogger Agent

Automatically writes SEO-optimised **Hindi articles** on Science, Technology, and Automobile — and publishes them directly to **WordPress** or **Blogger** every 6 hours using Gemini AI.

---

## How It Works

```
Gemini AI (web knowledge)
        ↓
Trending topic selection
        ↓
Hindi article generation (900+ words, SEO-optimised)
        ↓
Hero image (Pollinations.ai — free)
        ↓
WordPress / Blogger  →  Live post ✅
```

---

## Features

- **Hindi articles** — Devanagari script, 900+ words, professional tone
- **SEO-ready** — Focus keyword, meta description, 7 keywords, JSON-LD schema
- **Auto images** — Free WebP hero images via Pollinations.ai
- **Dual platform** — WordPress (recommended) or Blogger
- **WordPress** — Auto-fills Yoast SEO / RankMath fields
- **Free hosting** — GitHub Actions (2000 min/month free)
- **4 articles/day** — Cycles through Science → Technology → Automobile

---

## Quick Start

### 1. Clone the repo

```bash
git clone https://github.com/YOUR_USERNAME/hindi-blogger-agent
cd hindi-blogger-agent
pip install -r requirements.txt
```

### 2. Set up environment

```bash
cp .env.example .env
# Edit .env with your credentials
```

### 3. Run locally

```bash
python agent.py
```

---

## GitHub Actions Setup (Free 24/7)

### Step 1 — Get your API keys

| Key | Where to get it |
|-----|-----------------|
| `GEMINI_API_KEY` | [aistudio.google.com/apikey](https://aistudio.google.com/apikey) — free |
| `WP_APP_PASSWORD` | WordPress → Users → Profile → Application Passwords |

### Step 2 — Add GitHub Secrets

Go to your repo → **Settings → Secrets and variables → Actions → New repository secret**

Add these secrets:

**For WordPress:**
```
GEMINI_API_KEY       → your Gemini API key
PLATFORM             → wordpress
WP_URL               → https://yoursite.com
WP_USERNAME          → your WordPress username
WP_APP_PASSWORD      → xxxx xxxx xxxx xxxx xxxx xxxx
```

**For Blogger (if using Blogger instead):**
```
GEMINI_API_KEY       → your Gemini API key
PLATFORM             → blogger
BLOG_ID              → your numeric Blogger blog ID
GOOGLE_CLIENT_ID     → from Google Cloud Console
GOOGLE_CLIENT_SECRET → from Google Cloud Console
GOOGLE_REFRESH_TOKEN → from python setup_auth.py
```

### Step 3 — Enable Actions

Go to **Actions tab** → Enable workflows → Click **"Run workflow"** to test.

---

## Post Schedule

| Time (IST) | Category |
|-----------|----------|
| 07:30 AM | Science |
| 01:30 PM | Technology |
| 07:30 PM | Automobile |
| 01:30 AM | Science |

Change times in `.github/workflows/auto-post.yml`.

---

## Article Output

Each published post includes:

- SEO-optimised Hindi title with primary keyword
- Meta description (150 chars)
- 7 focus keywords
- Fully formatted HTML article (900+ words)
- H2/H3 headings, bullet points
- AI hero image (1024×576, free)
- JSON-LD schema for Google rich results
- Social excerpt

---

## Supported Platforms

### WordPress ✅ (Recommended)
- Auto-sets slug, tags, Yoast SEO description, focus keyword
- Works with any WordPress.org install
- Requires Application Password (not your main password)

### Blogger ✅
- Auto-sets permalink from English title
- Sets location to India
- JSON-LD schema in HTML for Google SEO
- Search Description — set manually in Blogger Dashboard (API limitation)

---

## Local Development

```bash
# Test without publishing
AUTO_PUBLISH=false python agent.py
# Drafts saved to drafts/ folder

# Force a specific category
python run_once.py --category technology

# Blogger OAuth setup (one-time)
python setup_auth.py
```

---

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `GEMINI_API_KEY` | ✅ | Gemini API key (free) |
| `PLATFORM` | ✅ | `wordpress` or `blogger` |
| `WP_URL` | WordPress | Your site URL |
| `WP_USERNAME` | WordPress | Admin username |
| `WP_APP_PASSWORD` | WordPress | Application password |
| `BLOG_ID` | Blogger | Numeric blog ID |
| `GOOGLE_CLIENT_ID` | Blogger | OAuth2 client ID |
| `GOOGLE_CLIENT_SECRET` | Blogger | OAuth2 client secret |
| `GOOGLE_REFRESH_TOKEN` | Blogger | From setup_auth.py |
| `INTERVAL_HOURS` | ❌ | Hours between posts (default: 6) |
| `AUTO_PUBLISH` | ❌ | true/false (default: true) |

---

## Tech Stack

| Component | Tool |
|-----------|------|
| AI Model | Gemini Flash (free tier) |
| Images | Pollinations.ai (free) |
| Scheduler | APScheduler + GitHub Actions |
| WordPress API | WP REST API v2 |
| Blogger API | Google Blogger API v3 |
| Language | Python 3.11+ |

---

## License

MIT — free to use, modify, and sell.
