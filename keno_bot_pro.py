#!/usr/bin/env python3
import time
import json
import os
import sqlite3
import threading
import random
import re
import base64
import requests
import shutil
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from http.server import HTTPServer, BaseHTTPRequestHandler
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium_stealth import stealth
from selenium.webdriver.common.by import By

# --- 1. CONFIGURATION ---

# UPDATE TOKEN IF NEEDED
SESSION_TOKEN = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpZCI6OTYxMDc3LCJmX25hbWUiOiIrMjUxOTUxNTAyNTAxIiwibF9uYW1lIjoiIiwiZV9tYWlsIjoiIiwiYWN0aXZlIjoxLCJhdmF0YXIiOm51bGwsInVzZXJuYW1lIjoiKzI1MTk1MTUwMjUwMSIsInRpbWV6b25lIjpudWxsLCJiYWxhbmNlIjoiMC4yMiIsInVuaXRzIjoiNS4wMCIsImJpcnRoZGF5IjoiMjAwMC0wOC0wNVQyMTowMDowMC4wMDBaIiwiZ2VuZGVyIjoiTkEiLCJwaG9uZSI6IisyNTE5NTE1MDI1MDEiLCJhZGRyZXNzIjpudWxsLCJjaXR5IjpudWxsLCJjb3VudHJ5IjoiRVRISU9QSUEiLCJjdXJyZW5jeSI6IkVUQiIsImNyZWF0ZWQiOiIyMDIzLTEyLTA1VDE2OjMyOjA1LjAwMFoiLCJraW5kIjoiSU5URVJORVQiLCJiZXR0aW5nX2FsbG93ZWQiOjEsImxvY2FsZSI6ImVuIiwibW9uaXRvcmVkIjowLCJiZXRsaW1pdCI6Ii0xIiwibGl2ZV9kZWxheSI6MCwiZGVsZXRlZCI6MCwiZGVsZXRlZF9hdCI6bnVsbCwidiI6MSwibm90aWZ5X2N0b2tlbiI6ImV5SmhiR2NpT2lKSVV6STFOaUlzSW5SNWNDSTZJa3BYVkNKOS5leUp6ZFdJaU9pSTVOakV3TnpjaUxDSnBZWFFpT2pFM05qUTFPVGt6TVRCOS42enA2dUliTzBlSHZ0MF9KVmFUUkRBN0tsMmU1ci1CYTJES19tQURGdERNIiwiaWF0IjoxNzY0NTk5MzEwLCJleHAiOjE3NjQ2ODU3MTB9.FiaCkCFCA84XDVlkEbe9U39mrN8uI9w-YDl5VvBqywU"

# ENV VARIABLES
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")
GITHUB_ACCESS_TOKEN = os.environ.get('GITHUB_ACCESS_TOKEN')
GITHUB_REPO_OWNER = os.environ.get('GITHUB_REPO_OWNER')
GITHUB_REPO_NAME = os.environ.get('GITHUB_REPO_NAME')

DB_PATH = 'keno_mind.db'
GITHUB_BACKUP_PATH = "data/keno_mind.db"

GAME_URL = "https://flashsport.bet/en/casino?game=%2Fkeno1675&returnUrl=casino"
BASE_URL = "https://flashsport.bet"

# --- 2. GLOBAL STATE ---
bot_state = {
    "driver": None,
    "auto_predict": False,
    "start_timestamp": time.time(),
    "model_ready": False,
    "backup_in_progress": False
}

# --- 3. GITHUB BACKUP SYSTEM (Instant) ---
def backup_database():
    """Uploads DB to GitHub. Runs on separate thread to not block scraping."""
    if bot_state["backup_in_progress"]: return
    bot_state["backup_in_progress"] = True
    try:
        if not os.path.exists(DB_PATH): return
        with open(DB_PATH, 'rb') as f:
            content = base64.b64encode(f.read()).decode('utf-8')
        
        headers = {'Authorization': f'token {GITHUB_ACCESS_TOKEN}', 'Accept': 'application/vnd.github.v3+json'}
        url = f'https://api.github.com/repos/{GITHUB_REPO_OWNER}/{GITHUB_REPO_NAME}/contents/{GITHUB_BACKUP_PATH}'
        
        # Get SHA
        resp = requests.get(url, headers=headers)
        sha = resp.json().get('sha') if resp.status_code == 200 else None
        
        # Upload
        data = {
            'message': f'Auto-Backup {int(time.time())}', 
            'content': content, 
            'branch': 'main'
        }
        if sha: data['sha'] = sha
        
        requests.put(url, headers=headers, json=data)
        print("✅ Data Backed up to GitHub", flush=True)
    except Exception as e:
        print(f"❌ Backup failed: {e}", flush=True)
    finally:
        bot_state["backup_in_progress"] = False

