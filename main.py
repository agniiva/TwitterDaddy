import asyncio
import time
import random
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.keys import Keys
from twitter.account import Account
import os
from openai import AsyncOpenAI
from random import uniform
import json
import logging
import anthropic
import undetected_chromedriver as uc
from dotenv import load_dotenv

# Load environment variables from a .env file
load_dotenv()

# --- Configure Logging ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler("bot.log"),
        logging.StreamHandler()
    ]
)

# --- Configurations ---
# It's crucial to store sensitive information securely. Here, we use environment variables.
openai_api_key = os.getenv("OPENAI_API_KEY")
twitter_cookie_data = {
    "ct0": os.getenv("TWITTER_CT0_COOKIE"),
    "auth_token": os.getenv("TWITTER_AUTH_TOKEN")
}
claude_api_key = os.environ.get("ANTHROPIC_API_KEY")

# Timing configurations
MIN_ACTION_DELAY = 60  # 60 seconds for testing; adjust as needed
MAX_ACTION_DELAY = 300  # 300 seconds for testing; adjust as needed
SCROLL_INTERVAL = 45  # seconds between scrolls

# After other configurations, add this:
SCREENSHOT_DIR = "errorScreenshots"
# Create the directory if it doesn't exist
os.makedirs(SCREENSHOT_DIR, exist_ok=True)

# Action selector schema
ACTION_SELECTOR_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": ["Like", "Retweet", "Reply", "Skip"]
        },
        "content": {
            "type": "string"
        }
    },
    "required": ["action", "content"],
    "additionalProperties": False,
    "strict": True
}

# --- Initialize Clients ---
client = AsyncOpenAI(api_key=openai_api_key)
twitter_client = Account(cookies=twitter_cookie_data)
claude_client = anthropic.Anthropic(api_key=claude_api_key)  # New Claude client
replied_tweet_ids = set()

# --- Initialize Selenium with Cookie Authentication ---
def initialize_driver_with_cookies(cookie_data):
    import ssl
    ssl._create_default_https_context = ssl._create_unverified_context

    options = uc.ChromeOptions()
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/116.0.0.0 Safari/537.36")

    # Masking WebDriver detection
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument(f"--user-data-dir={os.path.join(os.getcwd(), 'user_data')}")
    options.add_argument("--profile-directory=Default")

    # Add this line to bypass SSL verification
    options.add_argument('--ignore-certificate-errors')
    options.add_argument('--ignore-ssl-errors')
    
    # Persistent user data for human-like behavior
    options.add_argument(f"--user-data-dir={os.path.join(os.getcwd(), 'user_data')}")
    options.add_argument("--profile-directory=Default")

    # Using undetected-chromedriver
    driver = uc.Chrome(options=options)

    driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
        "source": """
            Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
            window.navigator.chrome = {runtime: {}};
            Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});
            Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3]});
        """
    })

    driver.get("https://x.com/")

    for key, value in cookie_data.items():
        driver.add_cookie({"name": key, "value": value, "domain": "x.com"})

    driver.refresh()  # Refresh to apply cookies
    return driver


# --- Use the updated driver initialization ---
driver = initialize_driver_with_cookies(twitter_cookie_data)

# --- Updated Delays for Human-like Behavior ---
def random_delay(min_delay, max_delay):
    """Random delay to mimic human actions."""
    delay = uniform(min_delay, max_delay)
    logging.info(f"Sleeping for {delay:.2f} seconds.")
    time.sleep(delay)

# --- Dynamic Viewport Adjustment ---
def randomize_viewport(driver):
    """Randomly adjusts the browser viewport size."""
    width = random.randint(1200, 1920)
    height = random.randint(800, 1080)
    driver.set_window_size(width, height)
    logging.info(f"Set browser window size to {width}x{height}.")

# Randomize viewport before each session
randomize_viewport(driver)

