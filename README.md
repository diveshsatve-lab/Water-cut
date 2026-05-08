# F-North Ward Water Cut Alert Bot

An automated Python bot that scans Mumbai water cut news every hour via GitHub Actions
and sends a Telegram alert ONLY when a confirmed, active water cut affects
F-North Ward (Sion, Matunga, Wadala, CGS Colony).

---

## How It Works - Full Pipeline

Every article goes through 5 sequential gates. An article must pass ALL gates to
trigger a Telegram alert. Each gate filters out a specific class of false positives.

```
Google News RSS (approx 48 articles per run)
        |
        v
   GATE 0: Pre-Filter              <- Date, dedup, topic check
        |
        v
   GATE 1: Headline Regex (F1)     <- Full shutdown signal in headline
        |
        v
   URL Decode + Article Scrape     <- Get real article body text
        |
        v
   GATE 2: Body Regex (F2)         <- Area keyword + cut phrase in body
        |
        v
   GATE 3: AI Location Check       <- Gemini reads article, Groq as fallback
        |
        v
   GATE 4: Python Expiry Check     <- Is the cut still in the future?
        |
        v
   Telegram Alert Sent
```

---

## Gate 0 - Pre-Filter (Date, Dedup, Topic)

Purpose: Discard articles that are too old, already seen, or unrelated to water supply.


### Step 0a - Title Deduplication

The bot runs two RSS feeds. The same article often appears in both feeds with different
encoded Google News URLs. Without deduplication the same article would be processed twice
and potentially send duplicate Telegram alerts.

How it works: Each article title is normalised (lowercased, whitespace stripped) and
added to a seen_titles Python set. If the normalised title already exists in the set,
the article is skipped immediately.


### Step 0b - Date Filter

Only articles published within the last 24 hours are processed.

The tricky problem it solves - Google Re-crawl:
Google News sometimes re-serves old articles (e.g. from April 30) when they get traffic
again. When this happens Google assigns a fresh timestamp of today in the RSS feed.
A naive date check using only published_parsed would incorrectly pass this old article.

The fix - use the OLDEST date:
The function checks BOTH published_parsed AND updated_parsed timestamps from the RSS entry
and takes the oldest of the two. If the original publish date is 8 days old the article
is blocked even if Google gave it a new updated_parsed of today.

    oldest = min(published_parsed, updated_parsed)
    age_hours = (now_utc - oldest).total_seconds() / 3600
    return age_hours <= 24

If no date is found at all the article is skipped conservatively (returns False) to avoid
processing undated content.


### Step 0c - Basic Topic Check

If the word "water" does not appear anywhere in the headline the article is immediately
dropped without any further processing. This avoids wasting resources on unrelated news
that appears in the RSS feed.

---

## Gate 1 - Headline Regex Filter (F1)

Purpose: Confirm the headline describes a full water shutdown, not a partial percentage
cut, not a pipeline construction update, not an unrelated mention of "water".

This gate uses two pattern lists checked against the headline only.


### Part A - Reject partial cuts (HEADLINE_PARTIAL_REJECT)

If the headline matches a percentage reduction pattern (like "5% cut", "10% reduction")
it is flagged as a partial cut. Partial cuts do not affect individual households the same
way a full shutdown does and generate too many false alerts.

Examples that get rejected:
- "BMC to impose 10% water cut from May 15"
- "15-20% reduction in water supply due to lake levels"


### Part B - Require full-cut signal (HEADLINE_FULL_CUT)

The headline must match at least one full-shutdown pattern:

    Pattern type                    Example headlines that pass
    ----------------------------------------------------------------
    X-hour water cut/shutdown       BMC announces 30-hour water shutdown
    supply suspended/stopped        Water supply suspended in Mumbai
    supply disrupted/snapped        Water supply snapped in several wards
    water cut (not followed by %)   Water cut scheduled for Monday
    no water / zero water supply    No water supply for residents tomorrow
    dry taps / go dry               Mumbai taps to go dry for 24 hours
    BMC shuts/stops/snaps water     BMC shuts water supply for repairs
    face water cut                  Residents to face water cut Tuesday


