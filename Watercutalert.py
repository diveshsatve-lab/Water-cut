import feedparser
import requests
import google.generativeai as genai
import os
from datetime import datetime
import pytz
import time
from bs4 import BeautifulSoup # This is the reading tool

# --- Configuration ---
TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN')
CHAT_ID = os.environ.get('CHAT_ID')
GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY')

MY_AREA_NAME = "F-North Ward / F Ward / Sion / Matunga / Wadala/ CGS"

RSS_URLS = [
    "https://news.google.com/rss/search?q=Mumbai+water+cut+when:1d&hl=en-IN&gl=IN&ceid=IN:en",
    "https://news.google.com/rss/search?q=BMC+water+supply+when:1d&hl=en-IN&gl=IN&ceid=IN:en"
]

# --- Setup Gemini AI ---
model = None
if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)
    try:
        model = genai.GenerativeModel('gemini-2.5-flash-lite')
        print("   ✅ Connected to Gemini 2.5 Flash-Lite")
    except:
        model = genai.GenerativeModel('gemini-2.0-flash')

def get_ist_time():
    utc_now = datetime.now(pytz.utc)
    ist_tz = pytz.timezone('Asia/Kolkata')
    return utc_now.astimezone(ist_tz)

def is_published_today(entry_published_struct):
    if not entry_published_struct: return False
    today_ist = get_ist_time().date()
    pub_date = datetime(*entry_published_struct[:6])
    return pub_date.day == today_ist.day and pub_date.month == today_ist.month

def get_article_text(url):
    """
    Downloads the website and extracts the text.
    """
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'}
    try:
        response = requests.get(url, headers=headers, timeout=10)
        soup = BeautifulSoup(response.content, 'html.parser')
        
        # customized to find text in paragraphs
        paragraphs = soup.find_all('p')
        text_content = ' '.join([p.get_text() for p in paragraphs])
        
        # Limit text to 3000 chars to avoid confusing AI with ads/footer junk
        return text_content[:3000]
    except Exception as e:
        print(f"      ⚠️ Could not scrape text: {e}")
        return "Could not fetch text. Make decision based on Headline only."

def ask_gemini(headline, full_text):
    if not model: return "NO"

    current_date = get_ist_time().strftime("%Y-%m-%d")
    
    # --- PROMPT: READ THE NEWS CONTENT ---
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
        return "NO"

def send_telegram_message(message):
    if not TELEGRAM_TOKEN or not CHAT_ID: return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": message, "parse_mode": "Markdown"}
    requests.post(url, json=payload)

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

            print(f"   👉 Found: {title[:40]}...")
            
            # --- NEW STEP: READ THE WEBSITE ---
            print("      📖 Reading full article...")
            article_text = get_article_text(link)
            
            # --- ASK AI WITH FULL CONTEXT ---
            decision = ask_gemini(title, article_text)
            
            if decision.startswith("YES"):
                print("      🚨 MATCH FOUND! Sending alert...")
                try: summary = decision.split("|")[1].strip()
                except: summary = "Check link."
                
                msg = (f"🚰 *Water Cut Alert*\n📍 *CONFIRMED for F-Ward*\n📝 {summary}\n\n📰 {title}\n🔗 [Read Article]({link})")
                send_telegram_message(msg)
                relevant_news_found = True
            else:
                print(f"      ✅ AI said NO (Not affecting F-North).")
            
            print("      💤 Sleeping 12s...")
            time.sleep(12) 

    if not relevant_news_found:
        print("   ✅ No detection today.")

if __name__ == "__main__":
    check_water_cuts()