async def get_ai_decision(tweet_text: str):
    """Get AI decision for tweet action using structured output"""
    try:
        response = await client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "system",
                    "content": [
                        {
                            "type": "text",
                            "text": """Analyze tweets and decide the best action to take. Consider:
                                - Tweet content quality and relevance
                                - Potential for meaningful engagement
                                - Appropriateness of different interaction types
                                - Risk of spam or inappropriate content"""
                        }
                    ]
                },
                {
                    "role": "user",
                    "content": [{"type": "text", "text": f"Decide action for this tweet:\n\n{tweet_text}"}]
                }
            ],
            temperature=1,
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": "ActionDecisionSchema",
                    "strict": True,
                    "schema": {
                        "type": "object",
                        "properties": {
                            "action": {
                                "type": "string", "enum": ["Like", "Retweet", "Reply", "Skip"]
                            },
                            "content": {
                                "type": "string",
                                "description": "The content associated with the action."
                            }     
                        },
                        "required": ["action", "content"],
                        "additionalProperties": False
                    }
                }
            }
        )
        response_content = response.choices[0].message.content
        # Parse the JSON response
        decision = json.loads(response_content)
        return decision
    except json.JSONDecodeError as jde:
        logging.error(f"JSON decode error: {jde}. Response was: {response_content}")
        return {"action": "Skip", "content": "Error in decision making process"}
    except Exception as e:
        logging.error(f"Error in AI decision: {e}")
        return {"action": "Skip", "content": "Error in decision making process"}
    
async def get_claude_reply(tweet_text: str, decision_content: str):
    """Get reply content from Claude for a tweet"""
    try:
        message = claude_client.messages.create(
            model="claude-3-5-haiku-20241022",
            max_tokens=1000,
            temperature=0,
            system="""You are Agniva Mahata
                a sarcastic cool guy,be funny, and you bacically reply to tweet with your ideas & 
                concepts on startups, design, business, tech, be very specific and clear keep the tweet reply concise yet profound
                dont be rude, , just reply with the tweet don't add any prefix or suffixes.
                Also keep your replies to 1 liner if you don't have anything specific to say""",
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": f"Reply for this tweet: {tweet_text}\nContext from analysis: {decision_content}"
                        }
                    ]
                }
            ]
        )
        # Extract the text content from Claude's response
        if isinstance(message.content, list):
            return message.content[0].text
        return str(message.content)
    except Exception as e:
        logging.error(f"Error getting Claude reply: {str(e)}")
        return None

def sanitize_text(text):
    """Remove non-BMP characters and normalize whitespace"""
    # Handle Claude's TextBlock response
    if hasattr(text, 'type') and text.type == 'text':
        text = text.text
    elif not isinstance(text, str):
        text = str(text)
    
    sanitized = ''
    for char in text:
        if 32 <= ord(char) <= 126 or char in '""\'.,!?- ':
            sanitized += char
    
    sanitized = ' '.join(sanitized.split())
    sanitized = sanitized[:280]  # Twitter character limit
    
    return sanitized

# --- Scraping and Action Updates ---
async def scrape_home_feed():
    """Scrape tweets from home feed with added human-like behavior."""
    logging.info("Accessing home feed...")
    driver.get("https://x.com/home")
    random_delay(2, 5)

    tweets = []
    last_height = driver.execute_script("return document.body.scrollHeight")

    while len(tweets) < 20:
        try:
            tweet_elements = WebDriverWait(driver, 10).until(
                EC.presence_of_all_elements_located((By.CSS_SELECTOR, '[data-testid="tweetText"]'))
            )
            
            for element in tweet_elements:
                try:
                    link_element = element.find_element(By.XPATH, "./ancestor::article//a[contains(@href, '/status/')]")
                    tweet_id = link_element.get_attribute("href").split('/')[-1]
                    
                    if tweet_id not in replied_tweet_ids:
                        tweets.append({
                            "id": tweet_id,
                            "text": element.text
                        })
                except Exception as e:
                    continue

            driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            random_delay(1.5, 3.0)

            new_height = driver.execute_script("return document.body.scrollHeight")
            if new_height == last_height:
                break
            last_height = new_height

        except Exception as e:
            logging.error(f"Error during home feed scraping: {e}")
            break

    logging.info(f"Scraped {len(tweets)} tweets from home feed.")
    return tweets