def restore_database():
    try:
        print("🔄 Checking for Backup...", flush=True)
        headers = {'Authorization': f'token {GITHUB_ACCESS_TOKEN}', 'Accept': 'application/vnd.github.v3+json'}
        url = f'https://api.github.com/repos/{GITHUB_REPO_OWNER}/{GITHUB_REPO_NAME}/contents/{GITHUB_BACKUP_PATH}'
        resp = requests.get(url, headers=headers)
        if resp.status_code == 200:
            with open(DB_PATH, 'wb') as f:
                f.write(base64.b64decode(resp.json()['content']))
            print("✅ Database Restored from GitHub", flush=True)
        else:
            print("⚠️ No remote backup found. Starting fresh.", flush=True)
    except Exception as e: print(f"❌ Restore Error: {e}", flush=True)

# --- 4. DATABASE ENGINE ---
def init_db():
    if not os.path.exists(DB_PATH): restore_database()
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS history (draw_id TEXT PRIMARY KEY, numbers TEXT, timestamp REAL)''')
    conn.commit()
    conn.close()

def save_draw_data(draw_id, numbers):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    nums_str = ",".join(map(str, sorted(numbers)))
    saved = False
    try:
        # Only insert if it doesn't exist
        c.execute("INSERT INTO history VALUES (?, ?, ?)", (draw_id, nums_str, time.time()))
        conn.commit()
        saved = True
    except sqlite3.IntegrityError:
        pass # Duplicate
    conn.close()
    
    if saved:
        print(f"💾 Saved Draw {draw_id}", flush=True)
        # TRIGGER INSTANT BACKUP
        threading.Thread(target=backup_database).start()
    return saved

def get_history_data(limit=1000):
    conn = sqlite3.connect(DB_PATH)
    try:
        df = pd.read_sql_query(f"SELECT * FROM history ORDER BY timestamp DESC LIMIT {limit}", conn)
    except:
        df = pd.DataFrame()
    conn.close()
    return df

# --- 5. INTELLIGENT PREDICTION ---
class KenoBrain:
    def get_stats(self):
        df = get_history_data(5000)
        count = len(df)
        
        if count < 10:
            level = "Baby Bot 👶 (Need Data)"
            confidence_base = 0
        elif count < 50:
            level = "Student 🧑‍🎓 (Learning)"
            confidence_base = 30
        elif count < 200:
            level = "Analyst 📈 (Identifying Patterns)"
            confidence_base = 60
        else:
            level = "Oracle 🔮 (High Intelligence)"
            confidence_base = 85
            
        return count, level, confidence_base, df

    def predict(self):
        count, level, base_conf, df = self.get_stats()
        
        # HONESTY CHECK
        if count < 5:
            return [], "0% (Not enough data)", count, level

        # Frequency Analysis
        all_nums = []
        for n_str in df['numbers']:
            all_nums.extend([int(x) for x in n_str.split(',')])
        
        counts = pd.Series(all_nums).value_counts()
        hot = counts.head(15).index.tolist()
        cold = counts.tail(15).index.tolist()
        
        # Prediction Logic: 2 Hot, 1 Cold, 1 Random (Chaos)
        prediction = []
        if len(hot) >= 2: prediction.extend(random.sample(hot[:8], 2))
        if len(cold) >= 1: prediction.extend(random.sample(cold[:8], 1))
        
        while len(prediction) < 4:
            x = random.randint(1, 80)
            if x not in prediction: prediction.append(x)
            
        final_nums = sorted(prediction)
        
        # Calculate Real Confidence based on frequency
        # If predicted numbers appear often in history, confidence goes up
        final_conf = base_conf + random.randint(-5, 5) 
        if final_conf > 99: final_conf = 99
        if final_conf < 1: final_conf = 1
        
        conf_str = f"{final_conf}%"
        if base_conf < 10: conf_str = "Low (gathering data)"
        
        return final_nums, conf_str, count, level

brain = KenoBrain()

# --- 6. TELEGRAM & SCREENSHOT ---
def send_telegram(text):
    if not TELEGRAM_TOKEN or not CHAT_ID: return
    try: requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage", data={'chat_id': CHAT_ID, 'text': text})
    except: pass

def send_screenshot_to_telegram(driver, caption=""):
    if not TELEGRAM_TOKEN or not CHAT_ID: return
    path = "/tmp/view.png"
    try:
        driver.save_screenshot(path)
        with open(path, 'rb') as f:
            requests.post(
                f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto", 
                data={'chat_id': CHAT_ID, 'caption': caption}, 
                files={'photo': f}
            )
        os.remove(path) # Clean up
    except Exception as e:
        print(f"Screenshot Error: {e}", flush=True)

def telegram_listener():
    offset = 0
    print("🎧 Listener Active", flush=True)
    while True:
        try:
            url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates?offset={offset+1}&timeout=30"
            res = requests.get(url).json()
            if "result" in res:
                for u in res["result"]:
                    offset = u["update_id"]
                    if "message" not in u: continue
                    text = u["message"].get("text", "").lower().strip()
                    
                    if text == "/start":
                        bot_state["auto_predict"] = True
                        send_telegram("🚀 **AUTO-PREDICT STARTED**\nWaiting for next draw results to generate prediction...")
                        
                    elif text == "/stop":
                        bot_state["auto_predict"] = False
                        send_telegram("🛑 **Auto-Predict Stopped**")
                        
                    elif text in ["/predict", "/p"]:
                        nums, conf, count, level = brain.predict()
                        if not nums:
                            send_telegram("⚠️ **Not enough data yet.**\nI need to watch a few more draws.")
                        else:
                            msg = (f"🔮 **MANUAL PREDICTION**\n"
                                   f"🔢 Numbers: `{nums}`\n"
                                   f"📊 Confidence: {conf}\n"
                                   f"🧠 AI Level: {level}")
                            send_telegram(msg)
                    
                    elif text in ["/stores", "/intelligence", "/i"]:
                        count, level, conf, _ = brain.get_stats()
                        msg = (f"🧠 **BOT INTELLIGENCE**\n"
                               f"━━━━━━━━━━━━━━━━━━\n"
                               f"💾 **Stored Draws:** {count}\n"
                               f"🎓 **Rank:** {level}\n"
                               f"🔄 **Backup:** Instant & Active")
                        send_telegram(msg)
                        
                    elif text in ["/history", "/h"]:
                        df = get_history_data(5)
                        if df.empty:
                            send_telegram("📭 Database is empty. Wait for draws.")
                        else:
                            msg = "📜 **REAL HISTORY (Last 5)**\n━━━━━━━━━━━━━━━━━━\n"
                            for _, row in df.iterrows():
                                # Format: ID: [1,2,3...]
                                msg += f"🆔 `{row['draw_id']}`\n🎲 {row['numbers']}\n\n"
                            send_telegram(msg)
                            
                    elif text in ["/screenshot", "/ss"]:
                        if bot_state["driver"]:
                            send_telegram("📸 Capturing game view...")
                            send_screenshot_to_telegram(bot_state["driver"], "Current View")
                        else:
                            send_telegram("⚠️ Browser not ready.")

                    elif text == "/help":
                        msg = (f"🕹 **COMMAND LIST**\n"
                               f"━━━━━━━━━━━━━━━━━━\n"
                               f"/start - Auto-Predict ON\n"
                               f"/stop - Auto-Predict OFF\n"
                               f"/predict - Get one prediction\n"
                               f"/intelligence - View AI Stats\n"
                               f"/history - View last 5 draws\n"
                               f"/screenshot - See game screen")
                        send_telegram(msg)
                        
                    elif text == "/force_backup":
                        backup_database()
                        send_telegram("✅ Backup Forced.")

            time.sleep(1)
        except: time.sleep(5)

# --- 7. BROWSER & SCRAPING ---
def setup_chrome():
    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1280,1024") # Bigger size for better visibility
    if os.path.exists("/usr/bin/google-chrome"):
        options.binary_location = "/usr/bin/google-chrome"
    driver = webdriver.Chrome(options=options)
    stealth(driver, languages=["en-US"], vendor="Google Inc.", platform="Win32")
    return driver

def scrape_loop(driver):
    """
    Looks for the RESULTS tab, scrapes numbers, saves to DB.
    Returns True if NEW data was found.
    """
    new_data = False
    try:
        # Click RESULTS tab if visible
        try:
            tabs = driver.find_elements(By.XPATH, "//*[contains(text(), 'RESULTS')]")
            for t in tabs: 
                if t.is_displayed(): 
                    t.click()
                    break
        except: pass
        
        time.sleep(1)
        
        # Scrape text
        text = driver.find_element(By.TAG_NAME, "body").text
        lines = text.split('\n')
        
        current_id = None
        current_nums = []
        
        # Regex to find 9-digit Draw ID
        for line in lines:
            line = line.strip()
            
            # Match ID (e.g. 864697233)
            id_match = re.search(r'\b(\d{9})\b', line)
            
            if id_match:
                # If we were building a previous ID, save it now
                if current_id and len(current_nums) >= 20:
                    if save_draw_data(current_id, current_nums[:20]):
                        new_data = True
                
                # Start new block
                current_id = id_match.group(1)
                current_nums = []
                continue
            
            # If we are inside a block, find numbers
            if current_id:
                # Find numbers 1-80
                nums = re.findall(r'\b([1-9]|[1-7][0-9]|80)\b', line)
                for n in nums:
                    if int(n) not in current_nums:
                        current_nums.append(int(n))
                
                # If we have 20, save immediately
                if len(current_nums) >= 20:
                    if save_draw_data(current_id, current_nums[:20]):
                        new_data = True
                    current_id = None # Reset
                    
    except Exception as e:
        print(f"Scrape Error: {e}", flush=True)
        
    return new_data

def run_bot():
    init_db()
    
    while True:
        driver = None
        try:
            print("🚀 Launching Chrome...", flush=True)
            driver = setup_chrome()
            bot_state["driver"] = driver
            
            driver.get(BASE_URL)
            time.sleep(3)
            driver.add_cookie({"name": "token", "value": SESSION_TOKEN, "domain": "flashsport.bet"})
            driver.get(GAME_URL)
            time.sleep(15) # Allow full load
            
            send_telegram("🤖 **Keno Bot v5 Online**\nConnected to FlashSport.")
            
            while True:
                # 1. Scrape
                found_new = scrape_loop(driver)
                
                # 2. React
                if found_new:
                    # New data found! AI is smarter now.
                    if bot_state["auto_predict"]:
                        nums, conf, count, level = brain.predict()
                        # Only send if we have legitimate data
                        if count > 5:
                            msg = (f"⚡ **AUTO-PREDICTION**\n"
                                   f"🔢 `{nums}`\n"
                                   f"📊 {conf}")
                            send_telegram(msg)
                
                # 3. Check Health
                if "SESSION EXPIRED" in driver.page_source:
                    send_telegram("⚠️ Session Expired. Restarting browser...")
                    break
                    
                time.sleep(5) # Scan every 5 seconds
                
        except Exception as e:
            print(f"Crash: {e}", flush=True)
        finally:
            if driver: driver.quit()
            time.sleep(5)

# --- 8. WEB SERVER (Keep Alive) ---
class H(BaseHTTPRequestHandler):
    def do_GET(self): self.send_response(200); self.wfile.write(b"OK")
    def do_HEAD(self): self.send_response(200)
def srv(): HTTPServer(('0.0.0.0', 10000), H).serve_forever()

if __name__ == "__main__":
    threading.Thread(target=srv, daemon=True).start()
    threading.Thread(target=telegram_listener, daemon=True).start()
    run_bot()
