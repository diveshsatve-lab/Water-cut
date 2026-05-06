import feedparser
import requests
import os
import json
import re
import time
from urllib.parse import quote, urlparse
from datetime import datetime
import pytz
from bs4 import BeautifulSoup
from google import genai as google_genai

# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# CONFIGURATION
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN')
CHAT_ID        = os.environ.get('CHAT_ID')
GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY')
GROQ_API_KEY   = os.environ.get('GROQ_API_KEY')

MY_AREA_NAME = "F-North Ward / F Ward / Sion / Matunga / Wadala / CGS"

RSS_URLS = [
    "https://news.google.com/rss/search?q=Mumbai+water+cut+when:1d&hl=en-IN&gl=IN&ceid=IN:en",
    "https://news.google.com/rss/search?q=BMC+water+supply+when:1d&hl=en-IN&gl=IN&ceid=IN:en",
]

# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# FILTER 1 â€” HEADLINE PATTERNS
# Must indicate a FULL water stoppage (not % reduction)
# Catches: "30-hour water cut", "24-Hour shutdown", "supply suspended" etc.
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
HEADLINE_FULL_CUT = [
    # X-hour water cut / shutdown / suspension
    r"\b\d+\s*[-â€“]?\s*hour\s+water\s+(?:cut|shut\s*down|shutdown|suspension|supply\s+cut)\b",
    r"\b\d+\s*[-â€“]?\s*hour\s+(?:water\s+)?supply\s+(?:cut|shutdown|suspension|disruption)\b",
    # supply suspended / stopped / halted / shut down
    r"\bwater\s+supply\s+(?:will\s+(?:be\s+|remain\s+)?)?(?:suspended|stopped|halted|shut\s*down|disrupted|cut\s+off)\b",
    r"\bsupply\s+(?:will\s+(?:be\s+)?)?(?:suspended|stopped|halted|shut\s*down|cut\s+off)\b",
    # water cut (not followed by %)
    r"\bwater\s+cut\b(?!\s*\d*\s*%)",
    # no water supply
    r"\bno\s+water\s+supply\b",
    # water shutdown / shutoff
    r"\bwater\s+shut(?:\s*down|-?off)\b",
    # complete / full water cut
    r"\b(?:complete|full|total)\s+water\s+(?:cut|shutdown|suspension|stoppage)\b",
    # dry taps
    r"\bdry\s+taps?\b",
    # BMC shuts / stops water
    r"\bbmc\s+(?:shuts?|stops?|suspends?|halts?)\s+water\b",
    # water disruption
    r"\bwater\s+(?:supply\s+)?disruption\b",
    # residents to face water cut
    r"\bface\s+(?:water\s+cut|no\s+water|water\s+shutdown)\b",
]

HEADLINE_PARTIAL_REJECT = [
    r"\b\d+\s*%\s*(?:water\s+)?cut\b",
    r"\b\d+\s*per\s*cent\s*(?:water\s+)?cut\b",
    r"\bwater\s+cut\s+(?:from|by)\b.*\d+\s*%",
    r"\bwater\s+shortage\b",
    r"\bwater\s+level\b",
    r"\bsupply\s+reduction\b",
    r"\breduced\s+(?:water\s+)?supply\b",
    r"\bwater\s+scarcity\b",
    r"\bpre[\s-]?monsoon\s+(?:cut|reduction)\b",
    r"\bwater\s+conservation\b",
    r"\bsave\s+water\b",
]

# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# FILTER 2 â€” ARTICLE BODY PATTERNS
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
AREA_PATTERNS = [
    r"\bf[\s\-]?north\b",     # F-North, F North, F-North Ward
    r"\bf[\s\-]?ward\b",      # F-Ward, F Ward
    r"\bsion\b",
    r"\bmatunga\b",
    r"\bwadala\b",
    r"\bcgs\b",               # CGS, CGS Colony
]

