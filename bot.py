import logging
import random
import sqlite3
import asyncio
import json
import os
import requests
import time
import string
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
import signal
import sys

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, LabeledPrice, PreCheckoutQuery
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    ContextTypes, MessageHandler, filters, PreCheckoutQueryHandler
)
from telegram.constants import ParseMode
from telegram.error import TelegramError

# ======================== НАСТРОЙКА ========================
TELEGRAM_TOKEN = os.environ.get("BOT_TOKEN", "")
CRYPTOBOT_API_KEY = os.environ.get("CRYPTOBOT_API_KEY", "")
CRYPTOBOT_API_URL = "https://pay.crypt.bot/api"

ADMIN_IDS = [int(id) for id in os.environ.get("ADMIN_IDS", "5697184715").split(",")]

BOT_NAME = "FEENDY STARS"
BOT_USERNAME = "FeendyStars_robot"

WELCOME_IMAGE_ID = None
CASE_IMAGE_ID = None

# Курс валют (1 $ = 0.2 TON, т.е. 1 TON = 5 $)
DOLLAR_PER_TON = 5.0
TON_PER_DOLLAR = 1 / DOLLAR_PER_TON
MIN_DEPOSIT_DOLLARS = 1.0
MAX_DEPOSIT_DOLLARS = 250.0

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ======================== CRYPTOBOT API ========================
class CryptoBotAPI:
    def __init__(self, api_key):
        self.api_key = api_key
        self.headers = {
            "Crypto-Pay-API-Token": api_key,
            "Content-Type": "application/json"
        }

    def create_invoice(self, amount_dollars, currency="TON", description="Пополнение баланса FEENDY STARS"):
        try:
            url = f"{CRYPTOBOT_API_URL}/createInvoice"
            if amount_dollars < MIN_DEPOSIT_DOLLARS:
                logger.warning(f"Сумма {amount_dollars}$ меньше минимальной")
                return None

            ton_amount = round(amount_dollars * TON_PER_DOLLAR, 2)
            if ton_amount < 0.1:
                ton_amount = 0.1
                amount_dollars = ton_amount / TON_PER_DOLLAR

            payload = {
                "asset": currency,
                "amount": str(ton_amount),
                "description": f"{description} на {amount_dollars:.2f}$",
                "paid_btn_name": "callback",
                "paid_btn_url": f"https://t.me/{BOT_USERNAME}",
                "payload": f"crypto_{int(amount_dollars*100)}_{int(time.time())}"
            }
            logger.info(f"Создание счёта CryptoBot: {amount_dollars}$ = {ton_amount} TON")
            response = requests.post(url, headers=self.headers, json=payload, timeout=10)
            if response.status_code == 200:
                data = response.json()
                if data.get('ok'):
                    return data['result']
                else:
                    logger.error(f"Ошибка CryptoBot: {data.get('error')}")
            else:
                logger.error(f"HTTP ошибка CryptoBot: {response.status_code}")
            return None
        except Exception as e:
            logger.error(f"Ошибка CryptoBot API: {e}")
            return None

    def transfer(self, user_id, amount, currency="TON"):
        try:
            url = f"{CRYPTOBOT_API_URL}/transfer"
            payload = {
                "user_id": user_id,
                "asset": currency,
                "amount": str(amount),
                "spend_id": f"withdraw_{user_id}_{int(time.time())}"
            }
            response = requests.post(url, headers=self.headers, json=payload, timeout=10)
            if response.status_code == 200:
                data = response.json()
                return data.get('ok', False)
            return False
        except Exception as e:
            logger.error(f"Ошибка перевода CryptoBot: {e}")
            return False

crypto = CryptoBotAPI(CRYPTOBOT_API_KEY)

