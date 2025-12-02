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
from datetime import datetime, timedelta, timezone
from http.server import HTTPServer, BaseHTTPRequestHandler
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium_stealth import stealth
from sklearn.ensemble import RandomForestClassifier
from selenium.webdriver.common.by import By

# --- 1. CONFIGURATION ---

# UPDATE TOKEN HERE
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
    "last_known_draw": None,
    "model_ready": False,
    "backup_in_progress": False,
    "data_count": 0
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
        data = {'message': f'Keno DB Backup {datetime.now()}', 'content': content, 'branch': 'main'}
        if sha: data['sha'] = sha
        requests.put(url, headers=headers, json=data)
        print("✅ Backup to GitHub success", flush=True)
    except Exception as e: print(f"❌ Backup failed: {e}", flush=True)
    finally: bot_state["backup_in_progress"] = False

def restore_database():
    try:
        print("🔄 Restoring DB...", flush=True)
        headers = {'Authorization': f'token {GITHUB_ACCESS_TOKEN}', 'Accept': 'application/vnd.github.v3+json'}
        url = f'https://api.github.com/repos/{GITHUB_REPO_OWNER}/{GITHUB_REPO_NAME}/contents/{GITHUB_BACKUP_PATH}'
        resp = requests.get(url, headers=headers)
        if resp.status_code == 200:
            with open(DB_PATH, 'wb') as f:
                f.write(base64.b64decode(resp.json()['content']))
            print("✅ DB Restored", flush=True)
            return True
        else: print("⚠️ No backup found", flush=True); return False
    except Exception as e: print(f"❌ Restore Error: {e}", flush=True); return False

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
        c.execute("SELECT predicted FROM predictions WHERE draw_id IS NULL ORDER BY timestamp DESC LIMIT 1")
        row = c.fetchone()
        if row:
            pred_nums = [int(x) for x in row[0].split(',')]
            hits = len(set(pred_nums) & set(numbers))
            c.execute("UPDATE predictions SET draw_id=?, actual=?, hit_count=? WHERE draw_id IS NULL", (draw_id, nums_str, hits))
            conn.commit()
    except sqlite3.IntegrityError: pass 
    conn.close()
    if saved: threading.Thread(target=backup_database).start()
    return saved

def get_data_frame(limit=5000):
    conn = sqlite3.connect(DB_PATH)
    try:
        df = pd.read_sql_query(f"SELECT * FROM history ORDER BY timestamp DESC LIMIT {limit}", conn)
    except:
        df = pd.DataFrame()
    conn.close()
    return df

def get_stats():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM history")
    total_draws = c.fetchone()[0]
    c.execute("SELECT AVG(hit_count) FROM predictions WHERE hit_count IS NOT NULL")
    avg_hits = c.fetchone()[0]
    conn.close()
    return total_draws, (avg_hits if avg_hits else 0)

# --- 5. AI ENGINE ---
class KenoBrain:
    def __init__(self):
        self.hot_numbers = []
        self.cold_numbers = []
        
    def train(self):
        df = get_data_frame(limit=500)
        bot_state["data_count"] = len(df)
        if len(df) < 5: return
        all_nums = []
        for n_str in df['numbers']:
            all_nums.extend([int(x) for x in n_str.split(',')])
        counts = pd.Series(all_nums).value_counts()
        self.hot_numbers = counts.head(15).index.tolist()
        self.cold_numbers = counts.tail(15).index.tolist()
        bot_state["model_ready"] = True

    def predict(self):
        if not bot_state["model_ready"]: self.train()
        count = bot_state["data_count"]
        if count < 20: confidence = "Low 🔴 (Collecting Data)"
        elif count < 100: confidence = "Medium 🟡 (Learning)"
        else: confidence = "High 🟢 (Locked)"

        prediction = []
        if len(self.hot_numbers) >= 2: prediction.extend(random.sample(self.hot_numbers[:8], 2))
        if len(self.cold_numbers) >= 1: prediction.extend(random.sample(self.cold_numbers[:8], 1))
        while len(prediction) < 4:
            x = random.randint(1, 80)
            if x not in prediction: prediction.append(x)
        
        pred_sorted = sorted(prediction)
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("INSERT INTO predictions (draw_id, predicted, actual, hit_count, timestamp) VALUES (?, ?, ?, ?, ?)", 
                  (None, ",".join(map(str, pred_sorted)), None, 0, time.time()))
        conn.commit()
        conn.close()
        return pred_sorted, confidence, count

brain = KenoBrain()

# --- 6. TELEGRAM ---
def send_telegram(text):
    if not TELEGRAM_TOKEN or not CHAT_ID: return
    try: requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage", data={'chat_id': CHAT_ID, 'text': text})
    except: pass

def send_photo(path, caption):
    if not TELEGRAM_TOKEN or not CHAT_ID: return
    try:
        with open(path, 'rb') as f:
            requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto", data={'chat_id': CHAT_ID, 'caption': caption}, files={'photo': f})
    except Exception as e: print(f"Photo Fail: {e}")

