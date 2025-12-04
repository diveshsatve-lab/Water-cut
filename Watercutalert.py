import feedparser
import requests
import google.generativeai as genai
import os
from datetime import datetime
import pytz
import time

# --- Configuration ---
TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN')
CHAT_ID = os.environ.get('CHAT_ID')
GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY')

# Specific Area Name for Logging
MY_AREA_NAME = "F-North Ward / Sion / Matunga"

# --- RSS Feeds ---
# Query looks for Mumbai water news from the last 24h
RSS_URLS = [
    "https://news.google.com/rss/search?q=Mumbai+water+cut+when:1d&hl=en-IN&gl=IN&ceid=IN:en",
    "https://news.google.com/rss/search?q=BMC+water+supply+when:1d&hl=en-IN&gl=IN&ceid=IN:en"
]

# --- Setup Gemini AI ---
# Switched to 1.5-flash as it is the current standard for speed/cost. 
# Change back to 2.5 if you specifically have access to it.
if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)
    model = genai.GenerativeModel('gemini-1.5-flash') 
else:
    model = None

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
    # Convert struct_time to datetime
    pub_date = datetime(*entry_published_struct[:6])
    
    # Simple check: Is the pub date's day same as today's day?
    return pub_date.day == today_ist.day and pub_date.month == today_ist.month

def ask_gemini(headline, link):
    """
    Sends news to Gemini to check:
    1. Is it F-North/Sion/Matunga (or vague F-Ward)? -> YES
    2. Is it F-South ONLY? -> NO
    3. Is it happening Today OR Future Date? -> YES
    """
    if not model:
        return "NO"

    current_date = get_ist_time().strftime("%Y-%m-%d")
    
    # --- UPDATED PROMPT LOGIC ---
    prompt = f"""
    You are an AI assistant monitoring critical water cut news for Mumbai. 
    Current Date: {current_date}

    Your goal is to decide if the following news is relevant to a user in **F-North Ward (Sion/Matunga)**.

    ### 1. LOCATION LOGIC
    - **RELEVANT (YES):** 
      - If text mentions **'F-North'**, **'F-North Ward'**, **'Sion'**, **'Matunga'**, **'Wadala'**, or **'CGS Colony'**.
      - If text mentions **'F-Ward'** vaguely (without specifying North or South).
      - If text says "All Wards" or "Across Mumbai".

    - **IRRELEVANT (NO):**
      - If text **ONLY** mentions 'F-South' (and does not mention F-North, Sion, etc).
      - If text is about specific other wards (e.g., "A, B, and E Wards") and excludes F-North.

    ### 2. TIME LOGIC (CRITICAL)
    - If the water cut is scheduled for **TODAY**, **TOMORROW**, or a **FUTURE DATE**, you **MUST** mark it RELEVANT.
    - Do not ignore news just because the date is not today.

    ### 3. OUTPUT FORMAT
    - If RELEVANT: Reply strictly with: "YES | [Short 1-sentence summary of Area & Date]"
    - If IRRELEVANT: Reply strictly with: "NO"

    ### News to Analyze:
    Headline: "{headline}"
    Link: {link}
    """
    
    try:
        response = model.generate_content(prompt)
        text_response = response.text.strip()
        # Clean up formatting in case AI adds bolding
        return text_response.replace('**', '') 
    except Exception as e:
        print(f"   ⚠️ Gemini Error: {e}")
        return "NO"

def send_telegram_message(message):
    if not TELEGRAM_TOKEN or not CHAT_ID:
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
            
            if link in seen_links:
                continue
            seen_links.add(link)

            # --- STEP 1: Date Filter (Python Side) ---
            # We strictly only want news PUBLISHED today.
            # But the CONTENT can be about a future date.
            if not is_published_today(entry.published_parsed):
                # print(f"   ⏭️ Skipping old news: {title[:30]}...") 
                continue

            # --- STEP 2: Keyword Pre-Check ---
            if 'water' not in title.lower():
                continue

            # --- STEP 3: Ask Gemini (The Brain) ---
            print(f"   👉 Asking AI: {title[:50]}...")
            
            # Pass title + link to AI
            decision = ask_gemini(title, link)
            
            if decision.startswith("YES"):
                print("      🚨 MATCH FOUND! Sending alert...")
                
                # Extract the custom summary from AI response "YES | Summary..."
                try:
                    summary_text = decision.split("|")[1].strip()
                except IndexError:
                    summary_text = "Check link for details."

                date_str = get_ist_time().strftime("%d-%m-%Y")
                
                msg = (f"🚰 *Water Cut Alert*\n"
                       f"📍 Status: *CONFIRMED*\n"
                       f"📝 Note: {summary_text}\n\n"
                       f"📰 *{title}*\n"
                       f"🔗 [Read Article]({link})")
                
                send_telegram_message(msg)
                relevant_news_found = True
            else:
                print(f"      ✅ AI said NO (Irrelevant location or F-South).")
            
            # --- STEP 4: Rate Limit Protection ---
            time.sleep(4) # Sleep 4s to be safe

    if not relevant_news_found:
        print("   ✅ No detection today.")

if __name__ == "__main__":
    check_water_cuts()
