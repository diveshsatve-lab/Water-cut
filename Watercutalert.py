import feedparser
import requests
import google.generativeai as genai
import os
from datetime import datetime
import pytz
import time

# --- Configuration ---
# ⚠️ SECRETS ARE LOADED FROM ENVIRONMENT VARIABLES. DO NOT HARDCODE THEM.
TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN')
CHAT_ID = os.environ.get('CHAT_ID')
GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY')

# Specific Area Name for Logging
MY_AREA_NAME = "F-North Ward / Sion / Matunga"

# --- RSS Feeds ---
RSS_URLS = [
    "https://news.google.com/rss/search?q=Mumbai+water+cut+when:1d&hl=en-IN&gl=IN&ceid=IN:en",
    "https://news.google.com/rss/search?q=BMC+water+supply+when:1d&hl=en-IN&gl=IN&ceid=IN:en"
]

# --- Setup Gemini AI ---
model = None

if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)
    
    # --- UPDATED MODEL LOGIC ---
    # Using Flash-Lite for much higher daily limits (e.g., 1,500/day)
    try:
        model = genai.GenerativeModel('gemini-2.5-flash-lite')
        print("   ✅ Connected to Gemini 2.5 Flash-Lite")
    except Exception as e:
        print(f"   ⚠️ Flash-Lite failed ({e}), falling back to 2.5 Flash...")
        model = genai.GenerativeModel('gemini-2.5-flash')

def get_ist_time():
    """Returns current time in Indian Standard Time."""
    utc_now = datetime.now(pytz.utc)
    ist_tz = pytz.timezone('Asia/Kolkata')
    return utc_now.astimezone(ist_tz)

def is_published_today(entry_published_struct):
    """Checks if the RSS entry was published today (IST)."""
    if not entry_published_struct:
        return False
    
    today_ist = get_ist_time().date()
    pub_date = datetime(*entry_published_struct[:6])
    return pub_date.day == today_ist.day and pub_date.month == today_ist.month

def ask_gemini(headline, link):
    if not model:
        return "NO"

    current_date = get_ist_time().strftime("%Y-%m-%d")
    
    # --- UPDATED PROMPT: STRICTER LOGIC ---
    prompt = f"""
    Current Date: {current_date}
    Headline: "{headline}"
    Link: {link}

    Task: Determine if this news indicates a Water Cut specifically for **F-North Ward (Sion/Matunga)**.
    
    Rules:
    1. YES if it explicitly mentions 'F-North', 'Sion', 'Matunga', 'Wadala', or 'CGS Colony'.
    2. YES if it mentions 'F-Ward' generally.
    3. NO if it mentions "Across Mumbai" or "City-wide" WITHOUT naming F-North explicitly (to avoid false positives).
    4. NO if it specifies ONLY 'F-South' or other specific wards excluding F-North.
    5. MARK RELEVANT even if the date is in the future.

    Output format: "YES | [Summary]" or "NO".
    """
    
    try:
        # generate_content is the standard method
        response = model.generate_content(prompt)
        text_response = response.text.strip().replace('**', '') 
        return text_response
    except Exception as e:
        print(f"   ⚠️ Gemini AI Error: {e}")
        return "NO"

def send_telegram_message(message):
    if not TELEGRAM_TOKEN or not CHAT_ID:
        print("   ❌ Telegram Token or Chat ID missing.")
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": message, "parse_mode": "Markdown"}
    try:
        requests.post(url, json=payload)
    except Exception as e:
        print(f"   ❌ Telegram Error: {e}")

def check_water_cuts():
    print(f"🔍 Scanning news for {MY_AREA_NAME}...")
    relevant_news_found = False
    seen_links = set()

    for url in RSS_URLS:
        feed = feedparser.parse(url)
        
        for entry in feed.entries:
            title = entry.title
            link = entry.link
            
            if link in seen_links: continue
            seen_links.add(link)

            if not is_published_today(entry.published_parsed): continue
            if 'water' not in title.lower(): continue

            print(f"   👉 Asking AI: {title[:50]}...")
            decision = ask_gemini(title, link)
            
            if decision.startswith("YES"):
                print("      🚨 MATCH FOUND! Sending alert...")
                try:
                    summary_text = decision.split("|")[1].strip()
                except IndexError:
                    summary_text = "Check link for details."
                
                msg = (f"🚰 *Water Cut Alert*\n"
                       f"📍 Status: *CONFIRMED*\n"
                       f"📝 Note: {summary_text}\n\n"
                       f"📰 *{title}*\n"
                       f"🔗 [Read Article]({link})")
                
                send_telegram_message(msg)
                relevant_news_found = True
            else:
                print(f"      ✅ AI said NO.")
            
            # --- CRITICAL FIX FOR RATE LIMIT ---
            # Sleep 12 seconds to stay under the 5 requests/minute limit
            print("      💤 Sleeping 12s to respect API quota...")
            time.sleep(12) 

    if not relevant_news_found:
        print("   ✅ No detection today.")

if __name__ == "__main__":
    check_water_cuts()
