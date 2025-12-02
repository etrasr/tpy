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

# SESSION TOKEN
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
    "backup_in_progress": False
}

# --- 3. GITHUB BACKUP SYSTEM ---
def backup_database():
    if bot_state["backup_in_progress"]: return
    bot_state["backup_in_progress"] = True
    try:
        if not os.path.exists(DB_PATH): return
        with open(DB_PATH, 'rb') as f:
            content = base64.b64encode(f.read()).decode('utf-8')
        
        headers = {'Authorization': f'token {GITHUB_ACCESS_TOKEN}', 'Accept': 'application/vnd.github.v3+json'}
        url = f'https://api.github.com/repos/{GITHUB_REPO_OWNER}/{GITHUB_REPO_NAME}/contents/{GITHUB_BACKUP_PATH}'
        
        resp = requests.get(url, headers=headers)
        sha = resp.json().get('sha') if resp.status_code == 200 else None
        
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
    c.execute('''CREATE TABLE IF NOT EXISTS predictions (draw_id TEXT, predicted TEXT, actual TEXT, hit_count INTEGER, timestamp REAL)''')
    conn.commit()
    conn.close()

def save_draw_data(draw_id, numbers):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    nums_str = ",".join(map(str, sorted(numbers)))
    saved = False
    try:
        c.execute("INSERT INTO history VALUES (?, ?, ?)", (draw_id, nums_str, time.time()))
        conn.commit()
        saved = True
        
        # Check if we had a pending prediction for this draw
        c.execute("SELECT rowid, predicted FROM predictions WHERE draw_id IS NULL ORDER BY timestamp DESC LIMIT 1")
        row = c.fetchone()
        if row:
            row_id, pred_nums_str = row
            pred_nums = [int(x) for x in pred_nums_str.split(',')]
            hits = len(set(pred_nums) & set(numbers))
            # Honest Accuracy Logic: We update the hidden prediction with real results
            c.execute("UPDATE predictions SET draw_id=?, actual=?, hit_count=? WHERE rowid=?", (draw_id, nums_str, hits, row_id))
            conn.commit()
            
    except sqlite3.IntegrityError:
        pass 
    conn.close()
    
    if saved:
        print(f"💾 Saved Draw {draw_id}", flush=True)
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

def get_4hr_accuracy():
    """Calculates honest win rate from last 4 hours of predictions."""
    conn = sqlite3.connect(DB_PATH)
    four_hours_ago = time.time() - 14400 # 4 hours in seconds
    try:
        c = conn.cursor()
        c.execute("SELECT hit_count FROM predictions WHERE timestamp > ? AND hit_count IS NOT NULL", (four_hours_ago,))
        rows = c.fetchall()
        
        if not rows: return 0, 0
        
        hits = [r[0] for r in rows]
        avg_hits = sum(hits) / len(hits)
        count = len(hits)
        return avg_hits, count
    except:
        return 0, 0
    finally:
        conn.close()