def telegram_listener():
    offset = 0
    print("🎧 Telegram Listener Started...", flush=True)
    while True:
        try:
            url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates?offset={offset+1}&timeout=30"
            res = requests.get(url).json()
            if "result" in res:
                for u in res["result"]:
                    offset = u["update_id"]
                    if "message" not in u: continue
                    text = u["message"].get("text", "").lower().strip()
                    
                    # 1. Start/Stop
                    if text == "/start":
                        bot_state["auto_predict"] = True
                        send_telegram("🟢 **Auto-Predict ON**\nMonitoring for new draws...")
                        
                    elif text == "/stop":
                        bot_state["auto_predict"] = False
                        send_telegram("🔴 **Auto-Predict OFF**")
                        
                    # 2. Prediction
                    elif text in ["/predict", "/p"]:
                        nums, conf, count = brain.predict()
                        send_telegram(f"🔮 **MANUAL PREDICTION**\n🔢 `{nums}`\n📊 {conf}\n📚 Based on {count} draws")
                    
                    # 3. Status
                    elif text in ["/status", "/s"]:
                        uptime_sec = int(time.time() - bot_state["start_timestamp"])
                        uptime = str(timedelta(seconds=uptime_sec))
                        auto_status = "🟢 ON" if bot_state["auto_predict"] else "🔴 OFF"
                        total, avg = get_stats()
                        
                        msg = (f"📊 **SYSTEM STATUS**\n"
                               f"🤖 Auto-Predict: {auto_status}\n"
                               f"⏱️ Uptime: {uptime}\n"
                               f"💾 Draws Stored: {total}\n"
                               f"🎯 Avg Accuracy: {avg:.2f} hits\n"
                               f"🔄 Backup System: Active")
                        send_telegram(msg)
                        
                    # 4. History
                    elif text in ["/history", "/h"]:
                        df = get_data_frame(limit=5)
                        if df.empty:
                            send_telegram("📭 No history found yet.")
                        else:
                            msg = "📜 **LAST 5 DRAWS**\n"
                            for index, row in df.iterrows():
                                ts = datetime.fromtimestamp(row['timestamp']).strftime('%H:%M')
                                msg += f"🆔 `{row['draw_id']}` ({ts})\n🔢 {row['numbers']}\n\n"
                            send_telegram(msg)
                            
                    # 5. Accuracy Report
                    elif text in ["/accuracy", "/a"]:
                        total, avg = get_stats()
                        msg = (f"🎯 **ACCURACY REPORT**\n"
                               f"📈 Average Hits: {avg:.2f} / 4\n"
                               f"📚 Data Points: {total} draws analyzed")
                        send_telegram(msg)
                        
                    # 6. Intelligence (Legacy)
                    elif text in ["/stores", "/intelligence", "/i"]:
                        total, avg = get_stats()
                        iq_level = "Baby 👶"
                        if total > 50: iq_level = "Student 🧑‍🎓"
                        if total > 200: iq_level = "Oracle 🔮"
                        send_telegram(f"🧠 **BRAIN POWER**\n💾 Stored: {total}\n🎓 IQ: {iq_level}\n🎯 Accuracy: {avg:.2f}")

                    # 7. Utilities
                    elif text in ["/screenshot", "/ss"]:
                        if bot_state["driver"]:
                            path = "/tmp/screenshot.png"
                            bot_state["driver"].save_screenshot(path)
                            send_photo(path, "📸 Live Vision")
                        else:
                            send_telegram("⚠️ Browser loading...")
                            
                    elif text == "/force_backup":
                        backup_database()
                        send_telegram("✅ Database forced to GitHub.")
                        
                    elif text == "/help":
                        msg = (f"🕹 **COMMAND LIST**\n\n"
                               f"/start - Auto-Predict ON\n"
                               f"/stop - Auto-Predict OFF\n"
                               f"/predict - Get next numbers\n"
                               f"/status - System Health\n"
                               f"/history - Last 5 Draws\n"
                               f"/accuracy - Win Rate\n"
                               f"/screenshot - See Screen\n"
                               f"/force_backup - Save Data")
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

def scrape_results(driver):
    new_data = False
    try:
        try:
            tabs = driver.find_elements(By.XPATH, "//*[contains(text(), 'RESULTS')]")
            for t in tabs: 
                if t.is_displayed(): t.click(); break
        except: pass
        
        text = driver.find_element(By.TAG_NAME, "body").text
        lines = text.split('\n')
        curr_id, nums = None, []
        
        for line in lines:
            line = line.strip()
            id_match = re.search(r'\b(\d{9})\b', line)
            if id_match:
                if curr_id and len(nums) == 20:
                    if save_draw_data(curr_id, nums):
                        new_data = True
                        print(f"Imported {curr_id}", flush=True)
                curr_id = id_match.group(1)
                nums = []
                continue
            if curr_id:
                found = re.findall(r'\b([1-9]|[1-7][0-9]|80)\b', line)
                for n in found:
                    if int(n) not in nums: nums.append(int(n))
                if len(nums) >= 20:
                    nums = nums[:20]
                    if save_draw_data(curr_id, nums):
                        new_data = True
                        print(f"Imported {curr_id}", flush=True)
                    curr_id = None
    except Exception as e: print(f"Scrape Error: {e}", flush=True)
    return new_data

def run_bot():
    init_db()
    def sched():
        while True: time.sleep(600); backup_database()
    threading.Thread(target=sched, daemon=True).start()

    while True:
        driver = None
        try:
            print("🚀 Launching Chrome...", flush=True)
            driver = setup_chrome()
            bot_state["driver"] = driver
            
            print("🔗 Base URL...", flush=True)
            driver.get(BASE_URL)
            time.sleep(3)
            driver.add_cookie({"name": "token", "value": SESSION_TOKEN, "domain": "flashsport.bet"})
            
            print("🎮 Game URL...", flush=True)
            driver.get(GAME_URL)
            time.sleep(15) 
            
            print("✅ Ready.", flush=True)
            send_telegram("🤖 Keno Bot v4 Online. Use /help for commands.")
            
            while True:
                if scrape_results(driver):
                    brain.train()
                    if bot_state["auto_predict"]:
                        nums, conf, count = brain.predict()
                        send_telegram(f"⚡ **AUTO**\n🔢 `{nums}`")
                
                if "SESSION EXPIRED" in driver.page_source:
                    print("⚠️ Session Expired", flush=True)
                    break
                time.sleep(10)
        except Exception as e: print(f"Error: {e}", flush=True)
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
