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
AREA_KEYWORDS = ["f-north", "f north", "sion", "matunga", "wadala", "cgs colony", "f-ward", "f ward"]

RSS_URLS = [
    "https://news.google.com/rss/search?q=Mumbai+water+cut+when:1d&hl=en-IN&gl=IN&ceid=IN:en",
    "https://news.google.com/rss/search?q=BMC+water+supply+when:1d&hl=en-IN&gl=IN&ceid=IN:en"
]

gemini_client = None
if GEMINI_API_KEY:
    try:
        gemini_client = genai.Client(api_key=GEMINI_API_KEY)
        print("Connected to Gemini (google-genai SDK)")
    except Exception as e:
        print(f"Could not init Gemini client: {e}")


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
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}
        resp = requests.get(f"https://news.google.com/rss/articles/{gn_art_id}", headers=headers, timeout=10)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        div = soup.select_one("c-wiz > div")
        if not div:
            return google_url
        signature = div.get("data-n-a-sg")
        timestamp = div.get("data-n-a-ts")
        articles_req = ["Fbv4je", f'["garturlreq",[["en-IN","IN",["FINANCE_TOP_INDICES","WEB_TEST_1_0_0"],null,null,1,1,"IN:en",null,180,null,null,null,null,null,0,null,null,[1608992183,723341000]],"en-IN","IN",1,[2,3,4,8],1,0,"655000234",0,0,null,0],"{gn_art_id}",{timestamp},"{signature}"]']
        payload = f"f.req={quote(json.dumps([[articles_req]]))}"
        post_resp = requests.post("https://news.google.com/_/DotsSplashUi/data/batchexecute", headers={"Content-Type": "application/x-www-form-urlencoded;charset=UTF-8"}, data=payload, timeout=10)
        post_resp.raise_for_status()
        parts = post_resp.text.split("\n\n")
        if len(parts) < 2:
            return google_url
        return json.loads(json.loads(parts[1])[0][2])[1]
    except Exception as e:
        print(f"      âš ï¸ URL decode failed: {e}")
        return google_url


def get_article_text(url):
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'}
    try:
        response = requests.get(url, headers=headers, timeout=10, allow_redirects=True)
        soup = BeautifulSoup(response.content, 'html.parser')
        paragraphs = soup.find_all('p')
        text_content = ' '.join([p.get_text(' ', strip=True) for p in paragraphs])
        return text_content[:3000]
    except Exception as e:
        print(f"      âš ï¸ Could not scrape text: {e}")
        return "Could not fetch text. Make decision based on Headline only."


def ask_gemini(headline, full_text, retries=3):
    if not gemini_client:
        return "API_FAILED"

    current_date = get_ist_time().strftime("%Y-%m-%d")
    prompt = f"""
Current Date: {current_date}
HEADLINE: "{headline}"
FULL NEWS ARTICLE TEXT:
"{full_text}"

TASK: Decide if this article is about a FULL water suspension/complete water stoppage that affects F-North Ward (Sion, Matunga, Wadala, CGS Colony).

Rules:
1. Reply NO if it is only a partial cut, percentage cut, low-pressure supply, or any notice where water still comes.
2. Reply NO if it is about another area only (Thane, Mira Bhayandar, Kalyan, etc.).
3. Reply YES only if the article clearly says water is suspended, completely cut, or supply will stop for F-North / Sion / Matunga / Wadala / CGS Colony.
4. If F-North is mentioned only as a passing place name, Reply NO.
5. If you are unsure, Reply NO.

OUTPUT FORMAT:
YES | short summary
or
NO
"""

    for attempt in range(1, retries + 1):
        try:
            response = gemini_client.models.generate_content(model='gemini-2.0-flash', contents=prompt)
            return response.text.strip().replace('**', '')
        except Exception as e:
            err_str = str(e)
            if '429' in err_str or 'quota' in err_str.lower():
                delay_match = re.search(r'retry_delay\s*\{\s*seconds:\s*(\d+)', err_str)
                wait = int(delay_match.group(1)) + 5 if delay_match else (30 * attempt)
                print(f"      â³ Quota hit. Waiting {wait}s... (Attempt {attempt}/{retries})")
                time.sleep(wait)
            else:
                print(f"      âš ï¸ Gemini error: {e}")
                return "API_FAILED"
    return "API_FAILED"


def send_telegram_message(message):
    if not TELEGRAM_TOKEN or not CHAT_ID:
        print("      âš ï¸ Telegram credentials missing â€” skipping send.")
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
    seen_titles = set()

    for rss_url in RSS_URLS:
        feed = feedparser.parse(rss_url)
        for entry in feed.entries:
            title = entry.title
            link = entry.link
            normalized_title = title.strip().lower()
            if normalized_title in seen_titles:
                continue
            seen_titles.add(normalized_title)
            if not is_published_recently(entry.get('published_parsed')):
                continue
            if 'water' not in title.lower():
                continue

            print(f"   ðŸ‘‰ Found: {title[:60]}...")
            real_url = decode_google_news_url(link)
            print("      ðŸ“– Reading full article...")
            article_text = get_article_text(real_url)

            decision = ask_gemini(title, article_text)
            decision_source = "Gemini AI"

            if decision == "API_FAILED":
                print("      âš ï¸ AI unavailable. Using strict regex fallback...")
                combined_text = (title + " " + article_text).lower()
                keyword_found = None
                for kw in AREA_KEYWORDS:
                    if re.search(r'\b' + re.escape(kw) + r'\b', combined_text):
                        keyword_found = kw
                        break
                if keyword_found:
                    summary = f"Keyword '{keyword_found}' detected in article."
                    decision = f"YES | {summary}"
                    decision_source = "Regex Keyword Fallback"
                else:
                    decision = "NO"

            if decision.startswith("YES"):
                try:
                    summary = decision.split("|", 1)[1].strip()
                except Exception:
                    summary = "Check link for details."
                msg = (
                    f"ðŸš° *Water Cut Alert*\n"
                    f"ðŸ“ *CONFIRMED for F-Ward*\n"
                    f"ðŸ“ {summary}\n\n"
                    f"ðŸ“° {title}\n"
                    f"ðŸ”— [Read Article]({real_url})\n"
                    f"_Source: {decision_source}_"
                )
                send_telegram_message(msg)
                relevant_news_found = True
            else:
                print("      âœ… Safe (Not affecting F-North).")

            print("      ðŸ’¤ Sleeping 15s...")
            time.sleep(15)

    if not relevant_news_found:
        print("   âœ… No detection today.")


if __name__ == "__main__":
    check_water_cuts()