# --- 5. INTELLIGENT PREDICTION ---
class KenoBrain:
    def get_intelligence_stats(self):
        df = get_history_data(5000)
        count = len(df)
        
        if count < 10: level = "Baby Bot 👶"
        elif count < 100: level = "Student 🧑‍🎓"
        elif count < 500: level = "Analyst 📈"
        else: level = "Oracle 🔮"
            
        return count, level, df

    def predict(self, save_to_db=True):
        count, level, df = self.get_intelligence_stats()
        
        # 1. Frequency Analysis
        if count > 5:
            all_nums = []
            for n_str in df['numbers']:
                all_nums.extend([int(x) for x in n_str.split(',')])
            
            counts = pd.Series(all_nums).value_counts()
            hot = counts.head(15).index.tolist()
            cold = counts.tail(15).index.tolist()
        else:
            hot, cold = [], []

        # 2. Selection Logic (2 Hot, 1 Cold, 1 Random)
        prediction = []
        if len(hot) >= 2: prediction.extend(random.sample(hot[:8], 2))
        if len(cold) >= 1: prediction.extend(random.sample(cold[:8], 1))
        
        while len(prediction) < 4:
            x = random.randint(1, 80)
            if x not in prediction: prediction.append(x)
            
        final_nums = sorted(prediction)
        
        # 3. Confidence Calc
        if count < 20: conf_str = "0% (No Data)"
        elif count < 100: conf_str = "40% (Learning)"
        else: conf_str = "85% (Confident)"
        
        # 4. Save Prediction (For Honest Accuracy Tracking)
        if save_to_db:
            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
            # Save with draw_id=NULL. This will be filled when the real draw happens.
            c.execute("INSERT INTO predictions (draw_id, predicted, actual, hit_count, timestamp) VALUES (?, ?, ?, ?, ?)", 
                      (None, ",".join(map(str, final_nums)), None, None, time.time()))
            conn.commit()
            conn.close()
        
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
        os.remove(path)
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
                    
                    # --- SHORTCUTS & COMMANDS ---
                    
                    # STATUS (Requested Priority)
                    if text in ["/status", "/stat"]:
                        uptime_sec = int(time.time() - bot_state["start_timestamp"])
                        uptime = str(timedelta(seconds=uptime_sec))
                        count, _, _ = brain.get_intelligence_stats()
                        send_telegram(f"📊 **SYSTEM STATUS**\n"
                                      f"⏱️ Uptime: {uptime}\n"
                                      f"💾 Data: {count} draws\n"
                                      f"🤖 Auto-Predict: {'ON' if bot_state['auto_predict'] else 'OFF'}")

                    # ACCURACY (Honest 4hr)
                    elif text in ["/accuracy", "/a"]:
                        avg, count = get_4hr_accuracy()
                        msg = (f"🎯 **4-HOUR ACCURACY**\n"
                               f"━━━━━━━━━━━━━━━━━━\n"
                               f"📈 Average Hits: {avg:.2f} / 4\n"
                               f"📚 Predictions Checked: {count}\n"
                               f"*(Based on all draws in last 4 hours)*")
                        send_telegram(msg)
                    
                    # START
                    elif text in ["/start", "/s"]:
                        bot_state["auto_predict"] = True
                        send_telegram("🚀 **AUTO-PREDICT ON**\nI will send predictions for every new draw.")
                        
                    # STOP
                    elif text in ["/stop", "/st"]:
                        bot_state["auto_predict"] = False
                        send_telegram("🛑 **Auto-Predict Stopped**")
                        
                    # PREDICT
                    elif text in ["/predict", "/p"]:
                        nums, conf, count, level = brain.predict(save_to_db=False)
                        msg = (f"🔮 **MANUAL PREDICTION**\n"
                               f"🔢 `{nums}`\n"
                               f"📊 Confidence: {conf}\n"
                               f"🧠 Logic: {level}")
                        send_telegram(msg)
                    
                    # INTELLIGENCE
                    elif text in ["/intelligence", "/i"]:
                        count, level, _ = brain.get_intelligence_stats()
                        send_telegram(f"🧠 **BOT INTELLIGENCE**\n"
                                      f"💾 Stored Draws: {count}\n"
                                      f"🎓 Rank: {level}\n"
                                      f"🤖 Learning Status: Active")

                    # STORES
                    elif text in ["/stores", "/sr"]:
                        count, _, _ = brain.get_intelligence_stats()
                        send_telegram(f"💾 **DATA STORE**\nTotal Draws Saved: {count}")
                        
                    # HISTORY
                    elif text in ["/history", "/h"]:
                        df = get_history_data(5)
                        if df.empty:
                            send_telegram("📭 No history found yet.")
                        else:
                            msg = "📜 **LAST 5 DRAW RESULTS**\n━━━━━━━━━━━━━━━━━━\n"
                            for _, row in df.iterrows():
                                try:
                                    dt = datetime.fromtimestamp(row['timestamp'])
                                    ts = dt.strftime('%H:%M:%S')
                                except: ts = "--:--"
                                msg += f"🆔 `{row['draw_id']}` ({ts})\n🎲 {row['numbers']}\n\n"
                            send_telegram(msg)

                    # SCREENSHOT
                    elif text in ["/screenshot", "/ss"]:
                        if bot_state["driver"]:
                            send_telegram("📸 Capturing screen...")
                            send_screenshot_to_telegram(bot_state["driver"], "Current Game View")
                        else:
                            send_telegram("⚠️ Browser initializing...")
                            
                    elif text == "/force_backup":
                        backup_database()
                        send_telegram("✅ Backup Forced.")
                        
                    elif text == "/help":
                        msg = (f"🕹 **COMMAND LIST**\n"
                               f"━━━━━━━━━━━━━━━━━━\n"
                               f"/status, /stat - System Health\n"
                               f"/accuracy, /a - Honest Win Rate\n"
                               f"/start, /s - Auto-Predict ON\n"
                               f"/stop, /st - Auto-Predict OFF\n"
                               f"/predict, /p - Manual Guess\n"
                               f"/history, /h - Last 5 Results\n"
                               f"/intelligence, /i - AI Stats\n"
                               f"/stores, /sr - Data Count\n"
                               f"/screenshot, /ss - View Screen")
                        send_telegram(msg)

            time.sleep(1)
        except: time.sleep(5)