Decision logic:

    Partial signal only          -> SKIP  ("5% water cut")
    Full signal only             -> PASS
    Both partial AND full        -> PASS  (full overrides: "30-hour 10% cut" is a shutdown)
    Neither                      -> SKIP

If an article fails F1 the bot never scrapes its content, saving time and avoiding
unnecessary hits on news sites.

---

## URL Decode and Article Scrape

Purpose: Get the actual text content of the article for Gate 2 and Gate 3 to analyse.

Why this step is necessary:
Google News RSS feeds do not give you the real article URL. entry.link is an encoded
Google redirect URL like:
    https://news.google.com/rss/articles/CBMiggFodHRw...

If you scrape this URL directly BeautifulSoup reads Google's own redirect page HTML and
not the newspaper article. All ward names and cut details are invisible to the script.

How it works:
The decode_google_news_url() function follows the redirect chain using
requests.get(..., allow_redirects=True) and returns response.url which is the final
real URL (e.g. timesofindia.com/...).

Then get_article_text() fetches that real page, extracts all paragraph tags using
BeautifulSoup, joins them, and returns the first 3000 characters. The 3000-character
limit avoids feeding advertisement text, cookie banners, and footer junk to the AI.

---

## Gate 2 - Body Regex Filter (F2)

Purpose: Confirm that the article body text explicitly mentions our ward AND contains a
full-cut phrase. This prevents articles that mention "Sion pumping station" as
infrastructure context from reaching the AI unnecessarily.

Two conditions must BOTH be true.


### Condition A - Area keyword found (AREA_PATTERNS)

The combined text (headline plus article body) must contain at least one of:
F-North, F North, F/North, F-Ward, F Ward, Sion, Matunga, Wadala, Antop Hill,
CGS, CGS Colony

These are matched as whole words using word boundary anchors to avoid partial matches
(e.g. "fashion" would not match "F").


### Condition B - Full-cut phrase found (BODY_FULL_CUT)

The body text must also contain a full-cut indicator phrase. This includes:
suspended, stopped, halted, shut down, snapped, unavailable, dry taps,
no water supply, BMC shuts water, face water cut, etc.

Why both conditions are required:
Many general Mumbai water cut articles mention "Sion" only as the location of a pumping
station, for example:
    "...a leak at the Sion pumping station caused disruption in south Mumbai..."

The area keyword alone would pass this article. Requiring a cut phrase in the same body
text prevents these infrastructure-reference false positives.

---

## Gate 3 - AI Location Check (Gemini with Groq fallback)

Purpose: Resolve geographic ambiguity that regex cannot handle. Specifically, distinguish
between "water cut in Sion" (our area is affected) vs "pipeline near Sion Panvel Highway"
(Sion is just a road name in the article).

By the time an article reaches Gate 3 Python has already confirmed:
- Published within 24 hours
- Headline has a full-cut signal
- Article body mentions F-North / Sion / Matunga / Wadala
- Article body has a full-cut phrase

The AI has exactly two narrow jobs:

Job 1 - Location check:
    Is F-North / Sion / Matunga / Wadala / CGS explicitly named as an AFFECTED area?
    If NO -> output NOT_CONFIRMED and stop.

Job 2 - Date extraction (only if Job 1 is YES):
    Translate the cut end time to: YYYY-MM-DD HH:MM (IST)
    Example: if article says "tomorrow at 4 PM" and today is 2026-05-09 -> 2026-05-09 16:00
    If no time found -> output UNKNOWN

Output format (exactly one line):
    CONFIRMED | Sion | 2026-05-09 16:00
    NOT_CONFIRMED | Location only mentioned as pumping station context

Why the AI is forbidden from judging whether a cut is current or expired:
LLMs are unreliable at time math. In a real run on May 8 2026, Groq returned "Ongoing"
for a cut that had already ended on May 6. By making the AI only translate dates into a
standard format and letting Python do the comparison, hallucinations are eliminated.