# ======================== БАЗА ДАННЫХ ========================
class Database:
    def __init__(self):
        db_path = os.environ.get("DB_PATH", "feendy_stars.db")
        if '/app/data' in db_path:
            try:
                os.makedirs('/app/data', exist_ok=True)
                logger.info("📁 Папка /app/data готова")
            except:
                pass

        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.cursor = self.conn.cursor()
        self._create_tables()
        self._init_admin()
        self._load_images()
        self._init_promocodes()
        self._init_shop()
        self._init_lottery()
        self._load_game_settings()

    def _create_tables(self):
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                balance REAL DEFAULT 0.0,
                snowflakes INTEGER DEFAULT 0,
                referrals INTEGER DEFAULT 0,
                referred_by INTEGER,
                daily_bonus DATE,
                crypto_id TEXT,
                telegram_username TEXT,
                is_admin INTEGER DEFAULT 0,
                is_banned INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                total_withdrawn REAL DEFAULT 0.0,
                total_lost REAL DEFAULT 0.0
            )
        ''')
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS games (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                game_type TEXT,
                bet REAL,
                multiplier REAL,
                win REAL,
                result TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS inventory (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                item_name TEXT,
                item_type TEXT,
                item_value REAL,
                source TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS cases (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT,
                price REAL,
                items TEXT
            )
        ''')
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS withdrawals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                amount REAL,
                method TEXT,
                wallet TEXT,
                status TEXT DEFAULT 'pending',
                reject_reason TEXT,
                admin_id INTEGER,
                processed_at TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS nft_withdrawals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                nft_name TEXT,
                nft_value REAL,
                status TEXT DEFAULT 'pending',
                reject_reason TEXT,
                admin_id INTEGER,
                processed_at TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS promocodes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                code TEXT UNIQUE,
                amount REAL,
                expires_at DATE,
                max_uses INTEGER,
                used_count INTEGER DEFAULT 0,
                created_by INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS promocode_uses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                code TEXT,
                used_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS payments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                amount REAL,
                method TEXT,
                invoice_id TEXT,
                status TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        ''')
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS shop (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT,
                price INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS lottery (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                ticket_number INTEGER,
                purchase_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                is_winner INTEGER DEFAULT 0
            )
        ''')
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS lottery_stats (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                total_tickets_sold INTEGER DEFAULT 0,
                prize_pool REAL DEFAULT 10.0,
                last_winner_id INTEGER,
                last_winner_ticket INTEGER,
                draw_date TIMESTAMP
            )
        ''')
        self.conn.commit()
        self._init_cases()

    def _load_game_settings(self):
        global GAME_SETTINGS
        self.cursor.execute('SELECT value FROM settings WHERE key = ?', ('game_settings',))
        res = self.cursor.fetchone()
        if res:
            try:
                GAME_SETTINGS = json.loads(res[0])
            except:
                pass

    def save_game_settings(self):
        self.cursor.execute('INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)',
                          ('game_settings', json.dumps(GAME_SETTINGS)))
        self.conn.commit()

    def _init_cases(self):
        self.cursor.execute('SELECT COUNT(*) FROM cases')
        if self.cursor.fetchone()[0] == 0:
            case_items = [
                {'name': '❤️ Сердце', 'chance': 60, 'value': 1.5, 'type': 'gift'},
                {'name': '🌹 Роза', 'chance': 17, 'value': 2.5, 'type': 'gift'},
                {'name': '🚀 Ракета', 'chance': 7, 'value': 5.0, 'type': 'gift'},
                {'name': '🌸 Цветы', 'chance': 7, 'value': 5.0, 'type': 'gift'},
                {'name': '💍 Кольцо', 'chance': 3, 'value': 10.0, 'type': 'gift'},
                {'name': '💎 Алмаз', 'chance': 1.5, 'value': 10.0, 'type': 'gift'},
                {'name': '🍭 Lol pop', 'chance': 1, 'value': 32.5, 'type': 'nft'},
                {'name': '🐕 Snoop Dogg', 'chance': 1, 'value': 42.5, 'type': 'nft'}
            ]
            self.cursor.execute(
                'INSERT INTO cases (name, price, items) VALUES (?, ?, ?)',
                (BOT_NAME, 3.5, json.dumps(case_items))
            )
            self.conn.commit()

    def _init_shop(self):
        self.cursor.execute('SELECT COUNT(*) FROM shop')
        if self.cursor.fetchone()[0] == 0:
            items = [
                ('🧦 Носок', 1250),
                ('📦 Змея в коробке', 1250),
                ('🐍 Змея 2025', 1250),
                ('🔔 Колокольчики', 1600),
                ('🎆 Бенгальские огни', 1300),
                ('🍪 Пряничный человечек', 1550)
            ]
            self.cursor.executemany('INSERT INTO shop (name, price) VALUES (?, ?)', items)
            self.conn.commit()

    def _init_admin(self):
        for admin_id in ADMIN_IDS:
            self.cursor.execute('SELECT * FROM users WHERE user_id = ?', (admin_id,))
            user = self.cursor.fetchone()
            if user:
                self.cursor.execute('UPDATE users SET is_admin = 1, is_banned = 0 WHERE user_id = ?', (admin_id,))
            else:
                self.cursor.execute('''
                    INSERT INTO users (user_id, username, first_name, is_admin, is_banned)
                    VALUES (?, 'admin', 'Admin', 1, 0)
                ''', (admin_id,))
        self.conn.commit()

    def _init_lottery(self):
        self.cursor.execute('SELECT COUNT(*) FROM lottery_stats')
        if self.cursor.fetchone()[0] == 0:
            self.cursor.execute('''
                INSERT INTO lottery_stats (total_tickets_sold, prize_pool)
                VALUES (0, 10.0)
            ''')
            self.conn.commit()

    def _load_images(self):
        global WELCOME_IMAGE_ID, CASE_IMAGE_ID
        self.cursor.execute('SELECT value FROM settings WHERE key = ?', ('welcome_image',))
        res = self.cursor.fetchone()
        if res:
            WELCOME_IMAGE_ID = res[0]
        self.cursor.execute('SELECT value FROM settings WHERE key = ?', ('case_image',))
        res = self.cursor.fetchone()
        if res:
            CASE_IMAGE_ID = res[0]

    def save_image(self, key, file_id):
        global WELCOME_IMAGE_ID, CASE_IMAGE_ID
        self.cursor.execute('INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)', (key, file_id))
        self.conn.commit()
        if key == 'welcome_image':
            WELCOME_IMAGE_ID = file_id
        elif key == 'case_image':
            CASE_IMAGE_ID = file_id

    def _init_promocodes(self):
        self.cursor.execute('SELECT COUNT(*) FROM promocodes')
        if self.cursor.fetchone()[0] == 0:
            expiry = (datetime.now() + timedelta(days=30)).date()
            self.cursor.execute('''
                INSERT INTO promocodes (code, amount, expires_at, max_uses, created_by)
                VALUES (?, ?, ?, ?, ?)
            ''', ('FEENDY10', 10.0, expiry, 100, ADMIN_IDS[0]))
            self.conn.commit()

    # ---------- Методы работы с пользователями ----------
    def get_user(self, user_id):
        self.cursor.execute('SELECT * FROM users WHERE user_id = ?', (user_id,))
        return self.cursor.fetchone()

    def create_user(self, user_id, username, first_name, referred_by=None):
        is_admin = 1 if user_id in ADMIN_IDS else 0
        self.cursor.execute('''
            INSERT OR IGNORE INTO users (user_id, username, first_name, referred_by, is_admin, is_banned)
            VALUES (?, ?, ?, ?, ?, 0)
        ''', (user_id, username, first_name, referred_by, is_admin))
        self.conn.commit()
        if referred_by and referred_by not in ADMIN_IDS:
            self.cursor.execute('UPDATE users SET referrals = referrals + 1, snowflakes = snowflakes + 5 WHERE user_id = ?', (referred_by,))
            self.conn.commit()

    def update_balance(self, user_id, amount):
        self.cursor.execute('UPDATE users SET balance = balance + ? WHERE user_id = ?', (amount, user_id))
        self.conn.commit()

    def update_snowflakes(self, user_id, amount):
        self.cursor.execute('UPDATE users SET snowflakes = snowflakes + ? WHERE user_id = ?', (amount, user_id))
        self.conn.commit()

    def add_lost_stars(self, user_id, amount):
        self.cursor.execute('UPDATE users SET total_lost = total_lost + ?, snowflakes = snowflakes + ? WHERE user_id = ?',
                            (amount, int(amount * 0.5 * 10)), user_id)
        self.conn.commit()

    def update_crypto_id(self, user_id, crypto_id):
        self.cursor.execute('UPDATE users SET crypto_id = ? WHERE user_id = ?', (crypto_id, user_id))
        self.conn.commit()

    def update_telegram_username(self, user_id, username):
        self.cursor.execute('UPDATE users SET telegram_username = ? WHERE user_id = ?', (username, user_id))
        self.conn.commit()

    def get_all_users(self):
        self.cursor.execute('SELECT user_id, username, first_name, balance, snowflakes, is_banned, is_admin, created_at FROM users ORDER BY created_at DESC')
        return self.cursor.fetchall()

    def add_game(self, user_id, game_type, bet, multiplier, win, result):
        self.cursor.execute('''
            INSERT INTO games (user_id, game_type, bet, multiplier, win, result)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (user_id, game_type, bet, multiplier, win, result))
        self.conn.commit()

    def get_cases(self):
        self.cursor.execute('SELECT * FROM cases')
        return self.cursor.fetchall()

    def open_case(self, case_id, user_id):
        try:
            self.cursor.execute('SELECT * FROM cases WHERE id = ?', (case_id,))
            case = self.cursor.fetchone()
            if not case:
                return None
            items = json.loads(case[3])
            total = sum(item['chance'] for item in items)
            r = random.uniform(0, total)
            cur = 0
            for item in items:
                cur += item['chance']
                if r <= cur:
                    if item['type'] == 'nft':
                        self.cursor.execute('''
                            INSERT INTO inventory (user_id, item_name, item_type, item_value, source)
                            VALUES (?, ?, ?, ?, 'case')
                        ''', (user_id, item['name'], item['type'], item['value']))
                        self.conn.commit()
                    return item
            return None
        except Exception as e:
            logger.error(f"Ошибка открытия кейса: {e}")
            return None

    def get_inventory(self, user_id):
        self.cursor.execute('SELECT item_name, item_value FROM inventory WHERE user_id = ?', (user_id,))
        return self.cursor.fetchall()

    def get_user_stats(self, user_id):
        self.cursor.execute('''
            SELECT COUNT(*), SUM(CASE WHEN win > 0 THEN 1 ELSE 0 END),
                   SUM(CASE WHEN win = 0 THEN 1 ELSE 0 END),
                   SUM(bet), SUM(win)
            FROM games WHERE user_id = ?
        ''', (user_id,))
        return self.cursor.fetchone()

    def check_daily_bonus(self, user_id):
        today = datetime.now().date()
        self.cursor.execute('SELECT daily_bonus FROM users WHERE user_id = ?', (user_id,))
        res = self.cursor.fetchone()
        if not res or not res[0] or datetime.strptime(res[0], '%Y-%m-%d').date() < today:
            r = random.random()
            if r < 0.4:
                bonus = 0.1
            elif r < 0.7:
                bonus = 0.2
            elif r < 0.85:
                bonus = 0.3
            elif r < 0.95:
                bonus = 0.4
            else:
                bonus = 0.5
            self.cursor.execute('UPDATE users SET daily_bonus = ?, balance = balance + ? WHERE user_id = ?', (today, bonus, user_id))
            self.conn.commit()
            return bonus
        return 0.0

    # ---------- Платежи ----------
    def add_payment(self, user_id, amount, method, invoice_id=None, status='pending'):
        self.cursor.execute('''
            INSERT INTO payments (user_id, amount, method, invoice_id, status)
            VALUES (?, ?, ?, ?, ?)
        ''', (user_id, amount, method, invoice_id, status))
        self.conn.commit()
        return self.cursor.lastrowid

    def confirm_payment(self, invoice_id):
        self.cursor.execute('SELECT * FROM payments WHERE invoice_id = ?', (invoice_id,))
        payment = self.cursor.fetchone()
        if payment:
            self.cursor.execute('UPDATE payments SET status = "completed" WHERE invoice_id = ?', (invoice_id,))
            self.update_balance(payment[1], payment[2])
            self.conn.commit()
            return True
        return False

    def confirm_crypto_payment(self, payload):
        try:
            parts = payload.split('_')
            if len(parts) >= 3 and parts[0] == 'crypto':
                amount_cents = int(parts[1])
                amount_dollars = amount_cents / 100.0
                # здесь нужно получить user_id из вебхука, но для упрощения оставим заглушку
                return True
        except:
            pass
        return False

    # ---------- Зимний магазин ----------
    def get_shop_items(self):
        self.cursor.execute('SELECT name, price FROM shop ORDER BY price')
        return self.cursor.fetchall()

    # ---------- Вывод средств ----------
    def create_withdrawal(self, user_id, amount, method, wallet):
        self.cursor.execute('''
            INSERT INTO withdrawals (user_id, amount, method, wallet)
            VALUES (?, ?, ?, ?)
        ''', (user_id, amount, method, wallet))
        self.conn.commit()
        return self.cursor.lastrowid

    def get_pending_withdrawals(self):
        self.cursor.execute('''
            SELECT w.*, u.username, u.first_name
            FROM withdrawals w
            JOIN users u ON w.user_id = u.user_id
            WHERE w.status = 'pending'
            ORDER BY w.created_at ASC
        ''')
        return self.cursor.fetchall()

    def approve_withdrawal(self, withdrawal_id, admin_id):
        self.cursor.execute('''
            SELECT user_id, amount FROM withdrawals WHERE id = ? AND status = 'pending'
        ''', (withdrawal_id,))
        w = self.cursor.fetchone()
        if not w:
            return False
        user_id, amount = w
        user = self.get_user(user_id)
        if user[3] < amount:
            return False
        self.update_balance(user_id, -amount)
        self.cursor.execute('''
            UPDATE withdrawals SET status = 'approved', admin_id = ?, processed_at = CURRENT_TIMESTAMP
            WHERE id = ?
        ''', (admin_id, withdrawal_id))
        self.conn.commit()
        self.cursor.execute('UPDATE users SET total_withdrawn = total_withdrawn + ? WHERE user_id = ?', (amount, user_id))
        self.conn.commit()
        return True

    def complete_withdrawal(self, withdrawal_id, admin_id):
        self.cursor.execute('''
            UPDATE withdrawals SET status = 'completed', admin_id = ?, processed_at = CURRENT_TIMESTAMP
            WHERE id = ? AND status = 'approved'
        ''', (admin_id, withdrawal_id))
        self.conn.commit()
        return self.cursor.rowcount > 0

    def reject_withdrawal(self, withdrawal_id, admin_id, reason):
        self.cursor.execute('''
            UPDATE withdrawals SET status = 'rejected', admin_id = ?, reject_reason = ?, processed_at = CURRENT_TIMESTAMP
            WHERE id = ? AND status = 'pending'
        ''', (admin_id, reason, withdrawal_id))
        self.conn.commit()
        return self.cursor.rowcount > 0

    def get_user_withdrawals(self, user_id):
        self.cursor.execute('''
            SELECT id, amount, method, status, reject_reason, created_at
            FROM withdrawals
            WHERE user_id = ?
            ORDER BY created_at DESC LIMIT 10
        ''', (user_id,))
        return self.cursor.fetchall()

    # ---------- Вывод NFT ----------
    def create_nft_withdrawal(self, user_id, nft_name, nft_value):
        self.cursor.execute('''
            INSERT INTO nft_withdrawals (user_id, nft_name, nft_value)
            VALUES (?, ?, ?)
        ''', (user_id, nft_name, nft_value))
        self.conn.commit()
        return self.cursor.lastrowid

    def get_pending_nft_withdrawals(self):
        self.cursor.execute('''
            SELECT w.*, u.username, u.first_name
            FROM nft_withdrawals w
            JOIN users u ON w.user_id = u.user_id
            WHERE w.status = 'pending'
            ORDER BY w.created_at ASC
        ''')
        return self.cursor.fetchall()

    def approve_nft_withdrawal(self, withdrawal_id, admin_id):
        self.cursor.execute('''
            UPDATE nft_withdrawals SET status = 'approved', admin_id = ?, processed_at = CURRENT_TIMESTAMP
            WHERE id = ? AND status = 'pending'
        ''', (admin_id, withdrawal_id))
        self.conn.commit()
        return self.cursor.rowcount > 0

    def complete_nft_withdrawal(self, withdrawal_id, admin_id):
        self.cursor.execute('''
            UPDATE nft_withdrawals SET status = 'completed', admin_id = ?, processed_at = CURRENT_TIMESTAMP
            WHERE id = ? AND status = 'approved'
        ''', (admin_id, withdrawal_id))
        self.conn.commit()
        return self.cursor.rowcount > 0

    def reject_nft_withdrawal(self, withdrawal_id, admin_id, reason):
        self.cursor.execute('''
            UPDATE nft_withdrawals SET status = 'rejected', admin_id = ?, reject_reason = ?, processed_at = CURRENT_TIMESTAMP
            WHERE id = ? AND status = 'pending'
        ''', (admin_id, reason, withdrawal_id))
        self.conn.commit()
        return self.cursor.rowcount > 0

    # ---------- Промокоды ----------
    def generate_promocode(self, amount, days_valid, max_uses, created_by):
        code = ''.join(random.choices(string.ascii_uppercase + string.digits, k=8))
        expires_at = (datetime.now() + timedelta(days=days_valid)).date()
        self.cursor.execute('''
            INSERT INTO promocodes (code, amount, expires_at, max_uses, created_by)
            VALUES (?, ?, ?, ?, ?)
        ''', (code, amount, expires_at, max_uses, created_by))
        self.conn.commit()
        return code

    def get_promocode_info(self, code):
        self.cursor.execute('SELECT * FROM promocodes WHERE code = ?', (code,))
        return self.cursor.fetchone()

    def activate_promocode(self, user_id, code):
        promo = self.get_promocode_info(code)
        if not promo:
            return {'success': False, 'reason': '❌ Код не найден'}
        if promo[3] and datetime.now().date() > datetime.strptime(promo[3], '%Y-%m-%d').date():
            return {'success': False, 'reason': '❌ Промокод истёк'}
        if promo[4] > 0 and promo[5] >= promo[4]:
            return {'success': False, 'reason': '❌ Промокод использован максимальное количество раз'}
        self.cursor.execute('SELECT * FROM promocode_uses WHERE user_id = ? AND code = ?', (user_id, code))
        if self.cursor.fetchone():
            return {'success': False, 'reason': '❌ Вы уже активировали этот промокод'}
        self.update_balance(user_id, promo[2])
        self.cursor.execute('INSERT INTO promocode_uses (user_id, code) VALUES (?, ?)', (user_id, code))
        self.cursor.execute('UPDATE promocodes SET used_count = used_count + 1 WHERE code = ?', (code,))
        self.conn.commit()
        return {'success': True, 'amount': promo[2]}

    def get_all_promocodes(self):
        self.cursor.execute('SELECT * FROM promocodes ORDER BY created_at DESC')
        return self.cursor.fetchall()

    # ---------- Лотерея ----------
    def get_lottery_stats(self):
        self.cursor.execute('SELECT * FROM lottery_stats ORDER BY id DESC LIMIT 1')
        return self.cursor.fetchone()

    def get_user_tickets(self, user_id):
        self.cursor.execute('SELECT ticket_number FROM lottery WHERE user_id = ?', (user_id,))
        return self.cursor.fetchall()

    def buy_lottery_ticket(self, user_id, count=1):
        stats = self.get_lottery_stats()
        if not stats:
            return {'success': False, 'message': 'Ошибка лотереи'}
        total_sold = stats[1]
        if total_sold + count > 100:
            return {'success': False, 'message': f'Осталось всего {100 - total_sold} билетов'}
        tickets = []
        for _ in range(count):
            while True:
                ticket_num = random.randint(100000, 999999)
                self.cursor.execute('SELECT id FROM lottery WHERE ticket_number = ?', (ticket_num,))
                if not self.cursor.fetchone():
                    break
            self.cursor.execute('''
                INSERT INTO lottery (user_id, ticket_number)
                VALUES (?, ?)
            ''', (user_id, ticket_num))
            tickets.append(ticket_num)
        self.cursor.execute('''
            UPDATE lottery_stats
            SET total_tickets_sold = total_tickets_sold + ?
            WHERE id = ?
        ''', (count, stats[0]))
        self.conn.commit()
        new_total = total_sold + count
        if new_total >= 100:
            self.draw_lottery()
        return {'success': True, 'tickets': tickets, 'count': count}

    def draw_lottery(self):
        try:
            stats = self.get_lottery_stats()
            if not stats:
                return
            self.cursor.execute('SELECT user_id, ticket_number FROM lottery')
            tickets = self.cursor.fetchall()
            if not tickets:
                return
            winner = random.choice(tickets)
            winner_id = winner[0]
            winner_ticket = winner[1]
            prize = 10.0
            self.update_balance(winner_id, prize)
            self.cursor.execute('''
                UPDATE lottery_stats
                SET last_winner_id = ?, last_winner_ticket = ?, draw_date = CURRENT_TIMESTAMP,
                    total_tickets_sold = 0
                WHERE id = ?
            ''', (winner_id, winner_ticket, stats[0]))
            self.cursor.execute('DELETE FROM lottery')
            self.conn.commit()
            return {
                'winner_id': winner_id,
                'winner_ticket': winner_ticket,
                'prize': prize
            }
        except Exception as e:
            logger.error(f"Ошибка розыгрыша лотереи: {e}")
            return None

    # ---------- Статистика для админа ----------
    def _get_most_popular_game(self, since):
        self.cursor.execute('''
            SELECT game_type, COUNT(*) as cnt FROM games
            WHERE created_at >= ?
            GROUP BY game_type
            ORDER BY cnt DESC
            LIMIT 1
        ''', (since,))
        row = self.cursor.fetchone()
        if row:
            names = {
                'flip': '🪙 Орёл и решка',
                'roulette': '💀 Русская рулетка',
                'slots': '🎰 Слоты',
                'mines': '💣 Минное поле',
                'dice': '🎲 Кости',
                'football': '⚽ Футбол',
                'basketball': '🏀 Баскетбол',
                'darts': '🎯 Дартс',
                'bowling': '🎳 Боулинг'
            }
            return names.get(row[0], row[0])
        return '—'

    def get_daily_stats(self):
        today = datetime.now().date()
        since = today.strftime('%Y-%m-%d 00:00:00')
        self.cursor.execute('SELECT COUNT(*) FROM users WHERE created_at >= ?', (since,))
        new_users = self.cursor.fetchone()[0]
        self.cursor.execute('SELECT COUNT(*) FROM games WHERE created_at >= ?', (since,))
        games = self.cursor.fetchone()[0]
        self.cursor.execute('SELECT SUM(amount) FROM payments WHERE created_at >= ? AND status = "completed"', (since,))
        deposits = self.cursor.fetchone()[0] or 0.0
        self.cursor.execute('SELECT SUM(amount) FROM withdrawals WHERE processed_at >= ? AND status = "completed"', (since,))
        withdrawals = self.cursor.fetchone()[0] or 0.0
        profit = deposits - withdrawals
        popular = self._get_most_popular_game(since)
        return {
            'new_users': new_users,
            'games': games,
            'deposits': deposits,
            'withdrawals': withdrawals,
            'profit': profit,
            'popular': popular
        }

    def get_weekly_stats(self):
        week_ago = (datetime.now() - timedelta(days=7)).strftime('%Y-%m-%d %H:%M:%S')
        self.cursor.execute('SELECT COUNT(*) FROM users WHERE created_at >= ?', (week_ago,))
        new_users = self.cursor.fetchone()[0]
        self.cursor.execute('SELECT COUNT(*) FROM games WHERE created_at >= ?', (week_ago,))
        games = self.cursor.fetchone()[0]
        self.cursor.execute('SELECT SUM(amount) FROM payments WHERE created_at >= ? AND status = "completed"', (week_ago,))
        deposits = self.cursor.fetchone()[0] or 0.0
        self.cursor.execute('SELECT SUM(amount) FROM withdrawals WHERE processed_at >= ? AND status = "completed"', (week_ago,))
        withdrawals = self.cursor.fetchone()[0] or 0.0
        profit = deposits - withdrawals
        popular = self._get_most_popular_game(week_ago)
        return {
            'new_users': new_users,
            'games': games,
            'deposits': deposits,
            'withdrawals': withdrawals,
            'profit': profit,
            'popular': popular
        }

    def get_monthly_stats(self):
        month_ago = (datetime.now() - timedelta(days=30)).strftime('%Y-%m-%d %H:%M:%S')
        self.cursor.execute('SELECT COUNT(*) FROM users WHERE created_at >= ?', (month_ago,))
        new_users = self.cursor.fetchone()[0]
        self.cursor.execute('SELECT COUNT(*) FROM games WHERE created_at >= ?', (month_ago,))
        games = self.cursor.fetchone()[0]
        self.cursor.execute('SELECT SUM(amount) FROM payments WHERE created_at >= ? AND status = "completed"', (month_ago,))
        deposits = self.cursor.fetchone()[0] or 0.0
        self.cursor.execute('SELECT SUM(amount) FROM withdrawals WHERE processed_at >= ? AND status = "completed"', (month_ago,))
        withdrawals = self.cursor.fetchone()[0] or 0.0
        profit = deposits - withdrawals
        popular = self._get_most_popular_game(month_ago)
        return {
            'new_users': new_users,
            'games': games,
            'deposits': deposits,
            'withdrawals': withdrawals,
            'profit': profit,
            'popular': popular
        }

    # ---------- Баны ----------
    def ban_user(self, admin_id, user_id):
        admin = self.get_user(admin_id)
        if not admin or admin[10] != 1:
            return False
        target = self.get_user(user_id)
        if target and target[10] == 1:
            return False
        self.cursor.execute('UPDATE users SET is_banned = 1 WHERE user_id = ?', (user_id,))
        self.conn.commit()
        return True

    def unban_user(self, admin_id, user_id):
        admin = self.get_user(admin_id)
        if not admin or admin[10] != 1:
            return False
        self.cursor.execute('UPDATE users SET is_banned = 0 WHERE user_id = ?', (user_id,))
        self.conn.commit()
        return True

    def get_banned_users(self):
        self.cursor.execute('SELECT user_id, username, first_name FROM users WHERE is_banned = 1')
        return self.cursor.fetchall()

    def get_total_stats(self):
        self.cursor.execute('SELECT COUNT(*) FROM users')
        total_users = self.cursor.fetchone()[0]
        self.cursor.execute('SELECT SUM(balance) FROM users')
        total_balance = self.cursor.fetchone()[0] or 0.0
        self.cursor.execute('SELECT SUM(snowflakes) FROM users')
        total_snowflakes = self.cursor.fetchone()[0] or 0
        self.cursor.execute('SELECT SUM(total_withdrawn) FROM users')
        total_withdrawn = self.cursor.fetchone()[0] or 0.0
        self.cursor.execute('SELECT COUNT(*) FROM games')
        total_games = self.cursor.fetchone()[0]
        return {
            'total_users': total_users,
            'total_balance': total_balance,
            'total_snowflakes': total_snowflakes,
            'total_withdrawn': total_withdrawn,
            'total_games': total_games
        }

    def cleanup_old_pending(self):
        week_ago = (datetime.now() - timedelta(days=7)).strftime('%Y-%m-%d %H:%M:%S')
        self.cursor.execute('''
            UPDATE withdrawals SET status = 'expired'
            WHERE status = 'pending' AND created_at < ?
        ''', (week_ago,))
        self.cursor.execute('''
            UPDATE nft_withdrawals SET status = 'expired'
            WHERE status = 'pending' AND created_at < ?
        ''', (week_ago,))
        self.conn.commit()

    def close(self):
        self.conn.close()

# ======================== НАСТРОЙКИ ИГР ========================
GAME_SETTINGS = {
    'flip': {'win_multiplier': 1.7, 'loss_multiplier': 0},
    'roulette': {'1': 1.1, '2': 1.3, '3': 1.7, '4': 2.5, '5': 4.5, '6': 0},
    'dice_number': {'win_multiplier': 4.7, 'loss_multiplier': 0},
    'dice_even_odd': {'win_multiplier': 1.7, 'loss_multiplier': 0},
    'slots': {'1': 1.4, '2': 1.5, '3': 5.0},
    'football': {'goal': 1.2, 'miss': 0},
    'basketball': {'point': 1.4, 'miss': 0},
    'darts': {'bullseye': 1.95, 'miss': 0},
    'bowling': {'strike': 1.9, 'miss': 0}
}

# ======================== БОТ ========================
db = Database()

def signal_handler(sig, frame):
    print('🛑 Остановка...')
    db.close()
    sys.exit(0)

signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)

# ======================== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ========================
async def edit_message(query, text, keyboard=None):
    try:
        if query.message.photo:
            if keyboard:
                await query.message.reply_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=keyboard)
            else:
                await query.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)
            await query.edit_message_reply_markup(reply_markup=None)
        else:
            if keyboard:
                await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=keyboard)
            else:
                await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN)
        return True
    except Exception as e:
        logger.error(f"Ошибка редактирования: {e}")
        return False