async def reply_to_tweet(driver, tweet_id, reply_text):
    try:
        tweet_url = f"https://x.com/i/web/status/{tweet_id}"
        driver.get(tweet_url)
        await asyncio.sleep(3)
        
        reply_button = WebDriverWait(driver, 15).until(
            EC.element_to_be_clickable((By.CSS_SELECTOR, '[data-testid="reply"]'))
        )
        reply_button.click()
        
        await asyncio.sleep(2)
        reply_box = WebDriverWait(driver, 15).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, '[data-testid="tweetTextarea_0"]'))
        )
        
        reply_box.clear()
        sanitized_reply = sanitize_text(reply_text)
        
        reply_box.click()
        await asyncio.sleep(0.5)
        
        # Split text into lines and handle each line separately
        lines = sanitized_reply.split('\n')
        for i, line in enumerate(lines):
            chunk_size = 50
            chunks = [line[i:i+chunk_size] for i in range(0, len(line), chunk_size)]
            
            for chunk in chunks:
                reply_box.send_keys(chunk)
                await asyncio.sleep(0.1)
            
            # Add new line using Shift+Enter if not the last line
            if i < len(lines) - 1:
                reply_box.send_keys(Keys.SHIFT + Keys.ENTER)
                await asyncio.sleep(0.1)
        
        await asyncio.sleep(1)
        
        submit_button = WebDriverWait(driver, 15).until(
            EC.element_to_be_clickable((By.CSS_SELECTOR, '[data-testid="tweetButton"]'))
        )
        submit_button.click()
        await asyncio.sleep(5)
        return True
            
    except Exception as e:
        logging.error(f"Error replying to tweet {tweet_id}: {e}")
        try:
            screenshot_path = os.path.join(SCREENSHOT_DIR, f"error_screenshot_{tweet_id}.png")
            driver.save_screenshot(screenshot_path)
            logging.info(f"Screenshot saved at: {screenshot_path}")
        except Exception as screenshot_error:
            logging.error(f"Error saving screenshot for tweet {tweet_id}: {screenshot_error}")
        return False

async def perform_ai_action(tweet):
    """Perform action based on AI decision"""
    try:
        # Get AI decision
        decision = await get_ai_decision(tweet["text"])
        logging.info(f"AI Decision for tweet {tweet['id']}: {decision}")
        
        if decision["action"] == "Reply":
            reply_content = await get_claude_reply(tweet["text"], decision["content"])
            if reply_content:
                success = await reply_to_tweet(driver, tweet["id"], reply_content)
                if success:
                    replied_tweet_ids.add(tweet["id"])
                    logging.info(f"Replied to tweet: https://x.com/i/web/status/{tweet['id']}")
                
        elif decision["action"] == "Like":
            like_button = WebDriverWait(driver, 10).until(
                EC.element_to_be_clickable((By.CSS_SELECTOR, '[data-testid="like"]'))
            )
            like_button.click()
            logging.info(f"Liked tweet: {tweet['id']}")
            
        elif decision["action"] == "Retweet":
            retweet_button = WebDriverWait(driver, 10).until(
                EC.element_to_be_clickable((By.CSS_SELECTOR, '[data-testid="retweet"]'))
            )
            retweet_button.click()
            await asyncio.sleep(1)
            
            confirm_retweet = WebDriverWait(driver, 10).until(
                EC.element_to_be_clickable((By.CSS_SELECTOR, '[data-testid="retweetConfirm"]'))
            )
            confirm_retweet.click()
            logging.info(f"Retweeted: {tweet['id']}")
            
        elif decision["action"] == "Skip":
            logging.info(f"Skipped tweet {tweet['id']}: {decision['content']}")
            
    except Exception as e:
        logging.error(f"Error performing AI action on tweet {tweet['id']}: {e}")

# --- Improved Main Loop ---
async def main():
    while True:
        try:
            tweets = await scrape_home_feed()
            
            for tweet in tweets:
                tweet_url = f"https://x.com/i/web/status/{tweet['id']}"
                driver.get(tweet_url)
                random_delay(2, 4)  # Human-like pause
                
                await perform_ai_action(tweet)
                
                random_delay(MIN_ACTION_DELAY, MAX_ACTION_DELAY)
            
            refresh_delay = uniform(300, 600)
            logging.info(f"Refreshing feed in {refresh_delay:.2f} seconds...")
            await asyncio.sleep(refresh_delay)

        except Exception as e:
            logging.error(f"Error in main loop: {e}")
            await asyncio.sleep(300)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.info("Bot stopped manually.")
    except Exception as e:
        logging.error(f"Unexpected error: {e}")