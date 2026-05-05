import feedparser
import requests
import google.generativeai as genai
import os
import json
from urllib.parse import quote, urlparse
from datetime import datetime
import pytz
import time
from bs4 import BeautifulSoup

# --- Configuration ---
TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN')
CHAT_ID = os.environ.get('CHAT_ID')
GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY')

MY_AREA_NAME = "F-North Ward / F Ward / Sion / Matunga / Wadala / CGS"

RSS_URLS = [
    "https://news.google.com/rss/search?q=Mumbai+water+cut+when:1d&hl=en-IN&gl=IN&ceid=IN:en",
    "https://news.google.com/rss/search?q=BMC+water+supply+when:1d&hl=en-IN&gl=IN&ceid=IN:en"
]

# --- Setup Gemini AI ---
# FIX #3: Use a verified model name. 'gemini-2.5-flash-lite' is not valid.
model = None
if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)
    try:
        model = genai.GenerativeModel('gemini-2.0-flash-lite')
        print("   âœ… Connected to Gemini 2.0 Flash-Lite")
    except Exception as e:
        print(f"   âš ï¸ Could not load Gemini model: {e}")
        model = None


def get_ist_time():
    utc_now = datetime.now(pytz.utc)
    ist_tz = pytz.timezone('Asia/Kolkata')
    return utc_now.astimezone(ist_tz)


# FIX #2: Convert published_parsed (UTC) to IST before comparing dates.
# Old code used a naive datetime, causing articles published after 6:30 PM IST
# to be incorrectly skipped (UTC day was already tomorrow).
def is_published_today(entry_published_struct):
    if not entry_published_struct:
        return False
    today_ist = get_ist_time().date()
    pub_utc = datetime(*entry_published_struct[:6], tzinfo=pytz.utc)
    pub_ist = pub_utc.astimezone(pytz.timezone('Asia/Kolkata'))
    return pub_ist.date() == today_ist


# FIX #1: Decode the Google News encoded URL to get the real article URL.
# Google News RSS feeds return encoded intermediate URLs (CBMi...) since mid-2024.
# Scraping those returns Google's own page, not the actual article.
# This function uses Google's internal batchexecute API to resolve the real URL.
def decode_google_news_url(google_url):
    """
    Resolves a Google News encoded RSS link to the actual article URL.
    Uses Google's internal batchexecute endpoint (reverse-engineered).
    Falls back to the original URL on any failure.
    """
    try:
        gn_art_id = urlparse(google_url).path.split("/")[-1]
        if not gn_art_id.startswith("CB"):
            return google_url  # Not an encoded URL, return as-is

        # Step 1: Fetch the signature and timestamp from the article page
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                          "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }
        resp = requests.get(
            f"https://news.google.com/rss/articles/{gn_art_id}",
            headers=headers,
            timeout=10
        )
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        div = soup.select_one("c-wiz > div")
        if not div:
            print("      âš ï¸ Could not find decode params, using original URL.")
            return google_url

        signature = div.get("data-n-a-sg")
        timestamp = div.get("data-n-a-ts")

        # Step 2: Call batchexecute to decode the URL
        articles_req = [
            "Fbv4je",
            f'["garturlreq",[["en-IN","IN",["FINANCE_TOP_INDICES","WEB_TEST_1_0_0"],'
            f'null,null,1,1,"IN:en",null,180,null,null,null,null,null,0,null,null,'
            f'[1608992183,723341000]],"en-IN","IN",1,[2,3,4,8],1,0,"655000234",0,0,'
            f'null,0],"{gn_art_id}",{timestamp},"{signature}"]',
        ]
        payload = f"f.req={quote(json.dumps([[articles_req]]))}"
        post_headers = {"Content-Type": "application/x-www-form-urlencoded;charset=UTF-8"}
        post_resp = requests.post(
            "https://news.google.com/_/DotsSplashUi/data/batchexecute",
            headers=post_headers,
            data=payload,
            timeout=10
        )
        post_resp.raise_for_status()

        # Parse the response to extract the decoded URL
        parts = post_resp.text.split("\n\n")
        if len(parts) < 2:
            return google_url
        decoded_data = json.loads(parts[1])
        real_url = json.loads(decoded_data[0][2])[1]
        print(f"      ðŸ”— Decoded URL: {real_url[:60]}...")
        return real_url

    except Exception as e:
        print(f"      âš ï¸ URL decode failed ({e}), using original URL.")
        return google_url