async def check_ban(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id in ADMIN_IDS:
        return True
    user = db.get_user(user_id)
    if user and user[11] == 1:
        if update.message:
            await update.message.reply_text("❌ Вы заблокированы")
        return False
    return True

def back_button(target='main_menu'):
    return InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Назад", callback_data=target)]])

def home_button():
    return InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Главное меню", callback_data="main_menu")]])

# ======================== ПРОВЕРКА БАЛАНСА ========================
async def check_balance_and_offer(update, context, user_id, required_amount, action_callback, success_message, game_data=None):
    user = db.get_user(user_id)
    if not user:
        if isinstance(update, Update) and update.message:
            await update.message.reply_text("❌ Пользователь не найден")
        return
    balance = user[3]
    if balance >= required_amount:
        if game_data:
            context.user_data['game_data'] = game_data
        context.user_data['pending_action'] = action_callback
        text = f"{success_message}\n\n💰 С баланса спишется ${required_amount:.2f}."
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("✅ Подтвердить", callback_data=action_callback)]])
        try:
            if isinstance(update, Update):
                if update.callback_query:
                    await update.callback_query.edit_message_text(text, reply_markup=kb)
                elif update.message:
                    await update.message.reply_text(text, reply_markup=kb)
            else:
                await update.message.reply_text(text, reply_markup=kb)
        except Exception as e:
            logger.error(f"Ошибка check_balance: {e}")
            if isinstance(update, Update) and update.message:
                await update.message.reply_text(text, reply_markup=kb)
    else:
        missing = required_amount - balance
        text = (f"❌ Недостаточно средств!\n\n"
                f"Требуется: ${required_amount:.2f}\n"
                f"У вас: ${balance:.2f}\n"
                f"Не хватает: ${missing:.2f}\n\n"
                f"Пополнить сейчас?")
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton(f"💰 Пополнить ${missing:.2f}", callback_data="deposit_crypto")],
            [InlineKeyboardButton("◀️ Назад", callback_data="main_menu")]
        ])
        if isinstance(update, Update):
            if update.callback_query:
                await update.callback_query.edit_message_text(text, reply_markup=kb)
            elif update.message:
                await update.message.reply_text(text, reply_markup=kb)
        else:
            await update.message.reply_text(text, reply_markup=kb)
            # ======================== ИГРЫ ========================
async def play_flip(update, context, user_id):
    query = update.callback_query
    user = db.get_user(user_id)
    win_mult = GAME_SETTINGS['flip']['win_multiplier']
    text = (f"🪙 *ОРЁЛ И РЕШКА*\n\n"
            f"💰 Баланс: ${user[3]:.2f}\n\n"
            f"🎲 Шанс 50/50\n"
            f"• 🦅 Орёл - x{win_mult}\n"
            f"• 🪙 Решка - x{win_mult}\n\n"
            f"Выбери на что ставишь:")
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton(f"🦅 ОРЁЛ (x{win_mult})", callback_data="flip_choice_1"),
         InlineKeyboardButton(f"🪙 РЕШКА (x{win_mult})", callback_data="flip_choice_2")],
        [InlineKeyboardButton("◀️ Назад", callback_data="casino_menu")]
    ])
    await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=kb)

async def play_roulette(update, context, user_id):
    query = update.callback_query
    user = db.get_user(user_id)
    text = (f"💀 *РУССКАЯ РУЛЕТКА*\n\n"
            f"💰 Баланс: ${user[3]:.2f}\n\n"
            f"🎲 Шансы и коэффициенты:\n"
            f"• 1️⃣ патрон - x{GAME_SETTINGS['roulette']['1']} (шанс 5/6)\n"
            f"• 2️⃣ патрона - x{GAME_SETTINGS['roulette']['2']} (шанс 4/6)\n"
            f"• 3️⃣ патрона - x{GAME_SETTINGS['roulette']['3']} (шанс 3/6)\n"
            f"• 4️⃣ патрона - x{GAME_SETTINGS['roulette']['4']} (шанс 2/6)\n"
            f"• 5️⃣ патронов - x{GAME_SETTINGS['roulette']['5']} (шанс 1/6)\n"
            f"• 6️⃣ патронов - 💀 100% смерть\n\n"
            f"Выбери количество патронов:")
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton(f"1️⃣ (x{GAME_SETTINGS['roulette']['1']})", callback_data="roulette_choice_1"),
         InlineKeyboardButton(f"2️⃣ (x{GAME_SETTINGS['roulette']['2']})", callback_data="roulette_choice_2"),
         InlineKeyboardButton(f"3️⃣ (x{GAME_SETTINGS['roulette']['3']})", callback_data="roulette_choice_3")],
        [InlineKeyboardButton(f"4️⃣ (x{GAME_SETTINGS['roulette']['4']})", callback_data="roulette_choice_4"),
         InlineKeyboardButton(f"5️⃣ (x{GAME_SETTINGS['roulette']['5']})", callback_data="roulette_choice_5"),
         InlineKeyboardButton("6️⃣ (💀)", callback_data="roulette_choice_6")],
        [InlineKeyboardButton("◀️ Назад", callback_data="casino_menu")]
    ])
    await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=kb)

async def play_dice(update, context, user_id):
    query = update.callback_query
    user = db.get_user(user_id)
    text = (f"🎲 *КОСТИ*\n\n"
            f"💰 Баланс: ${user[3]:.2f}\n\n"
            f"🎲 Режимы игры:\n"
            f"• 🔢 На число - x{GAME_SETTINGS['dice_number']['win_multiplier']} (шанс 1/6)\n"
            f"• 🔴 Чёт/Нечёт - x{GAME_SETTINGS['dice_even_odd']['win_multiplier']} (шанс 1/2)\n\n"
            f"Выбери режим игры:")
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton(f"🔢 НА ЧИСЛО (x{GAME_SETTINGS['dice_number']['win_multiplier']})", callback_data="dice_number_menu")],
        [InlineKeyboardButton(f"🟥 ЧЁТ / НЕЧЁТ (x{GAME_SETTINGS['dice_even_odd']['win_multiplier']})", callback_data="dice_even_odd_menu")],
        [InlineKeyboardButton("◀️ Назад", callback_data="casino_menu")]
    ])
    await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=kb)

async def play_dice_number(update, context, user_id):
    query = update.callback_query
    user = db.get_user(user_id)
    win_mult = GAME_SETTINGS['dice_number']['win_multiplier']
    text = (f"🎲 *КОСТИ - СТАВКА НА ЧИСЛО*\n\n"
            f"💰 Баланс: ${user[3]:.2f}\n\n"
            f"🎲 Шанс 1/6\n"
            f"• Любое число - x{win_mult}\n\n"
            f"Выбери число:")
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("1️⃣", callback_data="dice_num_1"),
         InlineKeyboardButton("2️⃣", callback_data="dice_num_2"),
         InlineKeyboardButton("3️⃣", callback_data="dice_num_3")],
        [InlineKeyboardButton("4️⃣", callback_data="dice_num_4"),
         InlineKeyboardButton("5️⃣", callback_data="dice_num_5"),
         InlineKeyboardButton("6️⃣", callback_data="dice_num_6")],
        [InlineKeyboardButton("◀️ Назад", callback_data="game_dice_classic")]
    ])
    await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=kb)

async def play_dice_even_odd(update, context, user_id):
    query = update.callback_query
    user = db.get_user(user_id)
    win_mult = GAME_SETTINGS['dice_even_odd']['win_multiplier']
    text = (f"🎲 *КОСТИ - ЧЁТ/НЕЧЁТ*\n\n"
            f"💰 Баланс: ${user[3]:.2f}\n\n"
            f"🎲 Шанс 1/2\n"
            f"• ✅ Чётное - x{win_mult}\n"
            f"• ❌ Нечётное - x{win_mult}\n\n"
            f"Выбери ставку:")
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton(f"✅ ЧЁТНОЕ (x{win_mult})", callback_data="dice_even"),
         InlineKeyboardButton(f"❌ НЕЧЁТНОЕ (x{win_mult})", callback_data="dice_odd")],
        [InlineKeyboardButton("◀️ Назад", callback_data="game_dice_classic")]
    ])
    await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=kb)

async def play_slots(update, context, user_id):
    query = update.callback_query
    user = db.get_user(user_id)
    mult1 = GAME_SETTINGS['slots']['1']
    mult2 = GAME_SETTINGS['slots']['2']
    mult3 = GAME_SETTINGS['slots']['3']
    text = (f"🎰 *СЛОТЫ*\n\n"
            f"💰 Баланс: ${user[3]:.2f}\n\n"
            f"🎰 Текущие коэффициенты:\n"
            f"• 1️⃣ одно совпадение - x{mult1}\n"
            f"• 2️⃣ два совпадения - x{mult2}\n"
            f"• 3️⃣ три совпадения - x{mult3}\n\n"
            f"Выбери на что ставишь:")
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton(f"1️⃣ ОДНО (x{mult1})", callback_data="slots_choice_1")],
        [InlineKeyboardButton(f"2️⃣ ДВА (x{mult2})", callback_data="slots_choice_2")],
        [InlineKeyboardButton(f"3️⃣ ТРИ (x{mult3})", callback_data="slots_choice_3")],
        [InlineKeyboardButton("◀️ Назад", callback_data="casino_menu")]
    ])
    await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=kb)

async def play_football(update, context, user_id):
    query = update.callback_query
    user = db.get_user(user_id)
    goal_mult = GAME_SETTINGS['football']['goal']
    miss_mult = GAME_SETTINGS['football']['miss']
    text = (f"⚽ *ФУТБОЛ*\n\n"
            f"💰 Баланс: ${user[3]:.2f}\n\n"
            f"⚽ Текущие коэффициенты:\n"
            f"• ⚽ ГОЛ - x{goal_mult} (шанс 1/3) - ВЫИГРЫШ\n"
            f"• 💨 МИМО - x{miss_mult} (шанс 2/3) - ВЫИГРЫШ если мимо\n\n"
            f"Выбери на что ставишь:")
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton(f"⚽ ГОЛ (x{goal_mult})", callback_data="football_goal"),
         InlineKeyboardButton(f"💨 МИМО (x{miss_mult})", callback_data="football_miss")],
        [InlineKeyboardButton("◀️ Назад", callback_data="casino_menu")]
    ])
    await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=kb)

async def play_basketball(update, context, user_id):
    query = update.callback_query
    user = db.get_user(user_id)
    point_mult = GAME_SETTINGS['basketball']['point']
    miss_mult = GAME_SETTINGS['basketball']['miss']
    text = (f"🏀 *БАСКЕТБОЛ*\n\n"
            f"💰 Баланс: ${user[3]:.2f}\n\n"
            f"🏀 Текущие коэффициенты:\n"
            f"• 🏀 ОЧКО - x{point_mult} (шанс 1/3) - ВЫИГРЫШ\n"
            f"• 💨 МИМО - x{miss_mult} (шанс 2/3) - ВЫИГРЫШ если мимо\n\n"
            f"Выбери на что ставишь:")
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton(f"🏀 ОЧКО (x{point_mult})", callback_data="basketball_point"),
         InlineKeyboardButton(f"💨 МИМО (x{miss_mult})", callback_data="basketball_miss")],
        [InlineKeyboardButton("◀️ Назад", callback_data="casino_menu")]
    ])
    await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=kb)

async def play_darts(update, context, user_id):
    query = update.callback_query
    user = db.get_user(user_id)
    bullseye_mult = GAME_SETTINGS['darts']['bullseye']
    miss_mult = GAME_SETTINGS['darts']['miss']
    text = (f"🎯 *ДАРТС*\n\n"
            f"💰 Баланс: ${user[3]:.2f}\n\n"
            f"🎯 Текущие коэффициенты:\n"
            f"• 🎯 В ЯБЛОЧКО - x{bullseye_mult} (шанс 1/6) - ВЫИГРЫШ\n"
            f"• 💨 МИМО - x{miss_mult} (шанс 5/6) - ВЫИГРЫШ если мимо\n\n"
            f"Выбери на что ставишь:")
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton(f"🎯 В ЯБЛОЧКО (x{bullseye_mult})", callback_data="darts_bullseye"),
         InlineKeyboardButton(f"💨 МИМО (x{miss_mult})", callback_data="darts_miss")],
        [InlineKeyboardButton("◀️ Назад", callback_data="casino_menu")]
    ])
    await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=kb)

async def play_bowling(update, context, user_id):
    query = update.callback_query
    user = db.get_user(user_id)
    strike_mult = GAME_SETTINGS['bowling']['strike']
    miss_mult = GAME_SETTINGS['bowling']['miss']
    text = (f"🎳 *БОУЛИНГ*\n\n"
            f"💰 Баланс: ${user[3]:.2f}\n\n"
            f"🎳 Текущие коэффициенты:\n"
            f"• 🎳 СТРАЙК - x{strike_mult} (шанс 1/6) - ВЫИГРЫШ\n"
            f"• 💨 МИМО - x{miss_mult} (шанс 5/6) - ВЫИГРЫШ если мимо\n\n"
            f"Выбери на что ставишь:")
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton(f"🎳 СТРАЙК (x{strike_mult})", callback_data="bowling_strike"),
         InlineKeyboardButton(f"💨 МИМО (x{miss_mult})", callback_data="bowling_miss")],
        [InlineKeyboardButton("◀️ Назад", callback_data="casino_menu")]
    ])
    await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=kb)

