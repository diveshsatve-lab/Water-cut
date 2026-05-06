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
from google import genai

TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN')
CHAT_ID = os.environ.get('CHAT_ID')
GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY')

MY_AREA_NAME = "F-North Ward / F Ward / Sion / Matunga / Wadala / CGS"

RSS_URLS = [
    "https://news.google.com/rss/search?q=Mumbai+water+cut+when:1d&hl=en-IN&gl=IN&ceid=IN:en",
    "https://news.google.com/rss/search?q=BMC+water+supply+when:1d&hl=en-IN&gl=IN&ceid=IN:en"
]

AREA_PATTERNS = [
    r"\bf\s*[- ]?north\b",
    r"\bf\s*[- ]?ward\b",
    r"\bsion\b",
    r"\bmatunga\b",
    r"\bwadala\b",
    r"\bcgs\s+colony\b",
]

FULL_CUT_PATTERNS = [
    r"\bwater\s+(?:supply\s+)?(?:will\s+remain\s+)?suspended\b",
    r"\bwater\s+(?:supply\s+)?(?:will\s+be\s+)?stopped\b",
    r"\bwater\s+(?:supply\s+)?(?:will\s+be\s+)?shut(?:\s+down)?\b",
    r"\bwater\s+shutdown\b",
    r"\bsupply\s+suspended\b",
    r"\bsupply\s+stopped\b",
    r"\bno\s+water\s+supply\b",
    r"\bcomplete\s+water\s+cut\b",
    r"\b24-hour\s+water\s+cut\b",
    r"\b30-hour\s+water\s+cut\b",
]

PARTIAL_CUT_PATTERNS = [
    r"\b10\s*%\s+water\s+cut\b",
    r"\b\d+\s*%\s+water\s+cut\b",
    r"\bpartial\s+water\s+cut\b",
    r"\blow\s+pressure\b",
    r"\breduced\s+supply\b",
    r"\bsupply\s+reduction\b",
    r"\bwater\s+cut\s+from\b.*\b%\b",
]

EXCLUDE_AREA_PATTERNS = [
    r"\bthane\b",
    r"\bmira\s*[- ]?bhayandar\b",
    r"\bkalyan\b",
    r"\bdombivli\b",
    r"\bnavi\s+mumbai\b",
    r"\bmbmc\b",
    r"\btmc\b",
]

client = None
if GEMINI_API_KEY:
    try:
        client = genai.Client(api_key=GEMINI_API_KEY)
        print("âœ… Connected to Gemini 2.0 Flash (google-genai SDK)")
    except Exception as e:
        print(f"âš ï¸ Could not init Gemini: {e}")


def get_ist_time():
    return datetime.now(pytz.utc).astimezone(pytz.timezone('Asia/Kolkata'))


def is_published_recently(entry_published_struct, max_hours=24):
    if not entry_published_struct:
        return False
    pub_utc = datetime(*entry_published_struct[:6], tzinfo=pytz.utc)
    age_hours = (datetime.now(pytz.utc) - pub_utc).total_seconds() / 3600
    return age_hours <= max_hours


def decode_google_news_url(google_url):
    try:
        gn_art_id = urlparse(google_url).path.split("/")[-1]
        if not gn_art_id.startswith("CB"):
            return google_url
        headers = {"User-Agent": "Mozilla/5.0"}
        resp = requests.get(f"https://news.google.com/rss/articles/{gn_art_id}", headers=headers, timeout=10)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        div = soup.select_one("c-wiz > div")
        if not div:
            return google_url
        signature = div.get("data-n-a-sg")
        timestamp = div.get("data-n-a-ts")
        articles_req = [
            "Fbv4je",
            f'["garturlreq",[["en-IN","IN",["FINANCE_TOP_INDICES","WEB_TEST_1_0_0"],null,null,1,1,"IN:en",null,180,null,null,null,null,null,0,null,null,[1608992183,723341000]],"en-IN","IN",1,[2,3,4,8],1,0,"655000234",0,0,null,0],"{gn_art_id}",{timestamp},"{signature}"]'
        ]
        payload = f"f.req={quote(json.dumps([[articles_req]]))}"
        post_resp = requests.post(
            "https://news.google.com/_/DotsSplashUi/data/batchexecute",
            headers={"Content-Type": "application/x-www-form-urlencoded;charset=UTF-8"},
            data=payload,
            timeout=10,
        )
        post_resp.raise_for_status()
        parts = post_resp.text.split("\n\n")
        if len(parts) < 2:
            return google_url
        return json.loads(json.loads(parts[1])[0][2])[1]
    except Exception as e:
        print(f"   âš ï¸ URL decode failed: {e}")
        return google_url


def get_article_text(url):
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        response = requests.get(url, headers=headers, timeout=10, allow_redirects=True)
        soup = BeautifulSoup(response.content, 'html.parser')
        paragraphs = soup.find_all('p')
        text_content = ' '.join([p.get_text(' ', strip=True) for p in paragraphs])
        return text_content[:4000]
    except Exception as e:
        print(f"   âš ï¸ Could not scrape text: {e}")
        return ""