def get_article_text(url):
    """Downloads the webpage and extracts paragraph text."""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    try:
        response = requests.get(url, headers=headers, timeout=10, allow_redirects=True)
        soup = BeautifulSoup(response.content, 'html.parser')
        paragraphs = soup.find_all('p')
        text_content = ' '.join([p.get_text() for p in paragraphs])
        return text_content[:3000]
    except Exception as e:
        print(f"      âš ï¸ Could not scrape text: {e}")
        return "Could not fetch text. Make decision based on Headline only."


def ask_gemini(headline, full_text):
    if not model:
        return "NO"

    current_date = get_ist_time().strftime("%Y-%m-%d")

    prompt = f"""
    Current Date: {current_date}

    HEADLINE: "{headline}"

    FULL NEWS ARTICLE TEXT:
    "{full_text}"

    ---------------------------------------------------
    TASK: Read the article text above carefully. Does this water cut affect "F-North Ward" (Sion, Matunga, Wadala, CGS Colony)?

    LOGIC:
    1. If the article lists specific wards (e.g., "K-East", "H-West") and DOES NOT mention F-North/Sion/Matunga -> Reply NO.
    2. If the article says "Whole Mumbai" or "All Wards" -> Reply NO (Assume false alarm unless F-North is explicitly named).
    3. ONLY Reply YES if you see the words: "F-North", "Sion", "Matunga", "Wadala", "CGS Colony", or "F-Ward".

    OUTPUT: "YES | [Short Summary]" or "NO".
    """

    try:
        response = model.generate_content(prompt)
        return response.text.strip().replace('**', '')
    except Exception as e:
        print(f"      âš ï¸ Gemini error: {e}")
        return "NO"


def send_telegram_message(message):
    if not TELEGRAM_TOKEN or not CHAT_ID:
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": message, "parse_mode": "Markdown"}
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        print(f"      âš ï¸ Telegram send failed: {e}")


def check_water_cuts():
    print(f"ðŸ” Scanning news for {MY_AREA_NAME}...")
    relevant_news_found = False

    # FIX #4: Deduplicate by title (not link) to avoid processing the same article
    # from multiple RSS feeds. Google News encodes the same article with different
    # CBMi... hashes per feed, so link-based dedup misses cross-feed duplicates.
    seen_titles = set()

    for rss_url in RSS_URLS:
        feed = feedparser.parse(rss_url)
        for entry in feed.entries:
            title = entry.title
            link = entry.link

            # FIX #4: Deduplicate on normalized title
            normalized_title = title.strip().lower()
            if normalized_title in seen_titles:
                continue
            seen_titles.add(normalized_title)

            # FIX #2: Now correctly compares IST dates
            if not is_published_today(entry.get('published_parsed')):
                continue
            if 'water' not in title.lower():
                continue

            print(f"   ðŸ‘‰ Found: {title[:60]}...")

            # FIX #1: Decode the Google News URL before scraping
            print("      ðŸ”— Decoding Google News URL...")
            real_url = decode_google_news_url(link)

            print("      ðŸ“– Reading full article...")
            article_text = get_article_text(real_url)

            decision = ask_gemini(title, article_text)

            if decision.startswith("YES"):
                print("      ðŸš¨ MATCH FOUND! Sending alert...")
                try:
                    summary = decision.split("|")[1].strip()
                except IndexError:
                    summary = "Check link for details."

                msg = (
                    f"ðŸš° *Water Cut Alert*\n"
                    f"ðŸ“ *CONFIRMED for F-Ward*\n"
                    f"ðŸ“ {summary}\n\n"
                    f"ðŸ“° {title}\n"
                    f"ðŸ”— [Read Article]({real_url})"
                )
                send_telegram_message(msg)
                relevant_news_found = True
            else:
                print(f"      âœ… AI said NO (Not affecting F-North).")

            print("      ðŸ’¤ Sleeping 12s...")
            time.sleep(12)

    if not relevant_news_found:
        print("   âœ… No water cut detection today.")


if __name__ == "__main__":
    check_water_cuts()