# ======================== ОБРАБОТКА ВЫБОРА ========================
async def handle_game_choice(update, context, user_id, data):
    query = update.callback_query
    if data.startswith('flip_choice_'):
        choice = data.replace('flip_choice_', '')
        context.user_data['game_type'] = 'flip'
        context.user_data['game_choice'] = choice
        user = db.get_user(user_id)
        text = (f"🪙 *ОРЁЛ И РЕШКА*\n\n"
                f"Твой выбор: {'🦅 ОРЁЛ' if choice == '1' else '🪙 РЕШКА'}\n"
                f"💰 Баланс: ${user[3]:.2f}\n\n"
                f"Введите сумму ставки (мин. 0.1$):")
        await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN)
        context.user_data['awaiting'] = 'bet_amount'
    elif data.startswith('roulette_choice_'):
        choice = data.replace('roulette_choice_', '')
        context.user_data['game_type'] = 'roulette'
        context.user_data['game_choice'] = choice
        user = db.get_user(user_id)
        mult = GAME_SETTINGS['roulette'].get(choice, 0)
        text = (f"💀 *РУССКАЯ РУЛЕТКА*\n\n"
                f"Патронов: {choice} (x{mult if mult>0 else '💀'})\n"
                f"💰 Баланс: ${user[3]:.2f}\n\n"
                f"Введите сумму ставки (мин. 0.1$):")
        await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN)
        context.user_data['awaiting'] = 'bet_amount'
    elif data.startswith('dice_num_'):
        num = data.replace('dice_num_', '')
        context.user_data['game_type'] = 'dice_num'
        context.user_data['game_choice'] = num
        user = db.get_user(user_id)
        mult = GAME_SETTINGS['dice_number']['win_multiplier']
        text = (f"🎲 *КОСТИ - ЧИСЛО {num}*\n\n"
                f"💰 Баланс: ${user[3]:.2f}\n\n"
                f"Введите сумму ставки (x{mult} если выпадет {num}):")
        await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN)
        context.user_data['awaiting'] = 'dice_bet'
    elif data == 'dice_even':
        context.user_data['game_type'] = 'dice_even_odd'
        context.user_data['game_choice'] = 'even'
        user = db.get_user(user_id)
        mult = GAME_SETTINGS['dice_even_odd']['win_multiplier']
        text = (f"🎲 *КОСТИ - ЧЁТНОЕ*\n\n"
                f"💰 Баланс: ${user[3]:.2f}\n\n"
                f"Введите сумму ставки (x{mult} если выпадет чётное):")
        await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN)
        context.user_data['awaiting'] = 'dice_bet'
    elif data == 'dice_odd':
        context.user_data['game_type'] = 'dice_even_odd'
        context.user_data['game_choice'] = 'odd'
        user = db.get_user(user_id)
        mult = GAME_SETTINGS['dice_even_odd']['win_multiplier']
        text = (f"🎲 *КОСТИ - НЕЧЁТНОЕ*\n\n"
                f"💰 Баланс: ${user[3]:.2f}\n\n"
                f"Введите сумму ставки (x{mult} если выпадет нечётное):")
        await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN)
        context.user_data['awaiting'] = 'dice_bet'
    elif data.startswith('slots_choice_'):
        choice = data.replace('slots_choice_', '')
        context.user_data['game_type'] = 'slots'
        context.user_data['game_choice'] = choice
        user = db.get_user(user_id)
        mult = GAME_SETTINGS['slots'].get(choice, 0)
        text = (f"🎰 *СЛОТЫ - {choice} СОВПАДЕНИЕ*\n\n"
                f"💰 Баланс: ${user[3]:.2f}\n\n"
                f"Введите сумму ставки (x{mult} если угадаешь):")
        await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN)
        context.user_data['awaiting'] = 'dice_bet'
    elif data == 'football_goal':
        context.user_data['game_type'] = 'football'
        context.user_data['game_choice'] = 'goal'
        user = db.get_user(user_id)
        mult = GAME_SETTINGS['football']['goal']
        text = (f"⚽ *ФУТБОЛ - ГОЛ*\n\n"
                f"💰 Баланс: ${user[3]:.2f}\n\n"
                f"Введите сумму ставки (x{mult} если будет гол):")
        await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN)
        context.user_data['awaiting'] = 'dice_bet'
    elif data == 'football_miss':
        context.user_data['game_type'] = 'football'
        context.user_data['game_choice'] = 'miss'
        user = db.get_user(user_id)
        mult = GAME_SETTINGS['football']['miss']
        text = (f"⚽ *ФУТБОЛ - МИМО*\n\n"
                f"💰 Баланс: ${user[3]:.2f}\n\n"
                f"⚠️ Если выпадет МИМО - ставка сгорает!\n\n"
                f"Введите сумму ставки (при голе - x{mult}, при мимо - проигрыш):")
        await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN)
        context.user_data['awaiting'] = 'dice_bet'
    elif data == 'basketball_point':
        context.user_data['game_type'] = 'basketball'
        context.user_data['game_choice'] = 'point'
        user = db.get_user(user_id)
        mult = GAME_SETTINGS['basketball']['point']
        text = (f"🏀 *БАСКЕТБОЛ - ОЧКО*\n\n"
                f"💰 Баланс: ${user[3]:.2f}\n\n"
                f"Введите сумму ставки (x{mult} если будет очко):")
        await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN)
        context.user_data['awaiting'] = 'dice_bet'
    elif data == 'basketball_miss':
        context.user_data['game_type'] = 'basketball'
        context.user_data['game_choice'] = 'miss'
        user = db.get_user(user_id)
        mult = GAME_SETTINGS['basketball']['miss']
        text = (f"🏀 *БАСКЕТБОЛ - МИМО*\n\n"
                f"💰 Баланс: ${user[3]:.2f}\n\n"
                f"⚠️ Если выпадет МИМО - ставка сгорает!\n\n"
                f"Введите сумму ставки (при очке - x{mult}, при мимо - проигрыш):")
        await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN)
        context.user_data['awaiting'] = 'dice_bet'
    elif data == 'darts_bullseye':
        context.user_data['game_type'] = 'darts'
        context.user_data['game_choice'] = 'bullseye'
        user = db.get_user(user_id)
        mult = GAME_SETTINGS['darts']['bullseye']
        text = (f"🎯 *ДАРТС - В ЯБЛОЧКО*\n\n"
                f"💰 Баланс: ${user[3]:.2f}\n\n"
                f"Введите сумму ставки (x{mult} если попадёшь):")
        await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN)
        context.user_data['awaiting'] = 'dice_bet'
    elif data == 'darts_miss':
        context.user_data['game_type'] = 'darts'
        context.user_data['game_choice'] = 'miss'
        user = db.get_user(user_id)
        mult = GAME_SETTINGS['darts']['miss']
        text = (f"🎯 *ДАРТС - МИМО*\n\n"
                f"💰 Баланс: ${user[3]:.2f}\n\n"
                f"⚠️ Если выпадет МИМО - ставка сгорает!\n\n"
                f"Введите сумму ставки (при попадании - x{mult}, при мимо - проигрыш):")
        await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN)
        context.user_data['awaiting'] = 'dice_bet'
    elif data == 'bowling_strike':
        context.user_data['game_type'] = 'bowling'
        context.user_data['game_choice'] = 'strike'
        user = db.get_user(user_id)
        mult = GAME_SETTINGS['bowling']['strike']
        text = (f"🎳 *БОУЛИНГ - СТРАЙК*\n\n"
                f"💰 Баланс: ${user[3]:.2f}\n\n"
                f"Введите сумму ставки (x{mult} если страйк):")
        await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN)
        context.user_data['awaiting'] = 'dice_bet'
    elif data == 'bowling_miss':
        context.user_data['game_type'] = 'bowling'
        context.user_data['game_choice'] = 'miss'
        user = db.get_user(user_id)
        mult = GAME_SETTINGS['bowling']['miss']
        text = (f"🎳 *БОУЛИНГ - МИМО*\n\n"
                f"💰 Баланс: ${user[3]:.2f}\n\n"
                f"⚠️ Если выпадет МИМО - ставка сгорает!\n\n"
                f"Введите сумму ставки (при страйке - x{mult}, при мимо - проигрыш):")
        await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN)
        context.user_data['awaiting'] = 'dice_bet'

# ======================== ОБРАБОТКА СТАВКИ ========================
async def handle_bet(update, context, user_id, bet):
    game_type = context.user_data.get('game_type')
    game_choice = context.user_data.get('game_choice')
    if not game_type:
        await update.message.reply_text("❌ Ошибка игры. Попробуйте снова.")
        return
    await check_balance_and_offer(
        update, context, user_id, bet,
        action_callback="game_confirm",
        success_message=f"Подтверждение ставки\n\nСтавка: ${bet:.2f}",
        game_data={'bet': bet}
    )

# ======================== ОБРАБОТКА РЕЗУЛЬТАТА ========================
async def process_game_result(update, context, user_id, bet, game_type, game_choice):
    query = update.callback_query
    user = db.get_user(user_id)
    win = 0.0
    multiplier = 0.0
    result_text = ""

    if game_type == 'flip':
        result = random.choice(['1', '2'])
        if result == game_choice:
            multiplier = GAME_SETTINGS['flip']['win_multiplier']
            win = bet * multiplier
            result_text = f"🎉 Ты угадал! Выпал {'🦅 ОРЁЛ' if result == '1' else '🪙 РЕШКА'}"
        else:
            result_text = f"😢 Не угадал. Выпал {'🦅 ОРЁЛ' if result == '1' else '🪙 РЕШКА'}"
    elif game_type == 'roulette':
        result = random.randint(1, 6)
        choice_num = int(game_choice)
        if result <= choice_num:
            result_text = f"💥 БАХ! Патрон был в позиции {result}"
        else:
            multiplier = GAME_SETTINGS['roulette'].get(game_choice, 0)
            win = bet * multiplier
            result_text = f"🎉 Ты выжил! Выпал номер {result}"
    elif game_type == 'dice_num':
        msg = await context.bot.send_dice(chat_id=user_id, emoji='🎲')
        res = msg.dice.value
        if str(res) == game_choice:
            multiplier = GAME_SETTINGS['dice_number']['win_multiplier']
            win = bet * multiplier
            result_text = f"🎉 Точное попадание! Выпало {res}"
        else:
            result_text = f"😢 Не угадал. Выпало {res}"
    elif game_type == 'dice_even_odd':
        msg = await context.bot.send_dice(chat_id=user_id, emoji='🎲')
        res = msg.dice.value
        is_even = res % 2 == 0
        if (game_choice == 'even' and is_even) or (game_choice == 'odd' and not is_even):
            multiplier = GAME_SETTINGS['dice_even_odd']['win_multiplier']
            win = bet * multiplier
            result_text = f"🎉 Угадал! Выпало {'чётное' if is_even else 'нечётное'} число {res}"
        else:
            result_text = f"😢 Не угадал. Выпало {'чётное' if is_even else 'нечётное'} число {res}"
    elif game_type == 'slots':
        msg = await context.bot.send_dice(chat_id=user_id, emoji='🎰')
        res = msg.dice.value
        if res == 64:
            matches = 3
        elif res == 43 or res == 22:
            matches = 2
        else:
            matches = 1
        if matches == int(game_choice):
            multiplier = GAME_SETTINGS['slots'].get(game_choice, 0)
            win = bet * multiplier
            result_text = f"🎉 Угадал! {matches} совпадения"
        else:
            result_text = f"😢 Не угадал. Выпало {matches} совпадения"
    elif game_type == 'football':
        msg = await context.bot.send_dice(chat_id=user_id, emoji='⚽')
        res = msg.dice.value
        if game_choice == 'goal':
            if res == 4:
                multiplier = GAME_SETTINGS['football']['goal']
                win = bet * multiplier
                result_text = f"⚽ ГОЛ! Ты выиграл! x{multiplier}"
            else:
                result_text = f"😢 НЕТ ГОЛА! Ты проиграл!"
        else:
            if res != 4:
                multiplier = GAME_SETTINGS['football']['miss']
                win = bet * multiplier
                result_text = f"💨 МИМО! Ты выиграл! x{multiplier}"
            else:
                result_text = f"😢 ГОЛ! Ты проиграл (ставил на МИМО)"
    elif game_type == 'basketball':
        msg = await context.bot.send_dice(chat_id=user_id, emoji='🏀')
        res = msg.dice.value
        if game_choice == 'point':
            if res == 4:
                multiplier = GAME_SETTINGS['basketball']['point']
                win = bet * multiplier
                result_text = f"🏀 ОЧКО! Ты выиграл! x{multiplier}"
            else:
                result_text = f"😢 НЕТ ОЧКА! Ты проиграл!"
        else:
            if res != 4:
                multiplier = GAME_SETTINGS['basketball']['miss']
                win = bet * multiplier
                result_text = f"💨 МИМО! Ты выиграл! x{multiplier}"
            else:
                result_text = f"😢 ОЧКО! Ты проиграл (ставил на МИМО)"
    elif game_type == 'darts':
        msg = await context.bot.send_dice(chat_id=user_id, emoji='🎯')
        res = msg.dice.value
        if game_choice == 'bullseye':
            if res == 6:
                multiplier = GAME_SETTINGS['darts']['bullseye']
                win = bet * multiplier
                result_text = f"🎯 В ЯБЛОЧКО! Ты выиграл! x{multiplier}"
            else:
                result_text = f"😢 МИМО! Ты проиграл!"
        else:
            if res != 6:
                multiplier = GAME_SETTINGS['darts']['miss']
                win = bet * multiplier
                result_text = f"💨 МИМО! Ты выиграл! x{multiplier}"
            else:
                result_text = f"😢 В ЯБЛОЧКО! Ты проиграл (ставил на МИМО)"
    elif game_type == 'bowling':
        msg = await context.bot.send_dice(chat_id=user_id, emoji='🎳')
        res = msg.dice.value
        if game_choice == 'strike':
            if res == 5:
                multiplier = GAME_SETTINGS['bowling']['strike']
                win = bet * multiplier
                result_text = f"🎳 СТРАЙК! Ты выиграл! x{multiplier}"
            else:
                result_text = f"😢 МИМО! Ты проиграл!"
        else:
            if res != 5:
                multiplier = GAME_SETTINGS['bowling']['miss']
                win = bet * multiplier
                result_text = f"💨 МИМО! Ты выиграл! x{multiplier}"
            else:
                result_text = f"😢 СТРАЙК! Ты проиграл (ставил на МИМО)"

    if win > 0:
        db.update_balance(user_id, win)
        new_balance = user[3] - bet + win
        text = (f"🎉 *ВЫИГРАЛ!*\n\n"
               f"{result_text}\n\n"
               f"💰 Ставка: ${bet:.2f}\n"
               f"💵 Выигрыш: ${win:.2f} (x{multiplier})\n"
               f"💳 Баланс: ${new_balance:.2f}")
    else:
        db.add_lost_stars(user_id, bet)
        new_balance = user[3] - bet
        snow_gain = int(bet * 0.5 * 10)
        text = (f"😢 *ПРОИГРЫШ*\n\n"
               f"{result_text}\n\n"
               f"💰 Ставка: ${bet:.2f} сгорела\n"
               f"✨ Снежинки: +{snow_gain} ✨\n"
               f"💳 Баланс: ${new_balance:.2f}")

    if game_type in ['dice_num', 'dice_even_odd']:
        base_game = 'dice_classic'
    elif game_type in ['flip', 'roulette']:
        base_game = game_type
    else:
        base_game = game_type
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🎮 Играть еще", callback_data=f"game_{base_game}"),
         InlineKeyboardButton("🎰 В казино", callback_data="casino_menu")],
        [InlineKeyboardButton("🏠 Главное меню", callback_data="main_menu")]
    ])
    await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=kb)

# ======================== МИННОЕ ПОЛЕ ========================
class MinesGame:
    def __init__(self, bet, mines_count=5):
        self.bet = bet
        self.mines_count = mines_count
        self.total_cells = 25
        self.mines = random.sample(range(self.total_cells), mines_count)
        self.opened = []
        self.multiplier = 1.0
        self.game_over = False

    def open_cell(self, pos):
        if pos in self.opened or self.game_over:
            return {'result': 'invalid', 'win': 0}
        if pos in self.mines:
            self.game_over = True
            return {'result': 'lose', 'win': 0}
        self.opened.append(pos)
        self.multiplier = 1.0 + 0.1 * len(self.opened)
        win = self.bet * self.multiplier
        if len(self.opened) == self.total_cells - self.mines_count:
            self.game_over = True
            return {'result': 'win', 'win': win}
        return {'result': 'continue', 'win': win, 'multiplier': self.multiplier}

    def cashout(self):
        self.game_over = True
        return self.bet * self.multiplier

async def show_mines_field(update, context, game):
    kb = []
    for i in range(0, 25, 5):
        row = []
        for j in range(5):
            idx = i + j
            if idx in game.opened:
                row.append(InlineKeyboardButton("✅", callback_data="noop"))
            else:
                row.append(InlineKeyboardButton(f"{idx+1}", callback_data=f"mines_open_{idx}"))
        kb.append(row)
    kb.append([InlineKeyboardButton("💰 Забрать", callback_data="mines_cashout")])
    kb.append([InlineKeyboardButton("◀️ Назад", callback_data="casino_menu")])
    text = (f"💣 Минное поле\n💰 Ставка: ${game.bet:.2f}\n"
            f"📈 Множитель: x{game.multiplier:.2f}\n"
            f"✅ Открыто: {len(game.opened)}/{25-game.mines_count}")
    if update.message:
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(kb))
    else:
        await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb))

