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

# --- 1. NEW SDK IMPORT ---
from google import genai

# --- Configuration ---
TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN')
CHAT_ID = os.environ.get('CHAT_ID')
GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY')

MY_AREA_NAME = "F-North Ward / F Ward / Sion / Matunga / Wadala/ CGS"

# --- 4. KEYWORD PRE-FILTER LIST ---
AREA_KEYWORDS = ["f-north", "f north", "sion", "matunga", "wadala", "cgs colony", "f-ward", "f ward"]

RSS_URLS = [
    "https://news.google.com/rss/search?q=Mumbai+water+cut+when:1d&hl=en-IN&gl=IN&ceid=IN:en",
    "https://news.google.com/rss/search?q=BMC+water+supply+when:1d&hl=en-IN&gl=IN&ceid=IN:en"
]

# --- Setup Gemini AI (New SDK) ---
client = None
if GEMINI_API_KEY:
    try:
        client = genai.Client(api_key=GEMINI_API_KEY)
        print("   ✅ Connected to Gemini 2.0 Flash (google-genai SDK)")
    except Exception as e:
        print(f"   ⚠️ Could not init Gemini: {e}")

def get_ist_time():
    utc_now = datetime.now(pytz.utc)
    ist_tz = pytz.timezone('Asia/Kolkata')
    return utc_now.astimezone(ist_tz)

def is_published_today(entry_published_struct):
    if not entry_published_struct: return False
    today_ist = get_ist_time().date()
    pub_utc = datetime(*entry_published_struct[:6], tzinfo=pytz.utc)
    pub_ist = pub_utc.astimezone(pytz.timezone('Asia/Kolkata'))
    return pub_ist.date() == today_ist

def decode_google_news_url(google_url):
    try:
        gn_art_id = urlparse(google_url).path.split("/")[-1]
        if not gn_art_id.startswith("CB"): return google_url
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}
        resp = requests.get(f"https://news.google.com/rss/articles/{gn_art_id}", headers=headers, timeout=10)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        div = soup.select_one("c-wiz > div")
        if not div: return google_url
        signature = div.get("data-n-a-sg")
        timestamp = div.get("data-n-a-ts")
        articles_req = ["Fbv4je", f'["garturlreq",[["en-IN","IN",["FINANCE_TOP_INDICES","WEB_TEST_1_0_0"],null,null,1,1,"IN:en",null,180,null,null,null,null,null,0,null,null,[1608992183,723341000]],"en-IN","IN",1,[2,3,4,8],1,0,"655000234",0,0,null,0],"{gn_art_id}",{timestamp},"{signature}"]']
        payload = f"f.req={quote(json.dumps([[articles_req]]))}"
        post_resp = requests.post("https://news.google.com/_/DotsSplashUi/data/batchexecute", headers={"Content-Type": "application/x-www-form-urlencoded;charset=UTF-8"}, data=payload, timeout=10)
        post_resp.raise_for_status()
        parts = post_resp.text.split("\n\n")
        if len(parts) < 2: return google_url
        return json.loads(json.loads(parts[1])[0][2])[1]
    except:
        return google_url

def get_article_text(url):
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'}
    try:
        response = requests.get(url, headers=headers, timeout=10, allow_redirects=True)
        soup = BeautifulSoup(response.content, 'html.parser')
        paragraphs = soup.find_all('p')
        text_content = ' '.join([p.get_text() for p in paragraphs])
        return text_content[:3000]
    except Exception as e:
        print(f"      ⚠️ Could not scrape text: {e}")
        return "Could not fetch text. Make decision based on Headline only."

def ask_gemini(headline, full_text, retries=3):
    if not client: return "NO"
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
    
    for attempt in range(1, retries + 1):
        try:
            # --- 2. SWITCHED TO GEMINI-2.0-FLASH ---
            response = client.models.generate_content(
                model='gemini-2.0-flash',
                contents=prompt
            )
            return response.text.strip().replace('**', '') 
        except Exception as e:
            err_str = str(e)
            if "429" in err_str or "quota" in err_str.lower():
                # --- 3. RETRY WITH BACKOFF USING GOOGLE'S DELAY HINT ---
                delay_match = re.search(r'retry_delay\s*\{\s*seconds:\s*(\d+)', err_str)
                wait = int(delay_match.group(1)) + 5 if delay_match else (30 * attempt)
                print(f"      ⏳ Quota hit. Waiting {wait}s... (Attempt {attempt}/{retries})")
                time.sleep(wait)
            else:
                print(f"      ⚠️ Gemini error: {e}")
                return "NO"
    return "NO"

def send_telegram_message(message):
    if not TELEGRAM_TOKEN or not CHAT_ID: return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": message, "parse_mode": "Markdown"}
    requests.post(url, json=payload)

def check_water_cuts():
    print(f"🔍 Scanning news for {MY_AREA_NAME}...")
    relevant_news_found = False
    seen_titles = set()

    for url in RSS_URLS:
        feed = feedparser.parse(url)
        for entry in feed.entries:
            title = entry.title
            link = entry.link
            
            normalized_title = title.strip().lower()
            if normalized_title in seen_titles: continue
            seen_titles.add(normalized_title)

            if not is_published_today(entry.get('published_parsed')): continue
            if 'water' not in title.lower(): continue

            print(f"   👉 Found: {title[:60]}...")
            real_url = decode_google_news_url(link)
            print("      📖 Reading full article...")
            article_text = get_article_text(real_url)
            
            # --- 4. KEYWORD PRE-FILTER ---
            combined_text = (title + " " + article_text).lower()
            keyword_found = next((kw for kw in AREA_KEYWORDS if kw in combined_text), None)
            
            if keyword_found:
                print(f"      🚨 KEYWORD MATCH ('{keyword_found}')! Skipping AI, sending alert directly...")
                summary = f"Keyword '{keyword_found}' detected in article."
                decision_source = "Keyword Filter"
                decision = f"YES | {summary}"
            else:
                # If no keyword is found, ask Gemini to be sure
                decision = ask_gemini(title, article_text)
                decision_source = "Gemini AI"
            
            if decision.startswith("YES"):
                if decision_source == "Gemini AI":
                    print("      🚨 AI MATCH FOUND! Sending alert...")
                
                try: summary = decision.split("|")[1].strip()
                except: summary = "Check link for details."
                
                msg = (f"🚰 *Water Cut Alert*\n📍 *CONFIRMED for F-Ward*\n📝 {summary}\n\n📰 {title}\n🔗 [Read Article]({real_url})\n_Source: {decision_source}_")
                send_telegram_message(msg)
                relevant_news_found = True
            else:
                print(f"      ✅ AI said NO (Not affecting F-North).")
            
            print("      💤 Sleeping 15s...")
            time.sleep(15) 

    if not relevant_news_found:
        print("   ✅ No detection today.")

if __name__ == "__main__":
    check_water_cuts()