### AI Fallback Chain

    Article needs AI check
            |
            v
       Gemini 2.0 Flash  (primary, faster, free tier)
            |
            +-- Response OK -> use it
            +-- 429 RPM (per-minute limit) -> wait up to 3 times -> try Groq
            +-- 429 RPD (daily limit gone) -> permanently switch to Groq for rest of run
                        |
                        v
               Groq LLaMA 3.3 70B  (fallback, generous free tier)
                        |
                        +-- Response OK -> use it
                        +-- 429 TPM -> wait up to 3 times -> bypass
                        +-- All retries exhausted -> send REVIEW NEEDED manual alert

Retry budgets:
    Gemini: 3 waits per article with exponential backoff
    Groq:   3 waits per article with exponential backoff

Markdown stripping:
Both AI responses have .replace("**", "") applied before processing because both models
sometimes emphasise output as **CONFIRMED** which would break the startswith("CONFIRMED")
check in the parser.

---

## Gate 4 - Python Expiry Kill-Switch

Purpose: Block alerts for water cuts that have already ended, even if the AI said
CONFIRMED. This is the final safety net for late-reporting articles. Newspapers sometimes
publish articles about water cuts that happened days ago.

Real example from the actual run log (May 8 2026):
    Groq: CONFIRMED | Wadala | 2026-05-06 16:00
    Python kill-switch: cut ended 2026-05-06 16:00 IST (past). Skipped.

Without this gate a false Telegram alert would have been sent for a cut that ended 3 days
prior.

How it works:
    parsed_end = IST.localize(datetime.strptime(ai_time, "%Y-%m-%d %H:%M"))
    if parsed_end < get_ist_time():
        SKIP  (cut is in the past)
    else:
        PROCEED to Telegram

Safe failure modes:
- If the AI outputs a time string Python cannot parse -> alert is sent (safer to alert
  than to silently miss a real cut)
- If the AI outputs UNKNOWN (no time in article) -> kill-switch is skipped and alert is
  sent, again erring on the side of alerting

---

## Telegram Alert Format

All alerts are sent as plain text with no HTML, no Markdown formatting, no emoji.
This is intentional. Telegram Markdown parse mode breaks on unescaped underscore,
asterisk, and bracket characters which appear frequently in news headlines.
Plain text is immune to all special characters.

Confirmed alert:
    [WATER CUT ALERT - CONFIRMED]
    Area: F-North / Sion / Matunga / Wadala / CGS

    Location: Sion
    Cut ends: 2026-05-09 16:00

    Headline: BMC announces 30-hour water shutdown...
    Link: https://timesofindia.com/...
    Verified by: Groq LLaMA 3.3 70B + Python

Bypass alert (both AIs exhausted, manual review needed):
    [WATER CUT ALERT - REVIEW NEEDED]
    Area: F-North / Sion / Matunga / Wadala / CGS

    AI quota exhausted - please verify manually.
    Passed headline and area keyword filters.

    Headline: BMC announces 30-hour water shutdown...
    Link: https://...

The clean_plain_text() function normalises AI response text before inserting it into
the message. It replaces em-dashes, en-dashes, and curly quotes with plain ASCII
equivalents to avoid encoding issues in Telegram.

---

## Run Summary

Printed at the end of every run:

    SCAN COMPLETE  [01:48 IST]
    Total articles scanned        : 14
    Passed Filter 1 (headline)    : 9
    Passed Filter 2 (body + area) : 1
    Gemini API calls made         : 1
    Groq API calls made           : 1
    Alerts sent                   : 0
    Gemini RPD exhausted this run - Groq was used as fallback.
    No water cuts detected for F-North today.

---