# ======================== КЛАСС ДЛЯ ОБРАБОТКИ ПОПОЛНЕНИЙ ========================
class DepositHandler:
    @staticmethod
    async def request_amount(update, context, user_id, method):
        text = ("💎 *Пополнение через CryptoBot (TON)*\n\n"
                "Введите сумму пополнения в долларах ($)\n"
                f"Минимальная сумма: ${MIN_DEPOSIT_DOLLARS:.2f}\n"
                f"Максимальная сумма: ${MAX_DEPOSIT_DOLLARS:.2f}\n"
                f"Курс: 1 TON = {DOLLAR_PER_TON:.2f}$\n\n"
                "Пример: `10`, `25.5`, `100`")
        context.user_data['deposit_method'] = method
        context.user_data['awaiting'] = 'deposit_amount_crypto'
        if isinstance(update, Update) and update.callback_query:
            await update.callback_query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=back_button("deposit_menu"))
        else:
            await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=back_button("deposit_menu"))

    @staticmethod
    async def process_amount(update, context, user_id, text, method):
        try:
            text = text.replace(',', '.')
            amount_dollars = float(text)
            if amount_dollars < MIN_DEPOSIT_DOLLARS:
                await update.message.reply_text(f"❌ Минимальная сумма: ${MIN_DEPOSIT_DOLLARS:.2f}")
                return False
            if amount_dollars > MAX_DEPOSIT_DOLLARS:
                await update.message.reply_text(f"❌ Максимальная сумма: ${MAX_DEPOSIT_DOLLARS:.2f}")
                return False
            await DepositHandler.create_crypto_invoice(update, context, user_id, amount_dollars)
            return True
        except ValueError:
            await update.message.reply_text("❌ Введите число (например: 10, 25.5, 100)")
            return False

    @staticmethod
    async def create_crypto_invoice(update, context, user_id, amount_dollars):
        invoice = crypto.create_invoice(amount_dollars, "TON", f"Пополнение {BOT_NAME} на ${amount_dollars:.2f}")
        if invoice:
            db.add_payment(user_id, amount_dollars, 'crypto', invoice['invoice_id'], 'pending')
            text = (f"💎 *Счёт создан*\n\n"
                   f"Сумма: ${amount_dollars:.2f}\n"
                   f"К оплате: {invoice['amount']} TON\n\n"
                   f"[💳 Перейти к оплате]({invoice['pay_url']})\n\n"
                   f"Счёт действителен 1 час")
            if update.message:
                await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN, disable_web_page_preview=True, reply_markup=back_button("deposit_menu"))
        else:
            await update.message.reply_text("❌ Ошибка создания счёта. Попробуйте позже.")
            # ======================== СТАРТ ========================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_ban(update, context):
        return
    user = update.effective_user
    user_id = user.id
    ref = None
    if context.args and context.args[0].startswith('ref'):
        try:
            ref = int(context.args[0].replace('ref', ''))
        except:
            pass
    db.create_user(user_id, user.username, user.first_name, ref)
    u = db.get_user(user_id)

    keyboard_rows = [
        [InlineKeyboardButton("🎰 Казино", callback_data="casino_menu"),
         InlineKeyboardButton("📦 Кейс", callback_data="case_menu")],
        [InlineKeyboardButton("❄️ Зимний магазин", callback_data="winter_shop"),
         InlineKeyboardButton("🎁 Бонус", callback_data="daily_bonus")],
        [InlineKeyboardButton("👥 Рефералы", callback_data="referral"),
         InlineKeyboardButton("👤 Профиль", callback_data="profile")],
        [InlineKeyboardButton("💰 Пополнить", callback_data="deposit_menu"),
         InlineKeyboardButton("💸 Вывод", callback_data="withdraw_menu")],
        [InlineKeyboardButton("🎟️ Промокод", callback_data="activate_promo"),
         InlineKeyboardButton("📦 Инвентарь", callback_data="inventory")],
        [InlineKeyboardButton("🎟️ Лотерея", callback_data="lottery"),
         InlineKeyboardButton("📜 Правила", callback_data="rules")]
    ]
    if user_id in ADMIN_IDS:
        keyboard_rows.append([InlineKeyboardButton("⚙️ Админ-панель", callback_data="admin_panel")])

    kb = InlineKeyboardMarkup(keyboard_rows)
    text = (f"🌟 Добро пожаловать в {BOT_NAME}!\n\n"
            f"🆔 ID: {user_id}\n"
            f"👤 Имя: {user.first_name}\n"
            f"💰 Баланс: ${u[3]:.2f}\n"
            f"❄️ Снежинки: {u[4]} ✨")
    if WELCOME_IMAGE_ID:
        await update.message.reply_photo(photo=WELCOME_IMAGE_ID, caption=text, parse_mode=ParseMode.MARKDOWN, reply_markup=kb)
    else:
        await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=kb)

