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

# â”€â”€ Retry budgets â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
GEMINI_RPM_RETRY_BUDGET = 3
GROQ_TPM_RETRY_BUDGET   = 3
MAX_OTHER_ERRORS        = 3

# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# FILTER PATTERNS
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
HEADLINE_FULL_CUT = [
    # X-hour water cut / shutdown / suspension / disruption / snapped
    r"\b\d+\s*[-â€“â€”]?\s*hour\s+water\s+(?:cut|shut\s*down|shutdown|suspension|supply\s+cut)\b",
    r"\b\d+\s*[-â€“â€”]?\s*hour\s+(?:water\s+)?supply\s+(?:cut|shutdown|suspension|disrupt(?:ion|ed|s)?|snapped)\b",

    # supply suspended / stopped / halted / shut down / disrupted / hit / affected / snapped / unavailable
    r"\bwater\s+supply\s+(?:(?:will|is|to)\s+(?:be\s+|remain\s+)?)?(?:suspended|stopped|halted|shut\s*down|disrupt(?:ed|s)?|cut\s+off|hit|affected|snapped|unavailable)\b",
    r"\bsupply\s+(?:(?:will|is|to)\s+(?:be\s+|remain\s+)?)?(?:suspended|stopped|halted|shut\s*down|cut\s+off|hit|affected|snapped|unavailable)\b",

    # water cut (not followed by %)
    r"\bwater\s+cut\b(?!\s*\d*\s*%)",

    # no water / zero water
    r"\b(?:no|zero)\s+water\s+supply\b",
    r"\bno\s+water\s+(?:for|in|on|tomorrow|today)\b",

    # water shutdown / shutoff / snapped
    r"\bwater\s+(?:shut(?:\s*down|-?off)|snapped)\b",

    # complete / full / total water cut / stoppage
    r"\b(?:complete|full|total)\s+water\s+(?:cut|shutdown|suspension|stoppage)\b",

    # dry taps / go dry
    r"\bdry\s+taps?\b",
    r"\b(?:go|goes|going)\s+dry\b",

    # BMC shuts / stops / suspends / halts / snaps water
    r"\bbmc\s+(?:shuts?|stops?|suspends?|halts?|snaps?)\s+water\b",

    # general water disruption / hit / affected / snapped
    r"\bwater\s+(?:supply\s+)?(?:disrupt(?:ion|s|ed)|hit|affected|snapped)\b",

    # residents to face water cut / dry taps
    r"\bface\s+(?:water\s+cut|no\s+water|water\s+shutdown|dry\s+taps)\b",
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

AREA_PATTERNS = [
    # F-North (Catches: F North, F-North, F/North, FNorth)
    r"\bf[\s\-/]?north\b",

    # F Ward (Catches: F Ward, F-Ward, F/Ward)
    r"\bf[\s\-/]?ward\b",

    # BMC abbreviation (Catches: F/N, F-N, F N, F/N Ward)
    r"\bf[\s\-/]n\b",

    # Sion & sub-areas (Catches: Sion, Sion East, Sion(E), Sion Koliwada)
    r"\bsion\b",

    # Matunga & sub-areas (Catches: Matunga, Matunga East, Matunga Labour Camp)
    r"\bmatunga\b",

    # Wadala & sub-areas (Catches: Wadala, Wadala TT, Wadala East)
    r"\bwadala\b",

    # CGS Colony (Catches: CGS, C.G.S, C.G.S., C G S, CGS Colony)
    r"\bc\.?\s*g\.?\s*s\.?\b",
]

BODY_FULL_CUT = [
    # X-hour water cut / shutdown / suspension / disruption / snapped
    r"\b\d+\s*[-â€“â€”]?\s*hour\s+water\s+(?:cut|shut\s*down|shutdown|suspension|supply\s+cut)\b",
    r"\b\d+\s*[-â€“â€”]?\s*hour\s+(?:water\s+)?supply\s+(?:cut|shutdown|suspension|disrupt(?:ion|ed|s)?|snapped)\b",

    # supply suspended / stopped / halted / shut down / disrupted / hit / affected / snapped / unavailable
    r"\bwater\s+supply\s+(?:(?:will|is|to)\s+(?:be\s+|remain\s+)?)?(?:suspended|stopped|halted|shut\s*down|disrupt(?:ed|s)?|cut\s+off|hit|affected|snapped|unavailable)\b",
    r"\bsupply\s+(?:(?:will|is|to)\s+(?:be\s+|remain\s+)?)?(?:suspended|stopped|halted|shut\s*down|cut\s+off|hit|affected|snapped|unavailable)\b",

    # water cut (not followed by %)
    r"\bwater\s+cut\b(?!\s*\d*\s*%)",

    # no water / zero water
    r"\b(?:no|zero)\s+water\s+supply\b",
    r"\bno\s+water\s+(?:for|in|on|tomorrow|today)\b",

    # water shutdown / shutoff / snapped
    r"\bwater\s+(?:shut(?:\s*down|-?off)|snapped)\b",

    # complete / full / total water cut / stoppage
    r"\b(?:complete|full|total)\s+water\s+(?:cut|shutdown|suspension|stoppage)\b",

    # dry taps / go dry
    r"\bdry\s+taps?\b",
    r"\b(?:go|goes|going)\s+dry\b",

    # BMC shuts / stops / suspends / halts / snaps water
    r"\bbmc\s+(?:shuts?|stops?|suspends?|halts?|snaps?)\s+water\b",

    # general water disruption / hit / affected / snapped
    r"\bwater\s+(?:supply\s+)?(?:disrupt(?:ion|s|ed)|hit|affected|snapped)\b",

    # residents to face water cut / dry taps
    r"\bface\s+(?:water\s+cut|no\s+water|water\s+shutdown|dry\s+taps)\b",
]


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# HELPERS
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
def get_ist_time():
    return datetime.now(pytz.utc).astimezone(pytz.timezone('Asia/Kolkata'))

def is_published_recently(entry, max_hours=24):
    """
    Returns True only if the article was originally published within max_hours.
    Uses the OLDER of published_parsed vs updated_parsed to avoid Google News
    re-crawl tricks where an April article gets a fresh updated timestamp today.
    If no date is found at all, conservatively returns False (skip the article).
    """
    now_utc = datetime.now(pytz.utc)

    candidates = []
    for field in ('published_parsed', 'updated_parsed'):
        struct = entry.get(field) if isinstance(entry, dict) else getattr(entry, field, None)
        if struct:
            try:
                candidates.append(datetime(*struct[:6], tzinfo=pytz.utc))
            except Exception:
                pass

    if not candidates:
        return False  # No date at all â€” skip conservatively

    # Use the OLDEST date found â€” catches re-crawled articles
    oldest = min(candidates)
    age_hours = (now_utc - oldest).total_seconds() / 3600
    return age_hours <= max_hours

def matches_any(patterns, text):
    return any(re.search(p, text, flags=re.IGNORECASE) for p in patterns)

def decode_google_news_url(google_url):
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
        r    = requests.get(
            url,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
            timeout=12, allow_redirects=True)
        soup = BeautifulSoup(r.content, 'html.parser')
        text = ' '.join(p.get_text(' ', strip=True) for p in soup.find_all('p'))
        return text[:5000]
    except Exception as e:
        print(f"      âš ï¸ Scrape failed: {e}")
        return ""

def build_prompt(headline, article_text):
    current_dt  = get_ist_time()
    current_str = current_dt.strftime("%Y-%m-%d %H:%M")
    return f"""You are a strict data extraction bot. Follow these two tasks exactly.

Current Date & Time (IST): {current_str}
HEADLINE: "{headline}"
ARTICLE TEXT: "{article_text}"

TASK 1 â€” LOCATION CHECK:
Does the text EXPLICITLY name any of these as an AFFECTED area?
  F-North Ward, F-North, F Ward, Sion, Matunga, Wadala, CGS Colony
Rules:
- Only count it if the location is listed as an affected area.
- Do NOT count it if the location appears only as a road name, pump station, or background context.
- Do NOT count "south Mumbai" or "central Mumbai" unless F-North/Sion/Matunga/Wadala/CGS is also named.
If TASK 1 is NO -> output NOT_CONFIRMED on a single line and stop.

TASK 2 â€” DATE EXTRACTION (only if TASK 1 is YES):
Find when the water cut ENDS (or STARTS if end time is not mentioned).
Translate that time to this exact format: YYYY-MM-DD HH:MM (IST)
Rules:
- If article says "tomorrow at 4 PM" and today is {current_str[:10]}, output the next day at 16:00.
- If no time is found at all, output UNKNOWN.
- Do NOT decide if the time is past or future. Just translate it.

OUTPUT FORMAT (exactly one line, no extra text):
CONFIRMED | [exact location name] | [YYYY-MM-DD HH:MM or UNKNOWN]
NOT_CONFIRMED | [one sentence reason]"""
def _parse_gemini_wait(err_str):
    m = re.search(r"retryDelay[\"'\s:]+(\d+)s", err_str)
    return (int(m.group(1)) + 3) if m else 65

def _parse_groq_wait(headers, err_body_str):
    try:
        val = headers.get("retry-after", "").strip()
        if val.isdigit():
            return int(val) + 3
    except Exception:
        pass
    m = re.search(r"please try again in ([\d.]+)s", err_body_str, re.I)
    if m:
        return int(float(m.group(1))) + 3
    return 63

def _is_gemini_hard_daily(err_str):
    """
    True ONLY for RPD exhaustion â€” NOT for per-minute RPM rate limits.
    Catches both the structured PerDay error AND the generic quota message
    seen in yesterday's logs: 'You exceeded your current quota'.
    """
    err_lower   = err_str.lower()
    has_per_day = "requestsperday" in err_lower or "perday" in err_lower
    has_per_min = "perminute" in err_lower
    hard_zero   = bool(re.search(r'"limit"\s*:\s*0\b', err_lower))
    generic_quota = "exceeded your current quota" in err_lower
    return (has_per_day and not has_per_min and hard_zero) or generic_quota

def _is_groq_hard_daily(err_body_str):
    return bool(re.search(r"(day|daily|per.day)", err_body_str, re.I))


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# AI LAYER 1 â€” GEMINI
#
# Return codes (3 distinct values, not 2):
#   "DAILY_EXHAUSTED"  â†’ hard RPD gone; set sticky flag, skip rest of run
#   "USE_GROQ"         â†’ RPM budget drained for THIS article; try Groq now
#                        but DON'T set sticky flag â€” Gemini may recover next article
#   "YES | summary"    â†’ confirmed relevant
#   "NO"               â†’ confirmed not relevant
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
gemini_client = None
if GEMINI_API_KEY:
    try:
        gemini_client = google_genai.Client(api_key=GEMINI_API_KEY)
        print("âœ… Gemini client ready")
    except Exception as e:
        print(f"âš ï¸ Gemini init failed: {e}")

def ask_gemini(headline, article_text):
    if not gemini_client:
        print("      âš ï¸ Gemini not initialised â†’ Groq.")
        return "USE_GROQ"

    prompt          = build_prompt(headline, article_text)
    rpm_budget_left = GEMINI_RPM_RETRY_BUDGET
    other_errors    = 0
    attempt         = 0

    while True:
        attempt += 1
        try:
            print(f"      ðŸ¤– Gemini attempt {attempt} "
                  f"[RPM budget remaining: {rpm_budget_left}]...")
            response = gemini_client.models.generate_content(
                model="gemini-2.0-flash", contents=prompt
            )
            result = (response.text or "").strip().replace("**", "")
            if not result:
                raise ValueError("Empty response from Gemini")
            other_errors = 0
            print(f"      ðŸ’¬ Gemini: {result[:100]}")
            return result

        except Exception as e:
            err = str(e)
            print(f"      âš ï¸ Gemini error (attempt {attempt}): {err[:250]}")

            if _is_gemini_hard_daily(err):
                print("      âŒ Gemini RPD (daily) exhausted â†’ permanently switching to Groq.")
                return "DAILY_EXHAUSTED"

            if "429" in err or "RESOURCE_EXHAUSTED" in err:
                if rpm_budget_left <= 0:
                    print(f"      âŒ Gemini RPM retry budget exhausted "
                          f"({GEMINI_RPM_RETRY_BUDGET} waits done) â†’ Groq for THIS article.")
                    return "USE_GROQ"
                wait = _parse_gemini_wait(err)
                rpm_budget_left -= 1
                print(f"      â³ Gemini RPM limit. Waiting {wait}s "
                      f"[budget left after this: {rpm_budget_left}]...")
                time.sleep(wait)
                continue

            other_errors += 1
            if other_errors >= MAX_OTHER_ERRORS:
                print(f"      âŒ Gemini {MAX_OTHER_ERRORS} consecutive "
                      f"non-rate errors â†’ Groq for THIS article.")
                return "USE_GROQ"
            print(f"      â³ Transient error #{other_errors}. Waiting 12s...")
            time.sleep(12)


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# AI LAYER 2 â€” GROQ
#
# Return codes:
#   "BOTH_EXHAUSTED"  â†’ hard RPD gone; set sticky flag
#   "YES | summary"   â†’ confirmed relevant
#   "NO"              â†’ confirmed not relevant
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"

def ask_groq(headline, article_text):
    if not GROQ_API_KEY:
        print("      âš ï¸ GROQ_API_KEY not set â†’ bypass alert.")
        return "BOTH_EXHAUSTED"

    prompt          = build_prompt(headline, article_text)
    payload         = {
        "model":       "llama-3.3-70b-versatile",
        "messages":    [{"role": "user", "content": prompt}],
        "max_tokens":  80,
        "temperature": 0,
    }
    tpm_budget_left = GROQ_TPM_RETRY_BUDGET
    other_errors    = 0
    attempt         = 0

    while True:
        attempt += 1
        try:
            print(f"      ðŸ¦™ Groq attempt {attempt} "
                  f"[TPM budget remaining: {tpm_budget_left}]...")
            headers = {
                "Authorization": f"Bearer {GROQ_API_KEY}",
                "Content-Type":  "application/json",
            }
            r = requests.post(GROQ_URL, headers=headers,
                              json=payload, timeout=30)

            if r.status_code == 429:
                body     = {}
                err_text = ""
                try:
                    body     = r.json()
                    err_text = str(body.get("error", {}).get("message", ""))
                except Exception:
                    err_text = r.text[:300]

                if _is_groq_hard_daily(err_text):
                    print("      âŒ Groq RPD (daily) exhausted â†’ bypass alert.")
                    return "BOTH_EXHAUSTED"

                if tpm_budget_left <= 0:
                    print(f"      âŒ Groq TPM retry budget exhausted "
                          f"({GROQ_TPM_RETRY_BUDGET} waits done) â†’ bypass alert.")
                    return "BOTH_EXHAUSTED"

                wait = _parse_groq_wait(r.headers, err_text)
                tpm_budget_left -= 1
                print(f"      â³ Groq TPM/RPM limit. Waiting {wait}s "
                      f"[budget left after this: {tpm_budget_left}]...")
                time.sleep(wait)
                continue

            r.raise_for_status()
            other_errors = 0
            result = r.json()["choices"][0]["message"]["content"].strip().replace("**", "")
            print(f"      ðŸ’¬ Groq: {result[:100]}")
            return result

        except requests.exceptions.HTTPError as e:
            print(f"      âš ï¸ Groq HTTP error (attempt {attempt}): {e}")
            other_errors += 1
        except Exception as e:
            print(f"      âš ï¸ Groq error (attempt {attempt}): {e}")
            other_errors += 1

        if other_errors >= MAX_OTHER_ERRORS:
            print(f"      âŒ Groq {MAX_OTHER_ERRORS} consecutive "
                  f"non-rate errors â†’ bypass alert.")
            return "BOTH_EXHAUSTED"
        print(f"      â³ Waiting 12s before next Groq attempt...")
        time.sleep(12)


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# TELEGRAM
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
def _escape_html(text):
    """Escape special HTML characters so Telegram never rejects the message."""
    return (str(text)
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;"))

def clean_plain_text(text):
    """Normalize Unicode punctuation to simple plain-text ASCII for Telegram."""
    return (str(text)
            .replace("â€”", "-")
            .replace("â€“", "-")
            .replace("â€™", "'")
            .replace("â€˜", "'")
            .replace('"', '"')
            .replace('"', '"')
            .strip())

def send_telegram_message(message):
    """
    Sends plain text â€” immune to all special characters in news headlines.
    Markdown breaks on any unescaped _ * [ ] in news headlines.
    HTML only breaks on unescaped & < > which we escape above.
    """
    if not TELEGRAM_TOKEN or not CHAT_ID:
        print("      âš ï¸ Telegram credentials missing.")
        return
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": CHAT_ID, "text": message},
            timeout=10)
        if r.status_code != 200:
            print(f"      âš ï¸ Telegram error {r.status_code}: {r.text[:100]}")
        else:
            print("      âœ… Telegram message sent.")
    except Exception as e:
        print(f"      âš ï¸ Telegram failed: {e}")


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# MAIN LOOP
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
def check_water_cuts():
    print(f"\n{'â”'*60}")
    print(f"  ðŸ” Water Cut Scanner â€” {get_ist_time().strftime('%d %b %Y, %H:%M IST')}")
    print(f"  Area: {MY_AREA_NAME}")
    print(f"  Gemini RPM retry budget : {GEMINI_RPM_RETRY_BUDGET} waits/article")
    print(f"  Groq   TPM retry budget : {GROQ_TPM_RETRY_BUDGET} waits/article")
    print(f"{'â”'*60}\n")

    seen_titles      = set()
    total            = 0
    passed_f1        = 0
    passed_f2        = 0
    gemini_calls     = 0
    groq_calls       = 0
    alerts_sent      = 0
    gemini_exhausted = False
    groq_exhausted   = False

    for rss_url in RSS_URLS:
        feed = feedparser.parse(rss_url)
        print(f"ðŸ“¡ Feed returned {len(feed.entries)} entries.\n")

        for entry in feed.entries:
            title = getattr(entry, 'title', '').strip()
            link  = getattr(entry, 'link',  '').strip()
            if not title or not link:
                continue

            norm = re.sub(r'\s+', ' ', title.lower())
            if norm in seen_titles:
                continue
            seen_titles.add(norm)

            if not is_published_recently(entry):
                continue
            if 'water' not in title.lower():
                continue

            total += 1
            print(f"ðŸ‘‰ [{total}] {title[:85]}")

            # â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
            # FILTER 1 â€” HEADLINE ONLY
            # â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
            is_partial = matches_any(HEADLINE_PARTIAL_REJECT, title)
            is_full    = matches_any(HEADLINE_FULL_CUT,       title)

            if is_partial and not is_full:
                print("   â© [F1] Partial/% cut. Skipped.\n")
                continue
            if not is_full:
                print("   â© [F1] No full-cut signal. Skipped.\n")
                continue

            print("   âœ… [F1] Full cut confirmed. Scraping article...")
            passed_f1 += 1

            real_url     = decode_google_news_url(link)
            article_text = get_article_text(real_url)
            combined     = f"{title} {article_text}"

            # â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
            # FILTER 2 â€” BODY: area keyword + full-cut phrase
            # â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
            area_found = matches_any(AREA_PATTERNS, combined)
            body_full  = matches_any(BODY_FULL_CUT,  combined)

            if not area_found:
                print("   â© [F2] No area keyword found. Skipped.\n")
                continue
            if not body_full:
                print("   â© [F2] Area found but no full-cut phrase. Skipped.\n")
                continue

            matched_kw = next(
                (p for p in AREA_PATTERNS if re.search(p, combined, re.I)), "?")
            print(f"   ðŸŽ¯ [F2] PASSED! Area='{matched_kw}' + cut phrase confirmed.")
            passed_f2 += 1

            # â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
            # AI CASCADE
            # Golden rule: only an explicit "NO" discards an F1+F2 article.
            # â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
            decision = None
            ai_used  = ""

            # â”€â”€ Step 1: Gemini â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
            if not gemini_exhausted:
                gemini_calls += 1
                raw = ask_gemini(title, article_text)

                if raw == "DAILY_EXHAUSTED":
                    gemini_exhausted = True

                elif raw == "USE_GROQ":
                    pass

                else:
                    decision = raw
                    ai_used  = "Gemini 2.0 Flash"
            else:
                print("      â­ï¸  Gemini skipped (RPD exhausted this run) â†’ Groq.")

            # â”€â”€ Step 2: Groq (only if Gemini gave no answer) â”€â”€â”€â”€â”€â”€â”€â”€â”€
            if decision is None and not groq_exhausted:
                groq_calls += 1
                raw = ask_groq(title, article_text)

                if raw == "BOTH_EXHAUSTED":
                    groq_exhausted = True
                else:
                    decision = raw
                    ai_used  = "Groq LLaMA 3.3 70B"

            # â”€â”€ Step 3: Both dead â†’ bypass, send for manual review â”€â”€â”€
            if decision is None:
                msg = (
                    f"[WATER CUT ALERT - REVIEW NEEDED]\n"
                    f"Area: F-North / Sion / Matunga / Wadala / CGS\n\n"
                    f"AI quota exhausted - please verify manually.\n"
                    f"Passed headline and area keyword filters.\n\n"
                    f"Headline: {clean_plain_text(title)}\n"
                    f"Link: {real_url}"
                )
                print("      ðŸš¨ Both AIs exhausted. Sending bypass review alert...")
                send_telegram_message(msg)
                alerts_sent += 1
                print()
                continue

            # â”€â”€ Explicit AI verdict + Python expiry kill-switch â”€â”€â”€â”€â”€â”€â”€â”€â”€
            # AI outputs: CONFIRMED | location | YYYY-MM-DD HH:MM (or UNKNOWN)
            #          or NOT_CONFIRMED | reason
            if decision.upper().startswith("CONFIRMED"):
                parts    = decision.split("|")
                location = parts[1].strip() if len(parts) > 1 else "F-North area"
                ai_time  = parts[2].strip() if len(parts) > 2 else "UNKNOWN"

                # â”€â”€ Python expiry kill-switch â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
                # AI translates the date â€” Python decides if it is past or future.
                # This prevents hallucinated "Ongoing" for already-ended cuts.
                time_valid = True
                if ai_time and ai_time.upper() != "UNKNOWN":
                    try:
                        ist_tz     = pytz.timezone("Asia/Kolkata")
                        parsed_end = ist_tz.localize(datetime.strptime(ai_time, "%Y-%m-%d %H:%M"))
                        if parsed_end < get_ist_time():
                            print(f"      â© Python kill-switch: cut ended {ai_time} IST (past). Skipped.")
                            time_valid = False
                    except ValueError:
                        print(f"      âš ï¸ Could not parse AI time '{ai_time}'. Proceeding to avoid missing alert.")

                if not time_valid:
                    print()
                    continue

                msg = (
                    f"[WATER CUT ALERT - CONFIRMED]\n"
                    f"Area: F-North / Sion / Matunga / Wadala / CGS\n\n"
                    f"Location: {clean_plain_text(location)}\n"
                    f"Cut ends: {clean_plain_text(ai_time)}\n\n"
                    f"Headline: {clean_plain_text(title)}\n"
                    f"Link: {real_url}\n"
                    f"Verified by: {clean_plain_text(ai_used)} + Python"
                )
                print(f"      ðŸš¨ {ai_used} confirmed + Python approved! Sending alert...")
                send_telegram_message(msg)
                alerts_sent += 1
            else:
                print(f"      âœ… {ai_used} -> NOT_CONFIRMED (not affecting F-North). Skipped.")

            print()

    # â”€â”€ Run summary â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    print(f"\n{'â”'*60}")
    print(f"  SCAN COMPLETE  [{get_ist_time().strftime('%H:%M IST')}]")
    print(f"  Total articles scanned        : {total}")
    print(f"  Passed Filter 1 (headline)    : {passed_f1}")
    print(f"  Passed Filter 2 (body + area) : {passed_f2}")
    print(f"  Gemini API calls made         : {gemini_calls}")
    print(f"  Groq API calls made           : {groq_calls}")
    print(f"  Alerts sent                   : {alerts_sent}")
    if gemini_exhausted:
        print("  âš ï¸  Gemini RPD exhausted this run â€” Groq was used as fallback.")
    if groq_exhausted:
        print("  âš ï¸  Groq RPD exhausted this run â€” bypass alerts were sent.")
    if alerts_sent == 0:
        print("  âœ… No water cuts detected for F-North today.")
    print(f"{'â”'*60}\n")


if __name__ == "__main__":
    check_water_cuts()