BODY_FULL_CUT = [
    r"\b\d+\s*[-â€“]?\s*hour\s+water\s+(?:cut|shut\s*down|shutdown|suspension|supply\s+cut)\b",
    r"\b\d+\s*[-â€“]?\s*hour\s+(?:water\s+)?supply\s+(?:cut|shutdown|suspension|disruption)\b",
    r"\bwater\s+supply\s+(?:will\s+(?:be\s+|remain\s+)?)?(?:suspended|stopped|halted|shut\s*down|disrupted|cut\s+off)\b",
    r"\bsupply\s+(?:will\s+(?:be\s+)?)?(?:suspended|stopped|halted|shut\s*down|cut\s+off)\b",
    r"\bwater\s+cut\b(?!\s*\d*\s*%)",
    r"\bno\s+water\s+supply\b",
    r"\bwater\s+shut(?:\s*down|-?off)\b",
    r"\b(?:complete|full|total)\s+water\s+(?:cut|shutdown|suspension|stoppage)\b",
    r"\bdry\s+taps?\b",
]


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# HELPERS
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
def get_ist_time():
    return datetime.now(pytz.utc).astimezone(pytz.timezone('Asia/Kolkata'))


def is_published_recently(struct, max_hours=24):
    if not struct:
        return False
    pub_utc   = datetime(*struct[:6], tzinfo=pytz.utc)
    age_hours = (datetime.now(pytz.utc) - pub_utc).total_seconds() / 3600
    return age_hours <= max_hours


def matches_any(patterns, text):
    return any(re.search(p, text, flags=re.IGNORECASE) for p in patterns)


def decode_google_news_url(google_url):
    """Resolve the real article URL from Google News encoded link."""
    try:
        gn_art_id = urlparse(google_url).path.split("/")[-1]
        if not gn_art_id.startswith("CB"):
            return google_url
        hdrs = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        r = requests.get(
            f"https://news.google.com/rss/articles/{gn_art_id}",
            headers=hdrs, timeout=10)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        div  = soup.select_one("c-wiz > div")
        if not div:
            return google_url
        sig = div.get("data-n-a-sg")
        ts  = div.get("data-n-a-ts")
        req = [
            "Fbv4je",
            f'["garturlreq",[["en-IN","IN",["FINANCE_TOP_INDICES","WEB_TEST_1_0_0"],'
            f'null,null,1,1,"IN:en",null,180,null,null,null,null,null,0,null,null,'
            f'[1608992183,723341000]],"en-IN","IN",1,[2,3,4,8],1,0,"655000234",0,0,'
            f'null,0],"{gn_art_id}",{ts},"{sig}"]'
        ]
        payload = f"f.req={quote(json.dumps([[req]]))}"
        pr = requests.post(
            "https://news.google.com/_/DotsSplashUi/data/batchexecute",
            headers={"Content-Type": "application/x-www-form-urlencoded;charset=UTF-8"},
            data=payload, timeout=10)
        pr.raise_for_status()
        parts = pr.text.split("\n\n")
        if len(parts) >= 2:
            return json.loads(json.loads(parts[1])[0][2])[1]
        return google_url
    except Exception as e:
        print(f"      âš ï¸ URL decode failed: {e}")
        return google_url


def get_article_text(url):
    try:
        r    = requests.get(url,
                            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
                            timeout=12, allow_redirects=True)
        soup = BeautifulSoup(r.content, 'html.parser')
        text = ' '.join(p.get_text(' ', strip=True) for p in soup.find_all('p'))
        return text[:5000]
    except Exception as e:
        print(f"      âš ï¸ Scrape failed: {e}")
        return ""


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# SHARED PROMPT BUILDER
# Same logic used by both Gemini and Groq
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
def build_prompt(headline, article_text):
    current_date = get_ist_time().strftime("%Y-%m-%d")
    return f"""Current Date: {current_date}
HEADLINE: "{headline}"
ARTICLE TEXT: "{article_text}"

TASK: Does this article describe a COMPLETE water supply shutdown/suspension that affects F-North Ward (Sion, Matunga, Wadala, CGS Colony)?

LOGIC:
1. If the article lists specific wards (e.g., "K-East", "H-West") and DOES NOT mention F-North/Sion/Matunga -> Reply NO.
2. If the article says "Whole Mumbai" or "All Wards" -> Reply NO (assume false alarm unless F-North or F Ward is explicitly named).
3. ONLY Reply YES if you see the words: "F-North Ward", "F Ward", "Sion", "Matunga", "Wadala", "CGS", or "F-North".

OUTPUT FORMAT (one line only):
YES | [Short one-line summary of who is affected and for how long]
or
NO"""


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# AI LAYER 1 â€” GEMINI
# Reads exact retryDelay from error. Falls back to Groq on
# daily quota exhaustion.
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
gemini_client = None
if GEMINI_API_KEY:
    try:
        gemini_client = google_genai.Client(api_key=GEMINI_API_KEY)
        print("âœ… Gemini client ready")
    except Exception as e:
        print(f"âš ï¸ Gemini init failed: {e}")