# ======================== ОБРАБОТЧИК КНОПОК ========================
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    user = db.get_user(user_id)
    if not user:
        await query.edit_message_text("❌ Ошибка")
        return
    if user[11] == 1 and user_id not in ADMIN_IDS:
        await query.edit_message_text("❌ Вы заблокированы")
        return
    if 'game_start_time' in context.user_data:
        if time.time() - context.user_data['game_start_time'] > 600:
            context.user_data.clear()
    data = query.data

    # ---------- ПРОФИЛЬ ----------
    if data == "profile":
        stats = db.get_user_stats(user_id)
        wd = db.get_user_withdrawals(user_id)
        text = (f"👤 Профиль\n\n"
                f"🆔 ID: {user_id}\n"
                f"👤 Имя: {user[2]}\n"
                f"📛 Username: @{user[1] or 'нет'}\n"
                f"💰 Баланс: ${user[3]:.2f}\n"
                f"❄️ Снежинки: {user[4]} ✨\n"
                f"👥 Рефералов: {user[5]}\n\n"
                f"📊 Статистика игр:\n"
                f"• Всего игр: {stats[0] or 0}\n"
                f"• Выиграно: {stats[1] or 0}\n"
                f"• Проиграно: {stats[2] or 0}\n"
                f"• Сумма ставок: ${stats[3] or 0:.2f}\n\n"
                f"📋 Последние выводы:\n")
        if wd:
            for w in wd:
                emoji = {"pending":"⏳","approved":"✅","completed":"✔️","rejected":"❌"}.get(w[3],"❓")
                text += f"{emoji} ${w[1]:.2f} — {w[2]}\n"
        else:
            text += "Пока нет выводов"
        await edit_message(query, text, back_button())

    # ---------- ПРАВИЛА ----------
    elif data == "rules":
        text = (f"📜 *Правила использования бота {BOT_NAME}*\n\n"
                f"🚫 *Запрещено:*\n"
                f"• Использование ботов для накрутки\n"
                f"• Создание мультиаккаунтов\n"
                f"• Обман системы реферальной программы\n"
                f"• Попытки обмана администрации\n\n"
                f"✅ *Разрешено:*\n"
                f"• Приглашать реальных друзей\n"
                f"• Активно участвовать в проекте\n"
                f"• Соблюдать правила каналов\n\n"
                f"⚡ *Нарушение правил ведет к:*\n"
                f"• Блокировке аккаунта\n"
                f"• Обнулению баланса\n"
                f"• Запрету на участие в проекте\n\n"
                f"👑 *Важно:* Администрация оставляет за собой право блокировать "
                f"пользователей без объяснения причин при подозрении в мошенничестве.\n\n"
                f"🎉 *Удачной игры!*")
        await edit_message(query, text, back_button("main_menu"))

    # ---------- КАЗИНО ----------
    elif data == "casino_menu":
        text = "🎰 Казино\n\nВыберите игру:"
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🪙 ОРЁЛ/РЕШКА", callback_data="game_flip"),
             InlineKeyboardButton("💀 РУССКАЯ РУЛЕТКА", callback_data="game_roulette")],
            [InlineKeyboardButton("🎰 СЛОТЫ", callback_data="game_slots"),
             InlineKeyboardButton("💣 МИННОЕ ПОЛЕ", callback_data="game_mines")],
            [InlineKeyboardButton("🎲 КОСТИ", callback_data="game_dice_classic"),
             InlineKeyboardButton("⚽ ФУТБОЛ", callback_data="game_football")],
            [InlineKeyboardButton("🏀 БАСКЕТБОЛ", callback_data="game_basketball"),
             InlineKeyboardButton("🎯 ДАРТС", callback_data="game_darts")],
            [InlineKeyboardButton("🎳 БОУЛИНГ", callback_data="game_bowling")],
            [InlineKeyboardButton("📜 Правила", callback_data="rules"),
             InlineKeyboardButton("◀️ Назад", callback_data="main_menu")]
        ])
        await edit_message(query, text, kb)

    # ---------- ЗАПУСК ИГР ----------
    elif data == "game_flip":
        await play_flip(update, context, user_id)
    elif data == "game_roulette":
        await play_roulette(update, context, user_id)
    elif data == "game_slots":
        await play_slots(update, context, user_id)
    elif data == "game_dice_classic":
        await play_dice(update, context, user_id)
    elif data == "dice_number_menu":
        await play_dice_number(update, context, user_id)
    elif data == "dice_even_odd_menu":
        await play_dice_even_odd(update, context, user_id)
    elif data == "game_football":
        await play_football(update, context, user_id)
    elif data == "game_basketball":
        await play_basketball(update, context, user_id)
    elif data == "game_darts":
        await play_darts(update, context, user_id)
    elif data == "game_bowling":
        await play_bowling(update, context, user_id)

    elif data.startswith("flip_choice_") or data.startswith("roulette_choice_") or \
         data.startswith("dice_num_") or data in ["dice_even", "dice_odd"] or \
         data.startswith("slots_choice_") or \
         data in ["football_goal", "football_miss", "basketball_point", "basketball_miss",
                  "darts_bullseye", "darts_miss", "bowling_strike", "bowling_miss"]:
        await handle_game_choice(update, context, user_id, data)

    # ---------- МИННОЕ ПОЛЕ ----------
    elif data == "game_mines":
        text = "💣 Минное поле\n\nВыберите количество мин:"
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("3 мины (x1.2)", callback_data="mines_set_3"),
             InlineKeyboardButton("4 мины (x1.45)", callback_data="mines_set_4"),
             InlineKeyboardButton("5 мин (x1.75)", callback_data="mines_set_5")],
            [InlineKeyboardButton("6 мин (x2.2)", callback_data="mines_set_6"),
             InlineKeyboardButton("7 мин (x2.8)", callback_data="mines_set_7"),
             InlineKeyboardButton("8 мин (x4.0)", callback_data="mines_set_8")],
            [InlineKeyboardButton("◀️ Назад", callback_data="casino_menu")]
        ])
        await edit_message(query, text, kb)

    elif data.startswith("mines_set_"):
        mines = int(data.replace("mines_set_", ""))
        context.user_data['mines_count'] = mines
        text = f"💣 Минное поле\n\nМин: {mines}\n\nВведите сумму ставки (мин. 0.1$):"
        await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN)
        context.user_data['awaiting'] = 'mines_bet'

    elif data.startswith("mines_open_"):
        pos = int(data.replace("mines_open_", ""))
        game = context.user_data.get('mines_game')
        if not game:
            await edit_message(query, "❌ Игра не найдена или истекло время сессии")
            return
        res = game.open_cell(pos)
        if res['result'] == 'lose':
            db.add_lost_stars(user_id, game.bet)
            await edit_message(query, f"💥 БАБАХ!\n💰 Ставка ${game.bet:.2f} проиграна\n✨ +{int(game.bet*0.5*10)} ✨")
            context.user_data.pop('mines_game', None)
        elif res['result'] == 'win':
            db.update_balance(user_id, res['win'])
            await edit_message(query, f"🎉 ТЫ ВЫИГРАЛ ВСЁ ПОЛЕ!\n💰 Выигрыш: ${res['win']:.2f}")
            context.user_data.pop('mines_game', None)
        elif res['result'] == 'continue':
            await show_mines_field(update, context, game)
        else:
            await edit_message(query, "❌ Неверный ход")

    elif data == "mines_cashout":
        game = context.user_data.get('mines_game')
        if game:
            win = game.cashout()
            db.update_balance(user_id, win)
            await edit_message(query, f"💰 Забрал выигрыш\n💵 ${win:.2f}")
            context.user_data.pop('mines_game', None)
        else:
            await edit_message(query, "❌ Игра не найдена")

    # ---------- ПОДТВЕРЖДЕНИЕ СТАВКИ ----------
    elif data == "game_confirm":
        game_data = context.user_data.get('game_data')
        if not game_data:
            await edit_message(query, "❌ Ошибка. Начните игру заново.", back_button("casino_menu"))
            return
        bet = game_data['bet']
        game_type = context.user_data.get('game_type')
        game_choice = context.user_data.get('game_choice')
        current_user = db.get_user(user_id)
        if not current_user or current_user[3] < bet:
            await edit_message(query, "❌ Недостаточно средств. Пополните баланс.", home_button())
            return
        db.update_balance(user_id, -bet)
        await process_game_result(update, context, user_id, bet, game_type, game_choice)
        context.user_data.pop('game_data', None)

    # ---------- КЕЙС ----------
    elif data == "case_menu":
        case = db.get_cases()[0]
        text = (f"📦 Кейс {BOT_NAME}\n\n"
                f"💰 Цена: ${case[2]:.2f}\n\n"
                f"Шансы:\n"
                f"❤️ Сердце (60%) — $1.5\n"
                f"🌹 Роза (17%) — $2.5\n"
                f"🚀 Ракета (7%) — $5.0\n"
                f"🌸 Цветы (7%) — $5.0\n"
                f"💍 Кольцо (3%) — $10.0\n"
                f"💎 Алмаз (1.5%) — $10.0\n"
                f"🍭 Lol pop (1%) — $32.5 (NFT)\n"
                f"🐕 Snoop Dogg (1%) — $42.5 (NFT)")
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton(f"📦 Открыть за ${case[2]:.2f} (баланс)", callback_data="open_case_balance")],
            [InlineKeyboardButton("◀️ Назад", callback_data="main_menu")]
        ])
        if CASE_IMAGE_ID:
            try:
                from telegram import InputMediaPhoto
                await query.edit_message_media(media=InputMediaPhoto(media=CASE_IMAGE_ID, caption=text, parse_mode=ParseMode.MARKDOWN), reply_markup=kb)
            except:
                await edit_message(query, text, kb)
        else:
            await edit_message(query, text, kb)

    elif data == "open_case_balance":
        case_price = 3.5
        await check_balance_and_offer(
            update, context, user_id, case_price,
            action_callback="confirm_open_case",
            success_message="🎁 Подтвердите открытие кейса"
        )

    elif data == "confirm_open_case":
        case_price = 3.5
        current_user = db.get_user(user_id)
        if current_user[3] < case_price:
            await check_balance_and_offer(update, context, user_id, case_price, "confirm_open_case", "🎁 Открыть кейс")
            return
        db.update_balance(user_id, -case_price)
        res = db.open_case(1, user_id)
        if res:
            if res['type'] == 'nft':
                text = (f"🎉 Поздравляем!\n\nВы выиграли NFT: {res['name']} (стоимость ${res['value']:.2f}).\n"
                        f"NFT сохранён в инвентаре.")
                kb = InlineKeyboardMarkup([
                    [InlineKeyboardButton("📤 Вывести", callback_data=f"withdraw_nft_{res['name']}")],
                    [InlineKeyboardButton("◀️ Назад", callback_data="case_menu")]
                ])
            else:
                db.update_balance(user_id, res['value'])
                text = f"🎉 Поздравляем!\n\nВы выиграли: {res['name']}\n💰 ${res['value']:.2f} зачислено на баланс!"
                kb = back_button("case_menu")
            await edit_message(query, text, kb)
        else:
            await edit_message(query, "❌ Ошибка открытия кейса")

    # ---------- ИНВЕНТАРЬ ----------
    elif data == "inventory":
        inv = db.get_inventory(user_id)
        if not inv:
            text = "📦 Инвентарь пуст"
            kb = back_button("main_menu")
        else:
            text = "📦 Твои NFT:\n\n"
            kb_rows = []
            for it in inv:
                text += f"• {it[0]} — ${it[1]:.2f}\n"
                kb_rows.append([InlineKeyboardButton(f"📤 Вывести {it[0]}", callback_data=f"withdraw_nft_{it[0]}")])
            kb_rows.append([InlineKeyboardButton("◀️ Назад", callback_data="main_menu")])
            kb = InlineKeyboardMarkup(kb_rows)
        await edit_message(query, text, kb)

    elif data.startswith("withdraw_nft_"):
        nft_name = data.replace("withdraw_nft_", "")
        cases = db.get_cases()
        items = json.loads(cases[0][3])
        price = None
        for it in items:
            if it['name'] == nft_name and it['type'] == 'nft':
                price = it['value']
                break
        if not price:
            shop = db.get_shop_items()
            for it in shop:
                if it[0] == nft_name:
                    price = it[1]
                    break
        if price:
            wid = db.create_nft_withdrawal(user_id, nft_name, price)
            for aid in ADMIN_IDS:
                kb = InlineKeyboardMarkup([
                    [InlineKeyboardButton(f"✅ Принять #{wid}", callback_data=f"approve_nft_{wid}"),
                     InlineKeyboardButton(f"❌ Отклонить #{wid}", callback_data=f"reject_nft_{wid}")]
                ])
                await context.bot.send_message(
                    aid,
                    f"🖼️ Заявка на вывод NFT\n\n👤 @{user[1] or user_id}\n🎁 {nft_name}\n💰 ${price:.2f}\n🆔 #{wid}",
                    reply_markup=kb
                )
            await edit_message(query, f"✅ Заявка #{wid} отправлена на вывод.")
        else:
            await edit_message(query, "❌ NFT не найден")

    # ---------- ЗИМНИЙ МАГАЗИН ----------
    elif data == "winter_shop":
        items = db.get_shop_items()
        text = f"❄️ Зимний магазин\n\nВаши снежинки: {user[4]} ✨\n\nДоступно:\n"
        for name, price in items:
            text += f"• {name} — {price} ✨\n"
        text += "\n❄️ За проигрыши +0.5 ✨, за рефералов +5 ✨"
        kb_rows = []
        for name, price in items:
            kb_rows.append([InlineKeyboardButton(f"🎁 {name}", callback_data=f"buy_{name}")])
        kb_rows.append([InlineKeyboardButton("◀️ Назад", callback_data="main_menu")])
        kb = InlineKeyboardMarkup(kb_rows)
        await edit_message(query, text, kb)

    elif data.startswith("buy_"):
        item_name = data.replace("buy_", "")
        items = db.get_shop_items()
        price = None
        for n, p in items:
            if n == item_name:
                price = p
                break
        if not price:
            await edit_message(query, "❌ Товар не найден")
            return
        if user[4] >= price:
            db.update_snowflakes(user_id, -price)
            wid = db.create_nft_withdrawal(user_id, item_name, price)
            for aid in ADMIN_IDS:
                kb = InlineKeyboardMarkup([
                    [InlineKeyboardButton(f"✅ Принять #{wid}", callback_data=f"approve_nft_{wid}"),
                     InlineKeyboardButton(f"❌ Отклонить #{wid}", callback_data=f"reject_nft_{wid}")]
                ])
                await context.bot.send_message(
                    aid,
                    f"🖼️ Новая покупка NFT\n\n👤 @{user[1] or user_id}\n🎁 {item_name}\n❄️ {price} ✨\n🆔 #{wid}",
                    reply_markup=kb
                )
            await edit_message(query, f"✅ Куплено! Заявка #{wid} отправлена на вывод.")
        else:
            await edit_message(query, f"❌ Не хватает {price - user[4]} ✨", back_button("winter_shop"))

    # ---------- РЕФЕРАЛЫ ----------
    elif data == "referral":
        link = f"https://t.me/{BOT_USERNAME}?start=ref{user_id}"
        text = (f"👥 Рефералы\n\n"
                f"🔗 `{link}`\n\n"
                f"Приглашено: {user[5]}\n"
                f"Заработано: {user[5] * 5} ✨\n\n"
                f"За каждого друга +5 ✨")
        await edit_message(query, text, back_button("main_menu"))

    # ---------- БОНУС ----------
    elif data == "daily_bonus":
        bonus = db.check_daily_bonus(user_id)
        if bonus > 0:
            text = f"🎁 +${bonus:.2f}"
        else:
            text = "❌ Бонус уже получен сегодня"
        await edit_message(query, text, home_button())

    # ---------- ПРОМОКОД ----------
    elif data == "activate_promo":
        context.user_data['awaiting'] = 'promocode'
        await edit_message(query, "🎟️ Введите промокод:")

    # ---------- ЛОТЕРЕЯ ----------
    elif data == "lottery":
        stats = db.get_lottery_stats()
        if not stats:
            await edit_message(query, "❌ Ошибка лотереи", back_button("main_menu"))
            return
        total_sold = stats[1]
        remaining = 100 - total_sold
        user_tickets = db.get_user_tickets(user_id)
        user_tickets_count = len(user_tickets)
        text = (f"🎟️ *ЛОТЕРЕЯ*\n\n"
                f"🏆 Призовой фонд: ${stats[2]:.2f}\n"
                f"💰 Цена билета: $0.30\n"
                f"📊 Продано билетов: {total_sold}/100\n"
                f"🎫 Осталось билетов: {remaining}\n"
                f"👤 Ваши билеты: {user_tickets_count}\n\n"
                f"Купи билет и выиграй ${stats[2]:.2f}!")
        if user_tickets_count > 0:
            tickets_list = ", ".join([str(t[0]) for t in user_tickets[:5]])
            if user_tickets_count > 5:
                tickets_list += f" и еще {user_tickets_count - 5}"
            text += f"\n\n🎫 Ваши номера: {tickets_list}"
        kb_rows = []
        if remaining > 0:
            kb_rows.append([InlineKeyboardButton("🎫 Купить 1 билет ($0.30)", callback_data="lottery_buy_1")])
            kb_rows.append([InlineKeyboardButton("🎫 Купить 5 билетов ($1.50)", callback_data="lottery_buy_5")])
            kb_rows.append([InlineKeyboardButton("🎫 Купить 10 билетов ($3.00)", callback_data="lottery_buy_10")])
        else:
            text += "\n\n🎉 Все билеты проданы! Ожидайте розыгрыша..."
        kb_rows.append([InlineKeyboardButton("🔄 Обновить", callback_data="lottery")])
        kb_rows.append([InlineKeyboardButton("◀️ Назад", callback_data="main_menu")])
        kb = InlineKeyboardMarkup(kb_rows)
        await edit_message(query, text, kb)

    elif data.startswith("lottery_buy_"):
        count = int(data.replace("lottery_buy_", ""))
        price = count * 0.30
        if user[3] < price:
            missing = price - user[3]
            text = (f"❌ Недостаточно средств!\n\n"
                    f"Стоимость: ${price:.2f}\n"
                    f"У вас: ${user[3]:.2f}\n"
                    f"Не хватает: ${missing:.2f}\n\n"
                    f"Пополнить сейчас?")
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton(f"💰 Пополнить ${missing:.2f}", callback_data="deposit_crypto")],
                [InlineKeyboardButton("◀️ Назад", callback_data="lottery")]
            ])
            await edit_message(query, text, kb)
            return
        result = db.buy_lottery_ticket(user_id, count)
        if result['success']:
            db.update_balance(user_id, -price)
            for aid in ADMIN_IDS:
                try:
                    tickets_list = ", ".join([str(t) for t in result['tickets']])
                    await context.bot.send_message(
                        aid,
                        f"🎟️ *Новая покупка билетов*\n\n"
                        f"👤 Пользователь: @{user[1] or user_id}\n"
                        f"🎫 Куплено билетов: {count}\n"
                        f"💰 Сумма: ${price:.2f}\n"
                        f"🎟️ Номера: {tickets_list}\n"
                        f"📊 Продано всего: {db.get_lottery_stats()[1]}/100",
                        parse_mode=ParseMode.MARKDOWN
                    )
                except:
                    pass
            tickets_list = ", ".join([str(t) for t in result['tickets']])
            text = (f"✅ *Билеты куплены!*\n\n"
                    f"🎫 Куплено билетов: {count}\n"
                    f"💰 Списано: ${price:.2f}\n"
                    f"🎟️ Ваши номера: {tickets_list}\n\n"
                    f"🍀 Удачи в розыгрыше!")
            stats = db.get_lottery_stats()
            if stats[1] >= 100:
                winner_result = db.draw_lottery()
                if winner_result:
                    winner_user = db.get_user(winner_result['winner_id'])
                    winner_name = winner_user[2] if winner_user else f"ID: {winner_result['winner_id']}"
                    text_winner = (f"🎉 *РОЗЫГРЫШ ЛОТЕРЕИ!*\n\n"
                                  f"🏆 Победитель: {winner_name}\n"
                                  f"🎫 Выигрышный билет: {winner_result['winner_ticket']}\n"
                                  f"💰 Приз: ${winner_result['prize']:.2f}\n\n"
                                  f"Поздравляем!")
                    users = db.get_all_users()
                    for u in users:
                        try:
                            await context.bot.send_message(u[0], text_winner, parse_mode=ParseMode.MARKDOWN)
                        except:
                            pass
                    text += f"\n\n🎉 *БИЛЕТЫ ЗАКОНЧИЛИСЬ!*\nРозыгрыш проведен!"
        else:
            text = f"❌ {result['message']}"
        await edit_message(query, text, back_button("lottery"))

    # ---------- ПОПОЛНЕНИЕ ----------
    elif data == "deposit_menu":
        text = (f"💰 *Пополнение*\n\n"
                f"💎 *CryptoBot (TON)* — 1 TON = {DOLLAR_PER_TON:.2f}$\n"
                f"• Минимальная сумма: ${MIN_DEPOSIT_DOLLARS:.2f}\n"
                f"• Максимальная сумма: ${MAX_DEPOSIT_DOLLARS:.2f}\n"
                f"• Зачисление после 1 подтверждения сети")
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("💎 CryptoBot", callback_data="deposit_crypto")],
            [InlineKeyboardButton("◀️ Назад", callback_data="main_menu")]
        ])
        await edit_message(query, text, kb)

    elif data == "deposit_crypto":
        await DepositHandler.request_amount(update, context, user_id, 'crypto')

    # ---------- ВЫВОД ----------
    elif data == "withdraw_menu":
        text = (f"💸 Вывод\n\n"
                f"💰 Баланс: ${user[3]:.2f}\n"
                f"📱 Telegram: @{user[9] or 'не указан'}\n"
                f"💳 CryptoBot ID: {user[8] or 'не указан'}\n\n"
                f"Минимум $5.00, комиссия 0%")
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("📱 Telegram", callback_data="withdraw_telegram"),
             InlineKeyboardButton("💳 CryptoBot", callback_data="withdraw_crypto")],
            [InlineKeyboardButton("⚙️ Настройки", callback_data="withdraw_settings")],
            [InlineKeyboardButton("◀️ Назад", callback_data="main_menu")]
        ])
        await edit_message(query, text, kb)

    elif data == "withdraw_settings":
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("📱 Указать Telegram", callback_data="set_telegram")],
            [InlineKeyboardButton("💳 Указать CryptoBot ID", callback_data="set_crypto")],
            [InlineKeyboardButton("◀️ Назад", callback_data="withdraw_menu")]
        ])
        await edit_message(query, "⚙️ Настройки", kb)

    elif data == "set_telegram":
        context.user_data['awaiting'] = 'telegram'
        await edit_message(query, "📱 Отправьте ваш Telegram Username (без @):")

    elif data == "set_crypto":
        context.user_data['awaiting'] = 'crypto'
        await edit_message(query, "💳 Отправьте ваш CryptoBot ID (только цифры):")

    elif data == "withdraw_telegram":
        if user[3] < 5.0:
            await edit_message(query, "❌ Минимум $5.00")
            return
        if not user[9]:
            await edit_message(query, "❌ Сначала укажите Telegram Username")
            return
        context.user_data['awaiting'] = 'withdraw_telegram_amount'
        await edit_message(query, f"📱 Введите сумму для вывода (макс ${user[3]:.2f}):")

    elif data == "withdraw_crypto":
        if user[3] < 5.0:
            await edit_message(query, "❌ Минимум $5.00")
            return
        if not user[8]:
            await edit_message(query, "❌ Сначала укажите CryptoBot ID")
            return
        context.user_data['awaiting'] = 'withdraw_crypto_amount'
        await edit_message(query, f"💳 Введите сумму для вывода (макс ${user[3]:.2f}):")

    # ================== АДМИН-ПАНЕЛЬ ==================
    elif data == "admin_panel":
        if user_id not in ADMIN_IDS:
            await edit_message(query, "❌ Нет прав")
            return
        stats = db.get_total_stats()
        ps = len(db.get_pending_withdrawals())
        pn = len(db.get_pending_nft_withdrawals())
        lottery_stats = db.get_lottery_stats()
        lottery_sold = lottery_stats[1] if lottery_stats else 0
        text = (f"⚙️ Админ-панель\n\n"
                f"👥 Пользователей: {stats['total_users']}\n"
                f"💰 Баланс: ${stats['total_balance']:.2f}\n"
                f"❄️ Снежинок: {stats['total_snowflakes']} ✨\n"
                f"💸 Выведено: ${stats['total_withdrawn']:.2f}\n"
                f"🎮 Игр: {stats['total_games']}\n\n"
                f"🎟️ Билетов продано: {lottery_sold}/100\n"
                f"⏳ Заявок на вывод: {ps}\n"
                f"🖼️ Заявок на NFT: {pn}")
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("👥 Пользователи", callback_data="admin_users")],
            [InlineKeyboardButton("⏳ Заявки вывод", callback_data="admin_withdrawals")],
            [InlineKeyboardButton("🖼️ Заявки NFT", callback_data="admin_nft_withdrawals")],
            [InlineKeyboardButton("🎟️ Промокоды", callback_data="admin_promocodes")],
            [InlineKeyboardButton("🔨 Баны", callback_data="admin_bans")],
            [InlineKeyboardButton("📢 Рассылка", callback_data="admin_broadcast")],
            [InlineKeyboardButton("🖼️ Картинки", callback_data="admin_images")],
            [InlineKeyboardButton("🎮 Настройка игр", callback_data="admin_game_settings")],
            [InlineKeyboardButton("📊 Статистика за день", callback_data="admin_stats_daily")],
            [InlineKeyboardButton("📊 Статистика за неделю", callback_data="admin_stats_weekly")],
            [InlineKeyboardButton("📊 Статистика за месяц", callback_data="admin_stats_monthly")],
            [InlineKeyboardButton("🎟️ Провести розыгрыш", callback_data="admin_draw_lottery")],
            [InlineKeyboardButton("◀️ Назад", callback_data="main_menu")]
        ])
        await edit_message(query, text, kb)

    # ---------- НАСТРОЙКА ИГР ----------
    elif data == "admin_game_settings":
        if user_id not in ADMIN_IDS:
            return
        text = "🎮 *Настройка коэффициентов игр*\n\nВыберите игру для настройки:"
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🪙 Орёл и Решка", callback_data="game_setting_flip")],
            [InlineKeyboardButton("🎲 Кости (число)", callback_data="game_setting_dice_num")],
            [InlineKeyboardButton("🎲 Кости (чёт/нечёт)", callback_data="game_setting_dice_eo")],
            [InlineKeyboardButton("🎰 Слоты", callback_data="game_setting_slots")],
            [InlineKeyboardButton("⚽ Футбол", callback_data="game_setting_football")],
            [InlineKeyboardButton("🏀 Баскетбол", callback_data="game_setting_basketball")],
            [InlineKeyboardButton("🎯 Дартс", callback_data="game_setting_darts")],
            [InlineKeyboardButton("🎳 Боулинг", callback_data="game_setting_bowling")],
            [InlineKeyboardButton("◀️ Назад", callback_data="admin_panel")]
        ])
        await edit_message(query, text, kb)

    elif data == "game_setting_flip":
        if user_id not in ADMIN_IDS:
            return
        current = GAME_SETTINGS['flip']['win_multiplier']
        context.user_data['setting_game'] = 'flip'
        context.user_data['setting_key'] = 'win_multiplier'
        context.user_data['awaiting'] = 'game_setting_value'
        await edit_message(query, f"🪙 Орёл и Решка\n\nТекущий коэффициент выигрыша: x{current}\n\nВведите новый коэффициент (например: 1.7, 2.0, 2.5):")

    elif data == "game_setting_dice_num":
        if user_id not in ADMIN_IDS:
            return
        current = GAME_SETTINGS['dice_number']['win_multiplier']
        context.user_data['setting_game'] = 'dice_number'
        context.user_data['setting_key'] = 'win_multiplier'
        context.user_data['awaiting'] = 'game_setting_value'
        await edit_message(query, f"🎲 Кости на число\n\nТекущий коэффициент выигрыша: x{current}\n\nВведите новый коэффициент (например: 4.7, 5.0, 6.0):")

    elif data == "game_setting_dice_eo":
        if user_id not in ADMIN_IDS:
            return
        current = GAME_SETTINGS['dice_even_odd']['win_multiplier']
        context.user_data['setting_game'] = 'dice_even_odd'
        context.user_data['setting_key'] = 'win_multiplier'
        context.user_data['awaiting'] = 'game_setting_value'
        await edit_message(query, f"🎲 Кости чёт/нечёт\n\nТекущий коэффициент выигрыша: x{current}\n\nВведите новый коэффициент (например: 1.7, 2.0, 2.5):")

    elif data == "game_setting_slots":
        if user_id not in ADMIN_IDS:
            return
        current1 = GAME_SETTINGS['slots']['1']
        current2 = GAME_SETTINGS['slots']['2']
        current3 = GAME_SETTINGS['slots']['3']
        text = (f"🎰 Слоты\n\n"
                f"Текущие коэффициенты:\n"
                f"• 1 совпадение: x{current1}\n"
                f"• 2 совпадения: x{current2}\n"
                f"• 3 совпадения: x{current3}\n\n"
                f"Выберите что изменить:")
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("1 совпадение", callback_data="slot_setting_1")],
            [InlineKeyboardButton("2 совпадения", callback_data="slot_setting_2")],
            [InlineKeyboardButton("3 совпадения", callback_data="slot_setting_3")],
            [InlineKeyboardButton("◀️ Назад", callback_data="admin_game_settings")]
        ])
        await edit_message(query, text, kb)

    elif data.startswith("slot_setting_"):
        if user_id not in ADMIN_IDS:
            return
        slot_num = data.replace("slot_setting_", "")
        context.user_data['setting_game'] = 'slots'
        context.user_data['setting_key'] = slot_num
        context.user_data['awaiting'] = 'game_setting_value'
        current = GAME_SETTINGS['slots'][slot_num]
        await edit_message(query, f"🎰 Слоты - {slot_num} совпадение\n\nТекущий коэффициент: x{current}\n\nВведите новый коэффициент (например: 1.4, 2.0, 5.0):")

    elif data == "game_setting_football":
        if user_id not in ADMIN_IDS:
            return
        current_goal = GAME_SETTINGS['football']['goal']
        current_miss = GAME_SETTINGS['football']['miss']
        text = (f"⚽ Футбол\n\n"
                f"Текущие коэффициенты:\n"
                f"• ГОЛ: x{current_goal}\n"
                f"• МИМО: x{current_miss}\n\n"
                f"Выберите что изменить:")
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("ГОЛ", callback_data="football_setting_goal")],
            [InlineKeyboardButton("МИМО", callback_data="football_setting_miss")],
            [InlineKeyboardButton("◀️ Назад", callback_data="admin_game_settings")]
        ])
        await edit_message(query, text, kb)

    elif data == "football_setting_goal":
        if user_id not in ADMIN_IDS:
            return
        context.user_data['setting_game'] = 'football'
        context.user_data['setting_key'] = 'goal'
        context.user_data['awaiting'] = 'game_setting_value'
        current = GAME_SETTINGS['football']['goal']
        await edit_message(query, f"⚽ Футбол - ГОЛ\n\nТекущий коэффициент: x{current}\n\nВведите новый коэффициент (например: 1.2, 1.5, 2.0):")

    elif data == "football_setting_miss":
        if user_id not in ADMIN_IDS:
            return
        context.user_data['setting_game'] = 'football'
        context.user_data['setting_key'] = 'miss'
        context.user_data['awaiting'] = 'game_setting_value'
        current = GAME_SETTINGS['football']['miss']
        await edit_message(query, f"⚽ Футбол - МИМО\n\nТекущий коэффициент: x{current}\n\nВведите новый коэффициент (можно 0 для проигрыша, или 1.7 для выигрыша):")

    elif data == "game_setting_basketball":
        if user_id not in ADMIN_IDS:
            return
        current_point = GAME_SETTINGS['basketball']['point']
        current_miss = GAME_SETTINGS['basketball']['miss']
        text = (f"🏀 Баскетбол\n\n"
                f"Текущие коэффициенты:\n"
                f"• ОЧКО: x{current_point}\n"
                f"• МИМО: x{current_miss}\n\n"
                f"Выберите что изменить:")
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("ОЧКО", callback_data="basketball_setting_point")],
            [InlineKeyboardButton("МИМО", callback_data="basketball_setting_miss")],
            [InlineKeyboardButton("◀️ Назад", callback_data="admin_game_settings")]
        ])
        await edit_message(query, text, kb)

    elif data == "basketball_setting_point":
        if user_id not in ADMIN_IDS:
            return
        context.user_data['setting_game'] = 'basketball'
        context.user_data['setting_key'] = 'point'
        context.user_data['awaiting'] = 'game_setting_value'
        current = GAME_SETTINGS['basketball']['point']
        await edit_message(query, f"🏀 Баскетбол - ОЧКО\n\nТекущий коэффициент: x{current}\n\nВведите новый коэффициент (например: 1.4, 1.7, 2.0):")

    elif data == "basketball_setting_miss":
        if user_id not in ADMIN_IDS:
            return
        context.user_data['setting_game'] = 'basketball'
        context.user_data['setting_key'] = 'miss'
        context.user_data['awaiting'] = 'game_setting_value'
        current = GAME_SETTINGS['basketball']['miss']
        await edit_message(query, f"🏀 Баскетбол - МИМО\n\nТекущий коэффициент: x{current}\n\nВведите новый коэффициент (можно 0 для проигрыша, или 1.4 для выигрыша):")

    elif data == "game_setting_darts":
        if user_id not in ADMIN_IDS:
            return
        current_bullseye = GAME_SETTINGS['darts']['bullseye']
        current_miss = GAME_SETTINGS['darts']['miss']
        text = (f"🎯 Дартс\n\n"
                f"Текущие коэффициенты:\n"
                f"• В ЯБЛОЧКО: x{current_bullseye}\n"
                f"• МИМО: x{current_miss}\n\n"
                f"Выберите что изменить:")
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("В ЯБЛОЧКО", callback_data="darts_setting_bullseye")],
            [InlineKeyboardButton("МИМО", callback_data="darts_setting_miss")],
            [InlineKeyboardButton("◀️ Назад", callback_data="admin_game_settings")]
        ])
        await edit_message(query, text, kb)

    elif data == "darts_setting_bullseye":
        if user_id not in ADMIN_IDS:
            return
        context.user_data['setting_game'] = 'darts'
        context.user_data['setting_key'] = 'bullseye'
        context.user_data['awaiting'] = 'game_setting_value'
        current = GAME_SETTINGS['darts']['bullseye']
        await edit_message(query, f"🎯 Дартс - В ЯБЛОЧКО\n\nТекущий коэффициент: x{current}\n\nВведите новый коэффициент (например: 1.95, 2.5, 3.0):")

    elif data == "darts_setting_miss":
        if user_id not in ADMIN_IDS:
            return
        context.user_data['setting_game'] = 'darts'
        context.user_data['setting_key'] = 'miss'
        context.user_data['awaiting'] = 'game_setting_value'
        current = GAME_SETTINGS['darts']['miss']
        await edit_message(query, f"🎯 Дартс - МИМО\n\nТекущий коэффициент: x{current}\n\nВведите новый коэффициент (можно 0 для проигрыша, или 1.5 для выигрыша):")

    elif data == "game_setting_bowling":
        if user_id not in ADMIN_IDS:
            return
        current_strike = GAME_SETTINGS['bowling']['strike']
        current_miss = GAME_SETTINGS['bowling']['miss']
        text = (f"🎳 Боулинг\n\n"
                f"Текущие коэффициенты:\n"
                f"• СТРАЙК: x{current_strike}\n"
                f"• МИМО: x{current_miss}\n\n"
                f"Выберите что изменить:")
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("СТРАЙК", callback_data="bowling_setting_strike")],
            [InlineKeyboardButton("МИМО", callback_data="bowling_setting_miss")],
            [InlineKeyboardButton("◀️ Назад", callback_data="admin_game_settings")]
        ])
        await edit_message(query, text, kb)

    elif data == "bowling_setting_strike":
        if user_id not in ADMIN_IDS:
            return
        context.user_data['setting_game'] = 'bowling'
        context.user_data['setting_key'] = 'strike'
        context.user_data['awaiting'] = 'game_setting_value'
        current = GAME_SETTINGS['bowling']['strike']
        await edit_message(query, f"🎳 Боулинг - СТРАЙК\n\nТекущий коэффициент: x{current}\n\nВведите новый коэффициент (например: 1.9, 2.5, 3.0):")

    elif data == "bowling_setting_miss":
        if user_id not in ADMIN_IDS:
            return
        context.user_data['setting_game'] = 'bowling'
        context.user_data['setting_key'] = 'miss'
        context.user_data['awaiting'] = 'game_setting_value'
        current = GAME_SETTINGS['bowling']['miss']
        await edit_message(query, f"🎳 Боулинг - МИМО\n\nТекущий коэффициент: x{current}\n\nВведите новый коэффициент (можно 0 для проигрыша, или 1.5 для выигрыша):")

    # ---------- ОСТАЛЬНЫЕ АДМИН-ФУНКЦИИ ----------
    elif data == "admin_draw_lottery":
        if user_id not in ADMIN_IDS:
            return
        winner_result = db.draw_lottery()
        if winner_result:
            winner_user = db.get_user(winner_result['winner_id'])
            winner_name = winner_user[2] if winner_user else f"ID: {winner_result['winner_id']}"
            text = (f"🎉 *РОЗЫГРЫШ ЛОТЕРЕИ!*\n\n"
                   f"🏆 Победитель: {winner_name}\n"
                   f"🎫 Выигрышный билет: {winner_result['winner_ticket']}\n"
                   f"💰 Приз: ${winner_result['prize']:.2f}\n\n"
                   f"Поздравляем!")
            users = db.get_all_users()
            for u in users:
                try:
                    await context.bot.send_message(u[0], text, parse_mode=ParseMode.MARKDOWN)
                except:
                    pass
            await edit_message(query, f"✅ Розыгрыш проведен!\n\nПобедитель: {winner_name}\nБилет: {winner_result['winner_ticket']}", back_button("admin_panel"))
        else:
            await edit_message(query, "❌ Нет билетов для розыгрыша", back_button("admin_panel"))

    elif data == "admin_stats_daily":
        if user_id not in ADMIN_IDS:
            return
        s = db.get_daily_stats()
        text = (f"📊 Статистика за сегодня\n\n"
                f"👥 Новые пользователи: {s['new_users']}\n"
                f"🎮 Сыграно игр: {s['games']}\n"
                f"🏆 Самая популярная: {s['popular']}\n\n"
                f"💰 Пополнения: ${s['deposits']:.2f}\n"
                f"💸 Выводы: ${s['withdrawals']:.2f}\n"
                f"📊 Чистая прибыль: ${s['profit']:.2f}")
        await edit_message(query, text, back_button("admin_panel"))

    elif data == "admin_stats_weekly":
        if user_id not in ADMIN_IDS:
            return
        s = db.get_weekly_stats()
        text = (f"📊 Статистика за неделю\n\n"
                f"👥 Новые пользователи: {s['new_users']}\n"
                f"🎮 Сыграно игр: {s['games']}\n"
                f"🏆 Самая популярная: {s['popular']}\n\n"
                f"💰 Пополнения: ${s['deposits']:.2f}\n"
                f"💸 Выводы: ${s['withdrawals']:.2f}\n"
                f"📊 Чистая прибыль: ${s['profit']:.2f}")
        await edit_message(query, text, back_button("admin_panel"))

    elif data == "admin_stats_monthly":
        if user_id not in ADMIN_IDS:
            return
        s = db.get_monthly_stats()
        text = (f"📊 Статистика за месяц\n\n"
                f"👥 Новые пользователи: {s['new_users']}\n"
                f"🎮 Сыграно игр: {s['games']}\n"
                f"🏆 Самая популярная: {s['popular']}\n\n"
                f"💰 Пополнения: ${s['deposits']:.2f}\n"
                f"💸 Выводы: ${s['withdrawals']:.2f}\n"
                f"📊 Чистая прибыль: ${s['profit']:.2f}")
        await edit_message(query, text, back_button("admin_panel"))

    elif data == "admin_users":
        if user_id not in ADMIN_IDS:
            return
        users = db.get_all_users()
        text = f"👥 Всего: {len(users)}\n\n"
        for u in users[:20]:
            status = "🔴" if u[5] == 1 else "🟢"
            admin = "👑" if u[6] == 1 else ""
            text += f"{status}{admin} {u[2]} (@{u[1]}) — ${u[3]:.2f} | ✨ {u[4]}\n"
        await edit_message(query, text, back_button("admin_panel"))

    elif data == "admin_withdrawals":
        if user_id not in ADMIN_IDS:
            return
        ws = db.get_pending_withdrawals()
        if not ws:
            await edit_message(query, "✅ Нет заявок", back_button("admin_panel"))
            return
        text = "⏳ Заявки на вывод:\n\n"
        kb_rows = []
        for w in ws[:5]:
            text += f"🆔 #{w[0]}\n👤 @{w[7]}\n💰 ${w[2]:.2f}\n🕐 {w[6][:16]}\n\n"
            kb_rows.append([
                InlineKeyboardButton(f"✅ Принять #{w[0]}", callback_data=f"approve_withdrawal_{w[0]}"),
                InlineKeyboardButton(f"❌ Отклонить #{w[0]}", callback_data=f"reject_withdrawal_{w[0]}")
            ])
        kb_rows.append([InlineKeyboardButton("◀️ Назад", callback_data="admin_panel")])
        kb = InlineKeyboardMarkup(kb_rows)
        await edit_message(query, text, kb)

    elif data.startswith("approve_withdrawal_"):
        if user_id not in ADMIN_IDS:
            return
        wid = int(data.replace("approve_withdrawal_", ""))
        if db.approve_withdrawal(wid, user_id):
            db.cursor.execute('SELECT user_id, amount FROM withdrawals WHERE id = ?', (wid,))
            uid, amt = db.cursor.fetchone()
            await context.bot.send_message(uid, f"✅ Заявка на вывод одобрена!\n💰 ${amt:.2f}\n⏳ Ожидайте выдачи.")
            kb = InlineKeyboardMarkup([[InlineKeyboardButton(f"✅ Выдано #{wid}", callback_data=f"complete_withdrawal_{wid}")]])
            await edit_message(query, f"✅ Заявка #{wid} одобрена. После выдачи нажмите кнопку.", kb)
        else:
            await edit_message(query, "❌ Ошибка")

    elif data.startswith("complete_withdrawal_"):
        if user_id not in ADMIN_IDS:
            return
        wid = int(data.replace("complete_withdrawal_", ""))
        if db.complete_withdrawal(wid, user_id):
            db.cursor.execute('SELECT user_id, amount FROM withdrawals WHERE id = ?', (wid,))
            uid, amt = db.cursor.fetchone()
            await context.bot.send_message(uid, f"✅ Вывод выполнен!\n💰 ${amt:.2f} получены.")
            await edit_message(query, f"✅ Заявка #{wid} завершена.")
        else:
            await edit_message(query, "❌ Ошибка")

    elif data.startswith("reject_withdrawal_"):
        if user_id not in ADMIN_IDS:
            return
        wid = int(data.replace("reject_withdrawal_", ""))
        context.user_data['reject_id'] = wid
        context.user_data['awaiting'] = 'reject_reason'
        await edit_message(query, f"❌ Причина отказа для #{wid}:")

    elif data == "admin_nft_withdrawals":
        if user_id not in ADMIN_IDS:
            return
        ws = db.get_pending_nft_withdrawals()
        if not ws:
            await edit_message(query, "✅ Нет заявок", back_button("admin_panel"))
            return
        text = "🖼️ Заявки на NFT:\n\n"
        kb_rows = []
        for w in ws[:5]:
            text += f"🆔 #{w[0]}\n👤 @{w[7]}\n🎁 {w[2]}\n💰 ${w[3]:.2f}\n🕐 {w[6][:16]}\n\n"
            kb_rows.append([
                InlineKeyboardButton(f"✅ Принять #{w[0]}", callback_data=f"approve_nft_{w[0]}"),
                InlineKeyboardButton(f"❌ Отклонить #{w[0]}", callback_data=f"reject_nft_{w[0]}")
            ])
        kb_rows.append([InlineKeyboardButton("◀️ Назад", callback_data="admin_panel")])
        kb = InlineKeyboardMarkup(kb_rows)
        await edit_message(query, text, kb)

    elif data.startswith("approve_nft_"):
        if user_id not in ADMIN_IDS:
            return
        wid = int(data.replace("approve_nft_", ""))
        if db.approve_nft_withdrawal(wid, user_id):
            db.cursor.execute('SELECT user_id, nft_name FROM nft_withdrawals WHERE id = ?', (wid,))
            uid, name = db.cursor.fetchone()
            await context.bot.send_message(uid, f"✅ Заявка на вывод NFT одобрена!\n🎁 {name}\n⏳ Ожидайте выдачи.")
            kb = InlineKeyboardMarkup([[InlineKeyboardButton(f"✅ Выдано #{wid}", callback_data=f"complete_nft_{wid}")]])
            await edit_message(query, f"✅ Заявка #{wid} одобрена.", kb)
        else:
            await edit_message(query, "❌ Ошибка")

    elif data.startswith("complete_nft_"):
        if user_id not in ADMIN_IDS:
            return
        wid = int(data.replace("complete_nft_", ""))
        if db.complete_nft_withdrawal(wid, user_id):
            db.cursor.execute('SELECT user_id, nft_name FROM nft_withdrawals WHERE id = ?', (wid,))
            uid, name = db.cursor.fetchone()
            await context.bot.send_message(uid, f"✅ NFT выдан!\n🎁 {name} получен.")
            await edit_message(query, f"✅ Заявка #{wid} завершена.")
        else:
            await edit_message(query, "❌ Ошибка")

    elif data.startswith("reject_nft_"):
        if user_id not in ADMIN_IDS:
            return
        wid = int(data.replace("reject_nft_", ""))
        context.user_data['reject_nft_id'] = wid
        context.user_data['awaiting'] = 'reject_nft_reason'
        await edit_message(query, f"❌ Причина отказа для NFT #{wid}:")

    elif data == "admin_promocodes":
        if user_id not in ADMIN_IDS:
            return
        promos = db.get_all_promocodes()
        text = "🎟️ Промокоды\n\n"
        for p in promos:
            text += f"• `{p[1]}` — ${p[2]:.2f} | {p[5]}/{p[4]}\n"
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("➕ Создать", callback_data="admin_create_promo")],
            [InlineKeyboardButton("◀️ Назад", callback_data="admin_panel")]
        ])
        await edit_message(query, text, kb)

    elif data == "admin_create_promo":
        if user_id not in ADMIN_IDS:
            return
        context.user_data['promo_step'] = 'amount'
        context.user_data['awaiting'] = 'promo_amount'
        await edit_message(query, "🎟️ Сумма в $:")

    elif data == "admin_bans":
        if user_id not in ADMIN_IDS:
            return
        banned = db.get_banned_users()
        if not banned:
            await edit_message(query, "✅ Нет забаненных", back_button("admin_panel"))
            return
        text = "🔨 Забанены:\n\n"
        kb_rows = []
        for b in banned:
            text += f"• {b[2]} (@{b[1]}) — ID: {b[0]}\n"
            kb_rows.append([InlineKeyboardButton(f"✅ Разбанить {b[0]}", callback_data=f"unban_{b[0]}")])
        kb_rows.append([InlineKeyboardButton("◀️ Назад", callback_data="admin_panel")])
        kb = InlineKeyboardMarkup(kb_rows)
        await edit_message(query, text, kb)

    elif data.startswith("unban_"):
        if user_id not in ADMIN_IDS:
            return
        bid = int(data.replace("unban_", ""))
        if db.unban_user(user_id, bid):
            await edit_message(query, f"✅ Пользователь {bid} разбанен")
        else:
            await edit_message(query, "❌ Ошибка")

    elif data == "admin_broadcast":
        if user_id not in ADMIN_IDS:
            return
        context.user_data['awaiting'] = 'broadcast'
        await edit_message(query, "📢 Отправьте сообщение для рассылки (можно с фото):")

    elif data == "admin_images":
        if user_id not in ADMIN_IDS:
            return
        text = (f"🖼️ Картинки\n\n"
                f"Приветствие: {'✅' if WELCOME_IMAGE_ID else '❌'}\n"
                f"Кейс: {'✅' if CASE_IMAGE_ID else '❌'}")
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🖼️ Загрузить приветствие", callback_data="upload_welcome")],
            [InlineKeyboardButton("🖼️ Загрузить кейс", callback_data="upload_case")],
            [InlineKeyboardButton("◀️ Назад", callback_data="admin_panel")]
        ])
        await edit_message(query, text, kb)

    elif data == "upload_welcome":
        if user_id not in ADMIN_IDS:
            return
        context.user_data['awaiting'] = 'upload_welcome'
        await edit_message(query, "🖼️ Отправьте фото для приветствия:")

    elif data == "upload_case":
        if user_id not in ADMIN_IDS:
            return
        context.user_data['awaiting'] = 'upload_case'
        await edit_message(query, "🖼️ Отправьте фото для кейса:")

    elif data == "noop":
        pass

    elif data == "main_menu":
        current_user = db.get_user(user_id)
        kb_rows = [
            [InlineKeyboardButton("🎰 Казино", callback_data="casino_menu"),
             InlineKeyboardButton("📦 Кейс", callback_data="case_menu")],
            [InlineKeyboardButton("❄️ Зимний магазин", callback_data="winter_shop"),
             InlineKeyboardButton("🎁 Бонус", callback_data="daily_bonus")],
            [InlineKeyboardButton("👥 Рефералы", callback_data="referral"),
             InlineKeyboardButton("👤 Профиль", callback_data="profile")],
            [InlineKeyboardButton("💰 Пополнить", callback_data="deposit_menu"),
             InlineKeyboardButton("💸 Вывод", callback_data="withdraw_menu")],
            [InlineKeyboardButton("🎟️ Промокод", callback_data="activate_promo"),
             InlineKeyboardButton("📦 Инвентарь", callback_data="inventory")],
            [InlineKeyboardButton("🎟️ Лотерея", callback_data="lottery"),
             InlineKeyboardButton("📜 Правила", callback_data="rules")]
        ]
        if user_id in ADMIN_IDS:
            kb_rows.append([InlineKeyboardButton("⚙️ Админ-панель", callback_data="admin_panel")])
        kb = InlineKeyboardMarkup(kb_rows)
        text = f"🌟 {BOT_NAME}\n\n🆔 ID: {user_id}\n💰 Баланс: ${current_user[3]:.2f}\n❄️ Снежинки: {current_user[4]} ✨"
        await edit_message(query, text, kb)