def matches_any(patterns, text):
    return any(re.search(p, text, flags=re.I) for p in patterns)


def strong_prefilter(title, article_text):
    combined = f"{title} {article_text}".lower()

    has_area = matches_any(AREA_PATTERNS, combined)
    has_full_cut = matches_any(FULL_CUT_PATTERNS, combined)
    has_partial_cut = matches_any(PARTIAL_CUT_PATTERNS, combined)
    has_excluded_area = matches_any(EXCLUDE_AREA_PATTERNS, combined)

    if not has_area:
        return False, "No target-area keyword found"
    if has_partial_cut and not has_full_cut:
        return False, "Only partial/percentage cut detected"
    if has_excluded_area and not has_area:
        return False, "Other city/authority detected"
    if not has_full_cut:
        return False, "No full suspension/shutdown pattern found"

    return True, "Passed strong pre-filter"


def ask_gemini(headline, full_text):
    if not client:
        return "API_FAILED"

    current_date = get_ist_time().strftime("%Y-%m-%d")
    prompt = f"""
Current Date: {current_date}
HEADLINE: "{headline}"
FULL NEWS ARTICLE TEXT: "{full_text}"

Task: Decide whether this article is about a REAL full water suspension affecting F-North Ward (Sion, Matunga, Wadala, CGS Colony).

Rules:
1. Reply NO for partial cuts, 10% cuts, reduced pressure, reduced supply, or shortage.
2. Reply NO if article is about Thane, Mira-Bhayandar, Kalyan, Dombivli, Navi Mumbai, etc.
3. Reply YES only if the article clearly says water supply is fully suspended/stopped/shut down for F-North / Sion / Matunga / Wadala / CGS Colony.
4. If location words appear only in passing and are not affected areas, reply NO.
5. If unsure, reply NO.

Output only:
YES | short summary
or
NO
"""
    try:
        response = client.models.generate_content(model="gemini-2.0-flash", contents=prompt)
        return (response.text or "NO").strip().replace("**", "")
    except Exception as e:
        print(f"   âš ï¸ Gemini error: {e}")
        return "API_FAILED"


def send_telegram_message(message):
    if not TELEGRAM_TOKEN or not CHAT_ID:
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": message, "parse_mode": "Markdown"}
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        print(f"   âš ï¸ Telegram send failed: {e}")


def check_water_cuts():
    print(f"ðŸ” Scanning news for {MY_AREA_NAME}...")
    relevant_news_found = False
    seen_titles = set()
    total_articles = 0
    passed_prefilter = 0
    gemini_calls = 0

    for rss_url in RSS_URLS:
        feed = feedparser.parse(rss_url)
        for entry in feed.entries:
            title = getattr(entry, 'title', '')
            link = getattr(entry, 'link', '')
            if not title or not link:
                continue

            normalized_title = re.sub(r'\s+', ' ', title.strip().lower())
            if normalized_title in seen_titles:
                continue
            seen_titles.add(normalized_title)

            if not is_published_recently(entry.get('published_parsed')):
                continue
            if 'water' not in title.lower():
                continue

            total_articles += 1
            print(f"ðŸ‘‰ Found: {title[:70]}...")

            real_url = decode_google_news_url(link)
            print("   ðŸ“– Reading full article...")
            article_text = get_article_text(real_url)

            passed, reason = strong_prefilter(title, article_text)
            if not passed:
                print(f"   â© Skipped before Gemini: {reason}")
                continue

            passed_prefilter += 1
            gemini_calls += 1
            print("   ðŸ¤– Passed strong filter. Asking Gemini...")
            decision = ask_gemini(title, article_text)

            if decision.startswith("YES"):
                try:
                    summary = decision.split("|", 1)[1].strip()
                except Exception:
                    summary = "Check article for details."
                msg = (
                    f"ðŸš° *Water Cut Alert*\n"
                    f"ðŸ“ *Possible full water suspension in F-Ward*\n"
                    f"ðŸ“ {summary}\n\n"
                    f"ðŸ“° {title}\n"
                    f"ðŸ”— [Read Article]({real_url})"
                )
                send_telegram_message(msg)
                relevant_news_found = True
                print("   ðŸš¨ Alert sent!")
            elif decision == "API_FAILED":
                print("   âš ï¸ Gemini unavailable. Skipping to avoid false alert.")
            else:
                print("   âœ… Gemini said NO.")

            time.sleep(5)

    print("\n--- Run Summary ---")
    print(f"Articles after basic feed filters: {total_articles}")
    print(f"Articles passed strong pre-filter: {passed_prefilter}")
    print(f"Gemini API calls made: {gemini_calls}")

    if not relevant_news_found:
        print("âœ… No detection today.")


if __name__ == "__main__":
    check_water_cuts()