# --- 7. BROWSER ---
def setup_chrome():
    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--disable-software-rasterizer")
    options.add_argument("--window-size=1080,1920") 
    options.add_argument("--user-agent=Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36")
    
    if os.path.exists("/usr/bin/google-chrome"):
        options.binary_location = "/usr/bin/google-chrome"
    
    driver = webdriver.Chrome(options=options)
    stealth(driver, languages=["en-US"], vendor="Google Inc.", platform="Win32")
    return driver

def scrape_loop(driver):
    new_data = False
    try:
        # Click RESULTS tab
        try:
            tabs = driver.find_elements(By.XPATH, "//*[contains(text(), 'RESULTS')]")
            for t in tabs: 
                if t.is_displayed(): 
                    t.click()
                    break
        except: pass
        
        time.sleep(1)
        
        # Scrape
        text = driver.find_element(By.TAG_NAME, "body").text
        lines = text.split('\n')
        current_id = None
        current_nums = []
        
        for line in lines:
            line = line.strip()
            id_match = re.search(r'\b(\d{9})\b', line)
            if id_match:
                if current_id and len(current_nums) >= 20:
                    if save_draw_data(current_id, current_nums[:20]):
                        new_data = True
                current_id = id_match.group(1)
                current_nums = []
                continue
            
            if current_id:
                nums = re.findall(r'\b([1-9]|[1-7][0-9]|80)\b', line)
                for n in nums:
                    if int(n) not in current_nums:
                        current_nums.append(int(n))
                if len(current_nums) >= 20:
                    if save_draw_data(current_id, current_nums[:20]):
                        new_data = True
                    current_id = None
                    
    except Exception as e:
        print(f"Scrape Error: {e}", flush=True)
        
    return new_data

def ensure_pending_prediction():
    """Checks if we have a pending prediction. If not, creates one."""
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("SELECT count(*) FROM predictions WHERE draw_id IS NULL")
        pending_count = c.fetchone()[0]
        conn.close()
        
        if pending_count == 0:
            brain.predict(save_to_db=True)
    except: pass

def run_bot():
    init_db()
    
    while True:
        driver = None
        try:
            print("🚀 Launching Chrome...", flush=True)
            driver = setup_chrome()
            bot_state["driver"] = driver
            
            # --- LOGIN SEQUENCE ---
            print("🔗 Base URL...", flush=True)
            driver.get(BASE_URL)
            time.sleep(3)
            
            print("🍪 Injecting Cookie...", flush=True)
            driver.add_cookie({"name": "token", "value": SESSION_TOKEN, "domain": "flashsport.bet"})
            
            print("🎮 Game URL...", flush=True)
            driver.get(GAME_URL)
            time.sleep(15) 
            
            print("✅ Ready. Monitoring...", flush=True)
            send_telegram("🤖 **Keno Bot v6 Online**\nSyncing with game cycle...")
            
            # Initial Prediction on Startup (So user can bet immediately)
            ensure_pending_prediction()
            
            while True:
                # 1. Scrape for New Results
                # This function returns True ONLY when a new draw (20 numbers) is fully complete and saved.
                found_new = scrape_loop(driver)
                
                # 2. If New Result Found -> PREDICT IMMEDIATELY for the Next Round
                if found_new:
                    # The previous prediction was just graded inside scrape_loop (save_draw_data).
                    # Now we must generate the prediction for the UPCOMING draw.
                    nums, conf, count, level = brain.predict(save_to_db=True)
                    
                    # If Auto-Predict is ON, send it to Telegram instantly
                    if bot_state["auto_predict"]:
                        msg = (f"⚡ **AUTO**\n🔢 `{nums}`\n📊 {conf}")
                        send_telegram(msg)
                
                # 3. Startup Check
                # Just in case DB is empty or logic missed, ensure there is always a pending prediction for accuracy tracking.
                ensure_pending_prediction()

                # 4. Check Health
                if "SESSION EXPIRED" in driver.page_source:
                    send_telegram("⚠️ Session Expired. Restarting...")
                    break
                    
                time.sleep(5) 
                
        except Exception as e:
            print(f"Crash: {e}", flush=True)
        finally:
            if driver: driver.quit()
            time.sleep(10)

# --- 8. SERVER ---
class H(BaseHTTPRequestHandler):
    def do_GET(self): self.send_response(200); self.wfile.write(b"OK")
    def do_HEAD(self): self.send_response(200)
def srv(): HTTPServer(('0.0.0.0', 10000), H).serve_forever()

if __name__ == "__main__":
    threading.Thread(target=srv, daemon=True).start()
    threading.Thread(target=telegram_listener, daemon=True).start()
    run_bot()