# ======================== ПЛАТЕЖИ (CRYPTOBOT) ========================
async def precheckout_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.pre_checkout_query
    await query.answer(ok=True)

async def successful_payment_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Этот обработчик не используется для CryptoBot, но оставлен для совместимости
    pass

# ======================== ОБРАБОТКА СООБЩЕНИЙ ========================
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_ban(update, context):
        return
    user_id = update.effective_user.id
    text = update.message.text if update.message.text else ""

    if user_id in ADMIN_IDS:
        if context.user_data.get('awaiting') == 'upload_welcome' and update.message.photo:
            file_id = update.message.photo[-1].file_id
            db.save_image('welcome_image', file_id)
            context.user_data.pop('awaiting')
            await update.message.reply_text("✅ Картинка сохранена!")
            return
        elif context.user_data.get('awaiting') == 'upload_case' and update.message.photo:
            file_id = update.message.photo[-1].file_id
            db.save_image('case_image', file_id)
            context.user_data.pop('awaiting')
            await update.message.reply_text("✅ Картинка сохранена!")
            return

    if 'awaiting' not in context.user_data:
        return
    state = context.user_data['awaiting']

    # Пополнение CryptoBot
    if state == 'deposit_amount_crypto':
        method = 'crypto'
        success = await DepositHandler.process_amount(update, context, user_id, text, method)
        if success:
            context.user_data.pop('awaiting')
            context.user_data.pop('deposit_method')
        return

    # Ставка
    if state == 'bet_amount':
        try:
            bet = float(text.replace(',', '.'))
            if bet < 0.1:
                await update.message.reply_text("❌ Минимальная ставка $0.10")
                return
            user = db.get_user(user_id)
            if bet > user[3]:
                await update.message.reply_text("❌ Недостаточно средств")
                return
            await handle_bet(update, context, user_id, bet)
        except ValueError:
            await update.message.reply_text("❌ Введите число (например, 0.5, 1.25)")
        return

    if state == 'dice_bet':
        try:
            bet = float(text.replace(',', '.'))
            if bet < 0.1:
                await update.message.reply_text("❌ Минимальная ставка $0.10")
                return
            user = db.get_user(user_id)
            if bet > user[3]:
                await update.message.reply_text("❌ Недостаточно средств")
                return
            await handle_bet(update, context, user_id, bet)
        except ValueError:
            await update.message.reply_text("❌ Введите число")
        return

    if state == 'mines_bet':
        try:
            bet = float(text.replace(',', '.'))
            if bet < 0.1:
                await update.message.reply_text("❌ Минимальная ставка $0.10")
                return
            user = db.get_user(user_id)
            if bet > user[3]:
                await update.message.reply_text("❌ Недостаточно средств")
                return
            mines = context.user_data.get('mines_count', 5)
            db.update_balance(user_id, -bet)
            game = MinesGame(bet, mines)
            context.user_data['mines_game'] = game
            context.user_data['game_start_time'] = time.time()
            await show_mines_field(update, context, game)
            context.user_data.pop('awaiting')
            context.user_data.pop('mines_count')
        except ValueError:
            await update.message.reply_text("❌ Введите число")
        return

    if state == 'telegram':
        db.update_telegram_username(user_id, text.strip().replace('@', ''))
        context.user_data.pop('awaiting')
        await update.message.reply_text("✅ Telegram сохранён")
    elif state == 'crypto':
        try:
            db.update_crypto_id(user_id, int(text))
            context.user_data.pop('awaiting')
            await update.message.reply_text("✅ CryptoBot ID сохранён")
        except:
            await update.message.reply_text("❌ Введите число")

    elif state == 'withdraw_telegram_amount':
        try:
            amt = float(text.replace(',', '.'))
            user = db.get_user(user_id)
            if amt < 5.0:
                await update.message.reply_text("❌ Минимум $5.00")
                return
            if amt > user[3]:
                await update.message.reply_text("❌ Недостаточно")
                return
            wid = db.create_withdrawal(user_id, amt, 'telegram', user[9])
            await update.message.reply_text(f"✅ Заявка #{wid} создана")
            for aid in ADMIN_IDS:
                kb = InlineKeyboardMarkup([
                    [InlineKeyboardButton(f"✅ Принять #{wid}", callback_data=f"approve_withdrawal_{wid}"),
                     InlineKeyboardButton(f"❌ Отклонить #{wid}", callback_data=f"reject_withdrawal_{wid}")]
                ])
                await context.bot.send_message(
                    aid,
                    f"⏳ Новая заявка\n👤 @{update.effective_user.username or user_id}\n💰 ${amt:.2f}\n📱 Telegram\n🆔 #{wid}",
                    reply_markup=kb
                )
            context.user_data.pop('awaiting')
        except:
            await update.message.reply_text("❌ Введите число")

    elif state == 'withdraw_crypto_amount':
        try:
            amt = float(text.replace(',', '.'))
            user = db.get_user(user_id)
            if amt < 5.0:
                await update.message.reply_text("❌ Минимум $5.00")
                return
            if amt > user[3]:
                await update.message.reply_text("❌ Недостаточно")
                return
            wid = db.create_withdrawal(user_id, amt, 'crypto', user[8])
            await update.message.reply_text(f"✅ Заявка #{wid} создана")
            for aid in ADMIN_IDS:
                kb = InlineKeyboardMarkup([
                    [InlineKeyboardButton(f"✅ Принять #{wid}", callback_data=f"approve_withdrawal_{wid}"),
                     InlineKeyboardButton(f"❌ Отклонить #{wid}", callback_data=f"reject_withdrawal_{wid}")]
                ])
                await context.bot.send_message(
                    aid,
                    f"⏳ Новая заявка\n👤 @{update.effective_user.username or user_id}\n💰 ${amt:.2f}\n💳 CryptoBot\n🆔 #{wid}",
                    reply_markup=kb
                )
            context.user_data.pop('awaiting')
        except:
            await update.message.reply_text("❌ Введите число")

    elif state == 'reject_reason':
        if user_id not in ADMIN_IDS:
            return
        wid = context.user_data.get('reject_id')
        reason = text
        if db.reject_withdrawal(wid, user_id, reason):
            await update.message.reply_text(f"✅ Заявка #{wid} отклонена")
            db.cursor.execute('SELECT user_id, amount FROM withdrawals WHERE id = ?', (wid,))
            uid, amt = db.cursor.fetchone()
            await context.bot.send_message(uid, f"❌ Заявка на вывод отклонена\n💰 ${amt:.2f}\n📝 Причина: {reason}")
        else:
            await update.message.reply_text("❌ Ошибка")
        context.user_data.pop('awaiting')
        context.user_data.pop('reject_id')

    elif state == 'reject_nft_reason':
        if user_id not in ADMIN_IDS:
            return
        wid = context.user_data.get('reject_nft_id')
        reason = text
        if db.reject_nft_withdrawal(wid, user_id, reason):
            await update.message.reply_text(f"✅ Заявка #{wid} отклонена")
            db.cursor.execute('SELECT user_id, nft_name FROM nft_withdrawals WHERE id = ?', (wid,))
            uid, name = db.cursor.fetchone()
            await context.bot.send_message(uid, f"❌ Заявка на вывод NFT отклонена\n🎁 {name}\n📝 Причина: {reason}")
        else:
            await update.message.reply_text("❌ Ошибка")
        context.user_data.pop('awaiting')
        context.user_data.pop('reject_nft_id')

    elif state == 'promocode':
        res = db.activate_promocode(user_id, text.upper().strip())
        if res['success']:
            msg = f"✅ Промокод активирован!\n💰 +${res['amount']:.2f}"
        else:
            msg = res['reason']
        await update.message.reply_text(msg, reply_markup=home_button())
        context.user_data.pop('awaiting')

    elif state == 'promo_amount':
        if user_id not in ADMIN_IDS:
            return
        try:
            amt = float(text.replace(',', '.'))
            context.user_data['promo_amount'] = amt
            context.user_data['awaiting'] = 'promo_days'
            await update.message.reply_text("📅 Срок действия (дни):")
        except:
            await update.message.reply_text("❌ Введите число")

    elif state == 'promo_days':
        if user_id not in ADMIN_IDS:
            return
        try:
            days = int(text)
            context.user_data['promo_days'] = days
            context.user_data['awaiting'] = 'promo_uses'
            await update.message.reply_text("🔄 Макс. использований (0 = безлимит):")
        except:
            await update.message.reply_text("❌ Введите число")

    elif state == 'promo_uses':
        if user_id not in ADMIN_IDS:
            return
        try:
            max_uses = int(text)
            amt = context.user_data['promo_amount']
            days = context.user_data['promo_days']
            code = db.generate_promocode(amt, days, max_uses, user_id)
            await update.message.reply_text(f"✅ Код: `{code}`", parse_mode=ParseMode.MARKDOWN)
            context.user_data.clear()
        except:
            await update.message.reply_text("❌ Введите число")

    elif state == 'broadcast':
        if user_id not in ADMIN_IDS:
            return
        context.user_data.pop('awaiting')
        users = db.get_all_users()
        sent = 0
        failed = 0
        await update.message.reply_text(f"📢 Рассылка {len(users)} пользователям...")
        if update.message.photo:
            photo = update.message.photo[-1].file_id
            caption = update.message.caption or ""
            for u in users:
                try:
                    await context.bot.send_photo(chat_id=u[0], photo=photo, caption=caption)
                    sent += 1
                    await asyncio.sleep(0.05)
                except:
                    failed += 1
        else:
            for u in users:
                try:
                    await context.bot.send_message(chat_id=u[0], text=text)
                    sent += 1
                    await asyncio.sleep(0.05)
                except:
                    failed += 1
        await update.message.reply_text(f"✅ Отправлено: {sent}\n❌ Ошибок: {failed}")

    elif state == 'game_setting_value':
        if user_id not in ADMIN_IDS:
            return
        try:
            new_value = float(text.replace(',', '.'))
            if new_value < 0:
                await update.message.reply_text("❌ Коэффициент не может быть отрицательным!")
                return
            game = context.user_data.get('setting_game')
            key = context.user_data.get('setting_key')
            if game and key:
                if game == 'slots':
                    GAME_SETTINGS[game][key] = new_value
                elif game in ['flip', 'dice_number', 'dice_even_odd']:
                    GAME_SETTINGS[game][key] = new_value
                else:
                    GAME_SETTINGS[game][key] = new_value
                db.save_game_settings()
                await update.message.reply_text(f"✅ Коэффициент для {game} - {key} изменён на x{new_value}")
                context.user_data.pop('setting_game')
                context.user_data.pop('setting_key')
                context.user_data.pop('awaiting')
            else:
                await update.message.reply_text("❌ Ошибка: игра не найдена")
        except ValueError:
            await update.message.reply_text("❌ Введите число (например: 1.5, 2.0, 3.7)")

# ======================== ЗАПУСК ========================
def main():
    print("=" * 60)
    print(f"🚀 ЗАПУСК {BOT_NAME} (ВАЛЮТА: $, ПОПОЛНЕНИЕ ТОЛЬКО CRYPTOBOT)")
    print("=" * 60)
    print("✅ Все игры и функционал сохранены")
    print("✅ Баланс в долларах")
    print("✅ Пополнение через CryptoBot (TON)")
    print("✅ Настройка коэффициентов через админ-панель")
    print(f"✅ Админы: {ADMIN_IDS}")
    print("=" * 60)

    try:
        application = Application.builder().token(TELEGRAM_TOKEN).build()
        application.add_handler(CommandHandler("start", start))
        application.add_handler(CallbackQueryHandler(button_handler))
        application.add_handler(PreCheckoutQueryHandler(precheckout_callback))
        application.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment_callback))
        application.add_handler(MessageHandler(filters.TEXT | filters.PHOTO, handle_message))
        print("🤖 Бот запущен!")
        application.run_polling(allowed_updates=Update.ALL_TYPES)
    except Exception as e:
        logger.error(f"Ошибка запуска: {e}")
        print(f"❌ Ошибка: {e}")

if __name__ == "__main__":
    main()