## Bug Fix History

    No.  Bug                              Root Cause                                    Fix
    ---  -------------------------------- -------------------------------------------- -------------------------------------------
    1    Script never found any alerts    entry.link scraped Google redirect page        Added decode_google_news_url() to follow
                                          not the real article                           HTTP redirects to real article URL

    2    Articles after 6:30 PM IST       UTC published_parsed compared against IST      Fixed is_published_recently() to convert
         were dropped                     date without timezone conversion               UTC to IST before comparison

    3    Gemini silently returned NO       gemini-2.5-flash-lite is not a valid model    Changed to valid model name
         for all calls                    name; fallback try/except never triggered      gemini-2.0-flash

    4    Same article processed twice     Different encoded Google URLs for same          Added seen_titles set deduplication
                                          article across two RSS feeds                   on normalised title

    5    Em-dash in headlines skipped      Dash character class [-] missing em-dash      Expanded to include em-dash in all
         by regex                                                                        pattern locations

    6    Telegram messages never arrived   parse_mode=Markdown breaks on _ * [           Switched to plain text, no parse_mode
                                          in news headlines causing 400 error

    7    Emoji appeared as garbled text    Ubuntu 24.04 console encoding                 Removed emoji from all Telegram message
         in GitHub Actions logs                                                          strings

    8    False alert sent for 2-day-old   AI judged date + status + location             Split responsibilities: AI translates
         expired cut                      simultaneously and hallucinated "Ongoing"      date only, Python checks expiry

    9    AI hallucinating expiry status   Over-complex multi-step prompt                 Rewrote to two narrow tasks: location
                                                                                         check + date translation only

    10   Groq response **CONFIRMED**      Missing .replace("**", "") on Groq output      Added markdown strip to Groq response
         not detected by parser                                                          parsing

    11   BODY_FULL_CUT missing synonyms   New synonyms added to headline patterns        Fully synced BODY_FULL_CUT list with
                                          but not synced to body patterns                HEADLINE_FULL_CUT list

    12   April 30 articles passing date   Google News re-crawl assigns today's           Fixed: use oldest of published_parsed
         filter                           timestamp to old articles                      vs updated_parsed

---

## GitHub Actions Setup

The bot runs on a scheduled cron every hour via GitHub Actions.

GitHub only reads workflow YAML files from the path:
    .github/workflows/

A folder named github/workflows/ without the leading dot is treated as a regular
directory and completely ignored by GitHub. This was the reason the keepalive workflow
never ran and the main scanner was disabled after 60 days.

GitHub automatically disables scheduled workflows in repositories that have had no
activity for 60 days. The keepalive workflow prevents this by making a small automated
commit on the 1st and 15th of every month, keeping the repository active.

Two workflow files in .github/workflows/:

    File              Purpose                              Schedule
    ----------------  -----------------------------------  -------------------------
    water-cut.yml     Main scanner - runs the bot          Every hour
    keepalive.yml     Prevents auto-disable after 60 days  1st and 15th of each month

---

## Required Secrets (GitHub Repository Secrets)

    Secret            Purpose
    ----------------  ----------------------------------------
    TELEGRAM_TOKEN    Bot token from @BotFather
    CHAT_ID           Your Telegram chat or group ID
    GEMINI_API_KEY    Google AI Studio API key (free tier)
    GROQ_API_KEY      Groq Cloud API key (free tier)

---

## Architecture Philosophy

The key design rule is: give each layer only the job it is reliable at.

    Responsibility               Handler          Reason
    ---------------------------  ---------------  ----------------------------------------
    Article age check            Python           Math is deterministic - AI cannot be
                                                  trusted with date calculations
    Deduplication                Python           Set membership check - zero ambiguity
    Headline relevance           Python (regex)   100% predictable, no API cost
    URL decoding                 Python           HTTP redirect follow - not a language task
    Article scraping             BeautifulSoup    Structured HTML parsing
    Area keyword check           Python (regex)   Exact word matching - AI not needed
    Full-cut phrase check        Python (regex)   Same - regex is sufficient and free
    Geographic ambiguity         AI               The one thing regex genuinely cannot do:
    resolution                   Gemini/Groq      distinguish "cut IN Sion" vs "pipeline
                                                  NEAR Sion highway"
    Date/time translation        AI               Translates human language dates to ISO
                                 Gemini/Groq      format only - no judgment
    Expiry decision              Python           Arithmetic: parsed_time < now - never
                                                  delegate time math to AI
    Message normalisation        Python           clean_plain_text() - deterministic