def ask_gemini(headline, article_text):
    """
    Returns:
      "YES | summary"           â€” confirmed relevant
      "NO"                      â€” confirmed not relevant
      "DAILY_QUOTA_EXHAUSTED"   â€” switch to Groq
    """
    if not gemini_client:
        return "DAILY_QUOTA_EXHAUSTED"

    prompt = build_prompt(headline, article_text)

    for attempt in range(1, 3):   # max 2 attempts
        try:
            print(f"      ðŸ¤– Gemini attempt {attempt}...")
            response = gemini_client.models.generate_content(
                model="gemini-2.0-flash", contents=prompt
            )
            result = (response.text or "NO").strip().replace("**", "")
            print(f"      ðŸ’¬ Gemini: {result[:80]}")
            return result

        except Exception as e:
            err = str(e)
            is_per_minute  = "GenerateRequestsPerMinutePerProjectPerModel" in err
            is_per_day     = "GenerateRequestsPerDayPerProjectPerModel"    in err
            is_token_limit = "GenerateContentInputTokensPerModelPerMinute" in err
            delay_match    = re.search(r"retryDelay.*?(\d+)s", err)
            suggested_wait = (int(delay_match.group(1)) + 3) if delay_match else None

            # True daily limit gone â€” no retry possible
            if is_per_day and not is_per_minute:
                print("      âŒ Gemini daily quota exhausted â†’ switching to Groq.")
                return "DAILY_QUOTA_EXHAUSTED"

            # Per-minute rate limit â€” wait exactly what API says, retry once
            if (is_per_minute or is_token_limit) and attempt == 1:
                wait = suggested_wait if suggested_wait else 65
                print(f"      â³ Gemini rate limit. API says wait {wait}s. Waiting...")
                time.sleep(wait)
                continue

            # Any other error
            print(f"      âš ï¸ Gemini error (attempt {attempt}): {err[:150]}")
            if attempt == 1:
                time.sleep(10)
            else:
                return "NO"

    return "NO"


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# AI LAYER 2 â€” GROQ (fallback, LLaMA 3.3 70B, 14,400 req/day)
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
def ask_groq(headline, article_text):
    """
    Returns:
      "YES | summary"           â€” confirmed relevant
      "NO"                      â€” confirmed not relevant
      "DAILY_QUOTA_EXHAUSTED"   â€” both AIs exhausted, send fallback alert
    """
    if not GROQ_API_KEY:
        print("      âš ï¸ GROQ_API_KEY not set.")
        return "DAILY_QUOTA_EXHAUSTED"

    prompt  = build_prompt(headline, article_text)
    url     = "https://api.groq.com/openai/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type":  "application/json",
    }
    payload = {
        "model":       "llama-3.3-70b-versatile",
        "messages":    [{"role": "user", "content": prompt}],
        "max_tokens":  80,
        "temperature": 0,
    }

    for attempt in range(1, 3):
        try:
            print(f"      ðŸ¦™ Groq attempt {attempt}...")
            r = requests.post(url, headers=headers, json=payload, timeout=20)

            if r.status_code == 429:
                err_body = ""
                try:    err_body = r.json().get("error", {}).get("message", "")
                except: pass

                # Daily limit gone
                if "day" in err_body.lower():
                    print("      âŒ Groq daily quota exhausted â†’ using fallback alert.")
                    return "DAILY_QUOTA_EXHAUSTED"

                # Per-minute limit â€” read retry-after header
                retry_after = int(r.headers.get("retry-after", 60)) + 3
                print(f"      â³ Groq rate limit. Waiting {retry_after}s as requested...")
                time.sleep(retry_after)
                continue

            r.raise_for_status()
            result = r.json()["choices"][0]["message"]["content"].strip()
            print(f"      ðŸ’¬ Groq: {result[:80]}")
            return result

        except requests.exceptions.HTTPError as e:
            print(f"      âš ï¸ Groq HTTP error (attempt {attempt}): {e}")
            if attempt == 1:
                time.sleep(10)
            else:
                return "NO"
        except Exception as e:
            print(f"      âš ï¸ Groq error (attempt {attempt}): {e}")
            return "NO"

    return "NO"


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# TELEGRAM
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
def send_telegram_message(message):
    if not TELEGRAM_TOKEN or not CHAT_ID:
        print("      âš ï¸ Telegram credentials missing.")
        return
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": CHAT_ID, "text": message, "parse_mode": "Markdown"},
            timeout=10)
        if r.status_code != 200:
            print(f"      âš ï¸ Telegram error {r.status_code}: {r.text[:100]}")
        else:
            print("      âœ… Telegram message sent.")
    except Exception as e:
        print(f"      âš ï¸ Telegram failed: {e}")


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# MAIN
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
def check_water_cuts():
    print(f"\n{'â”'*60}")
    print(f"  ðŸ” Water Cut Scanner â€” {get_ist_time().strftime('%d %b %Y, %H:%M IST')}")
    print(f"  Area: {MY_AREA_NAME}")
    print(f"{'â”'*60}\n")

    seen_titles      = set()
    total            = 0
    passed_f1        = 0
    passed_f2        = 0
    gemini_calls     = 0
    groq_calls       = 0
    alerts_sent      = 0
    gemini_exhausted = False   # True once Gemini daily quota is gone
    groq_exhausted   = False   # True once Groq daily quota is gone

    for rss_url in RSS_URLS:
        feed = feedparser.parse(rss_url)
        print(f"ðŸ“¡ Feed returned {len(feed.entries)} entries.\n")

        for entry in feed.entries:
            title = getattr(entry, 'title', '').strip()
            link  = getattr(entry, 'link',  '').strip()
            if not title or not link:
                continue

            # â”€â”€ Deduplication (by normalised title) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
            norm = re.sub(r'\s+', ' ', title.lower())
            if norm in seen_titles:
                continue
            seen_titles.add(norm)

            # â”€â”€ Recency: must be within last 24 hours (IST-safe) â”€â”€â”€â”€â”€â”€â”€â”€â”€
            if not is_published_recently(entry.get('published_parsed')):
                continue

            # â”€â”€ Must contain "water" somewhere in headline â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
            if 'water' not in title.lower():
                continue

            total += 1
            print(f"ðŸ‘‰ [{total}] {title[:85]}")

            # â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
            # FILTER 1 â€” HEADLINE CHECK (zero network requests)
            # â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
            is_partial = matches_any(HEADLINE_PARTIAL_REJECT, title)
            is_full    = matches_any(HEADLINE_FULL_CUT,       title)

            if is_partial and not is_full:
                print("   â© [F1] Partial / % cut in headline. Skipped.\n")
                continue
            if not is_full:
                print("   â© [F1] No full-cut signal in headline. Skipped.\n")
                continue

            print("   âœ… [F1] Full water cut in headline. Scraping article now...")

            # â”€â”€ Decode Google News redirect â†’ real URL â†’ scrape â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
            real_url     = decode_google_news_url(link)
            article_text = get_article_text(real_url)
            combined     = f"{title} {article_text}"
            passed_f1   += 1

            # â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
            # FILTER 2 â€” BODY CHECK
            # Both area keyword AND full-cut phrase must appear in body
            # â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
            area_found = matches_any(AREA_PATTERNS, combined)
            body_full  = matches_any(BODY_FULL_CUT,  combined)

            if not area_found:
                print("   â© [F2] Area keyword not found in article body. Skipped.\n")
                continue
            if not body_full:
                print("   â© [F2] Area found but no full-cut phrase in body. Skipped.\n")
                continue

            matched_kw = next(
                (p for p in AREA_PATTERNS if re.search(p, combined, re.I)), "?")
            print(f"   ðŸŽ¯ [F2] PASSED! Area='{matched_kw}' + full-cut phrase confirmed.")
            passed_f2 += 1

            # â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
            # AI CASCADE â€” Gemini first â†’ Groq fallback â†’ direct fallback
            # Articles are sent to AI ONE AT A TIME
            # â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
            decision      = None
            source_label  = ""

            # â”€â”€ Try Gemini â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
            if not gemini_exhausted:
                gemini_calls += 1
                decision = ask_gemini(title, article_text)
                if decision == "DAILY_QUOTA_EXHAUSTED":
                    gemini_exhausted = True
                    decision = None   # pass down to Groq

            # â”€â”€ Try Groq (if Gemini exhausted or unavailable) â”€â”€â”€â”€â”€â”€â”€â”€â”€
            if decision is None and not groq_exhausted:
                groq_calls += 1
                decision = ask_groq(title, article_text)
                if decision == "DAILY_QUOTA_EXHAUSTED":
                    groq_exhausted = True
                    decision = None   # pass down to local fallback

            # â”€â”€ Both APIs exhausted â†’ local filter fallback â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
            if decision is None:
                print("   âš ï¸ Both APIs exhausted â€” using LOCAL FILTER FALLBACK.")
                decision     = "FALLBACK"
                source_label = "âš ï¸ Local Filter Fallback (AI unavailable â€” verify manually)"

            # â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
            # DECIDE: SEND OR SKIP
            # â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
            if decision.upper().startswith("YES"):
                try:    summary = decision.split("|", 1)[1].strip()
                except: summary = "Check article for details."
                if not source_label:
                    source_label = "Gemini AI" if not gemini_exhausted else "Groq AI (LLaMA 3.3 70B)"

            elif decision == "FALLBACK":
                summary = ("AI verification unavailable (both Gemini & Groq quota exhausted). "
                           "Article passed all local filters. Please verify manually.")

            else:
                # AI said NO
                ai_name = "Gemini" if not gemini_exhausted else "Groq"
                print(f"   âœ… {ai_name} confirmed: NOT affecting F-North. Skipped.\n")
                continue

            # â”€â”€ Build and send Telegram message â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
            msg = (
                f"ðŸš° *Water Cut Alert*\n"
                f"ðŸ“ *Area: F-North / Sion / Matunga / Wadala / CGS*\n"
                f"ðŸ“ {summary}\n\n"
                f"ðŸ“° {title}\n"
                f"ðŸ”— [Read Article]({real_url})\n"
                f"_Verified by: {source_label}_"
            )
            print("      ðŸš¨ Sending Telegram alert...")
            send_telegram_message(msg)
            alerts_sent += 1
            print()

    # â”€â”€ Final run summary â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    print(f"\n{'â”'*60}")
    print(f"  SCAN COMPLETE  [{get_ist_time().strftime('%H:%M IST')}]")
    print(f"  Total articles scanned        : {total}")
    print(f"  Passed Filter 1 (headline)    : {passed_f1}")
    print(f"  Passed Filter 2 (body + area) : {passed_f2}")
    print(f"  Gemini API calls made         : {gemini_calls}")
    print(f"  Groq API calls made           : {groq_calls}")
    print(f"  Alerts sent                   : {alerts_sent}")
    if gemini_exhausted:
        print("  âš ï¸  Gemini daily quota was exhausted â€” Groq was used as fallback.")
    if groq_exhausted:
        print("  âŒ  Groq daily quota also exhausted â€” local filter fallback was used.")
    if alerts_sent == 0:
        print("  âœ… No water cuts detected for F-North today.")
    print(f"{'â”'*60}\n")


if __name__ == "__main__":
    check_water_cuts()
