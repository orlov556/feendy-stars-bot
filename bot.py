import logging
import random
import sqlite3
import asyncio
import json
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, LabeledPrice, PreCheckoutQuery, InputMediaPhoto
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes, MessageHandler, filters, PreCheckoutQueryHandler
from telegram.constants import ParseMode
import os
import requests
import time
import string

# ======================== НАСТРОЙКА ========================
TELEGRAM_TOKEN = os.environ.get("BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")
CRYPTOBOT_API_KEY = os.environ.get("CRYPTOBOT_API_KEY", "Y_CRYPTOBOT_API_KEY")
CRYPTOBOT_API_URL = "https://pay.crypt.bot/api"

ADMIN_IDS = [5697184715]  # ТВОЙ ID

BOT_NAME = "FEENDY STARS"

# Глобальные переменные для хранения ID картинок
WELCOME_IMAGE_ID = None
CASE_IMAGE_ID = None

# Курсы валют
RUB_PER_STAR = 1.3        # 1 звезда в боте = 1.3 рубля
RUB_PER_TON = 105          # 1 TON = 105 рублей
TON_PER_STAR = RUB_PER_STAR / RUB_PER_TON  # 1 звезда = 0.01238 TON

# Настройка логирования
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
    
    def create_invoice(self, stars_amount, currency="TON", description="Пополнение баланса FEENDY STARS"):
        """Создание счета для пополнения через CryptoBot"""
        try:
            url = f"{CRYPTOBOT_API_URL}/createInvoice"
            
            ton_amount = round(stars_amount * TON_PER_STAR, 2)
            rub_amount = stars_amount * RUB_PER_STAR
            
            payload = {
                "asset": currency,
                "amount": str(ton_amount),
                "description": f"{description} на {stars_amount} ★ (≈ {rub_amount:.2f} руб)",
                "paid_btn_name": "callback",
                "paid_btn_url": "https://t.me/FeendyStars_robot",
                "payload": f"crypto_{stars_amount}_{int(time.time())}"
            }
            
            logger.info(f"Creating CryptoBot invoice: {stars_amount} ★ = {ton_amount} TON")
            response = requests.post(url, headers=self.headers, json=payload, timeout=10)
            
            if response.status_code == 200:
                data = response.json()
                if data.get('ok'):
                    return data['result']
                else:
                    logger.error(f"CryptoBot API error: {data}")
            else:
                logger.error(f"CryptoBot HTTP error: {response.status_code}")
            return None
        except Exception as e:
            logger.error(f"CryptoBot API error: {e}")
            return None
    
    def transfer(self, user_id, amount, currency="TON"):
        """Перевод средств пользователю"""
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
            logger.error(f"CryptoBot transfer error: {e}")
            return False

crypto = CryptoBotAPI(CRYPTOBOT_API_KEY)

# ======================== БАЗА ДАННЫХ ========================

class Database:
    def __init__(self):
        self.conn = sqlite3.connect('feendy_stars.db', check_same_thread=False)
        self.cursor = self.conn.cursor()
        self._create_tables()
        self._init_admin()
        self._load_images()
        self._init_promocodes()
    
    def _create_tables(self):
        # Таблица пользователей
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                balance INTEGER DEFAULT 0,
                snowflakes INTEGER DEFAULT 0,
                referrals INTEGER DEFAULT 0,
                referred_by INTEGER,
                daily_bonus DATE,
                crypto_id TEXT,
                telegram_username TEXT,
                is_admin INTEGER DEFAULT 0,
                is_banned INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                total_withdrawn INTEGER DEFAULT 0,
                total_lost INTEGER DEFAULT 0,
                last_active TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Таблица игр
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS games (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                game_type TEXT,
                bet INTEGER,
                multiplier REAL,
                win INTEGER,
                result TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Таблица кейсов
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS cases (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT,
                price INTEGER,
                items TEXT
            )
        ''')
        
        # Таблица инвентаря
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS inventory (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                item_name TEXT,
                item_price INTEGER,
                source TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Таблица заявок на вывод звёзд
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS withdrawals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                amount INTEGER,
                method TEXT,
                wallet TEXT,
                status TEXT DEFAULT 'pending',
                reject_reason TEXT,
                admin_id INTEGER,
                processed_at TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Таблица заявок на вывод NFT
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS nft_withdrawals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                nft_name TEXT,
                snowflakes_cost INTEGER,
                status TEXT DEFAULT 'pending',
                reject_reason TEXT,
                admin_id INTEGER,
                processed_at TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Таблица промокодов
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS promocodes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                code TEXT UNIQUE,
                amount INTEGER,
                expires_at DATE,
                max_uses INTEGER,
                used_count INTEGER DEFAULT 0,
                created_by INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Таблица активаций промокодов
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS promocode_uses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                code TEXT,
                used_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Таблица настроек
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        ''')
        
        # Таблица платежей
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS payments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                amount INTEGER,
                method TEXT,
                invoice_id TEXT,
                status TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        self.conn.commit()
        self._init_cases()
        self._init_settings()
    
    def _init_cases(self):
        cases = [
            {
                'name': BOT_NAME,
                'price': 35,
                'items': [
                    {'name': '❤️ Сердце', 'chance': 60, 'value': 15},
                    {'name': '🌹 Роза', 'chance': 17, 'value': 25},
                    {'name': '🚀 Ракета', 'chance': 7, 'value': 50},
                    {'name': '🌸 Цветы', 'chance': 7, 'value': 50},
                    {'name': '💍 Кольцо', 'chance': 3, 'value': 100},
                    {'name': '💎 Алмаз', 'chance': 1.5, 'value': 100},
                    {'name': '🎭 Люлом', 'chance': 1, 'value': 325},
                    {'name': '🐕 Chyn Dogg', 'chance': 1, 'value': 425}
                ]
            }
        ]
        
        for case in cases:
            self.cursor.execute(
                'INSERT OR IGNORE INTO cases (name, price, items) VALUES (?, ?, ?)',
                (case['name'], case['price'], json.dumps(case['items']))
            )
        self.conn.commit()
    
    def _init_admin(self):
        """Принудительно делаем админа при каждом запуске"""
        for admin_id in ADMIN_IDS:
            self.cursor.execute('SELECT * FROM users WHERE user_id = ?', (admin_id,))
            user = self.cursor.fetchone()
            
            if user:
                self.cursor.execute('''
                    UPDATE users SET is_admin = 1, is_banned = 0 WHERE user_id = ?
                ''', (admin_id,))
            else:
                self.cursor.execute('''
                    INSERT INTO users (user_id, username, first_name, is_admin, is_banned)
                    VALUES (?, 'admin', 'Admin', 1, 0)
                ''', (admin_id,))
        
        self.conn.commit()
        logger.info(f"✅ Админ с ID {ADMIN_IDS[0]} установлен")
    
    def _init_settings(self):
        settings = {
            'min_withdrawal': '50',
            'withdrawal_fee': '0',
            'case_price': '35',
            'house_edge': '10',
            'stars_rate': '1',
            'rub_per_star': '1.3',
            'rub_per_ton': '105'
        }
        for key, value in settings.items():
            self.cursor.execute(
                'INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)',
                (key, value)
            )
        self.conn.commit()
    
    def _init_promocodes(self):
        """Добавляем тестовый промокод если нет"""
        self.cursor.execute('SELECT COUNT(*) FROM promocodes')
        if self.cursor.fetchone()[0] == 0:
            expiry = (datetime.now() + timedelta(days=30)).date()
            self.cursor.execute('''
                INSERT INTO promocodes (code, amount, expires_at, max_uses, created_by)
                VALUES (?, ?, ?, ?, ?)
            ''', ('FEENDY100', 100, expiry, 100, ADMIN_IDS[0]))
            self.conn.commit()
    
    def _load_images(self):
        """Загрузка ID картинок из базы данных"""
        global WELCOME_IMAGE_ID, CASE_IMAGE_ID
        
        self.cursor.execute('SELECT value FROM settings WHERE key = ?', ('welcome_image',))
        result = self.cursor.fetchone()
        if result:
            WELCOME_IMAGE_ID = result[0]
            logger.info(f"✅ Загружена картинка приветствия")
        
        self.cursor.execute('SELECT value FROM settings WHERE key = ?', ('case_image',))
        result = self.cursor.fetchone()
        if result:
            CASE_IMAGE_ID = result[0]
            logger.info(f"✅ Загружена картинка кейса")
    
    def save_image(self, key, file_id):
        """Сохранение ID картинки в базу данных"""
        global WELCOME_IMAGE_ID, CASE_IMAGE_ID
        
        self.cursor.execute('''
            INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)
        ''', (key, file_id))
        self.conn.commit()
        
        if key == 'welcome_image':
            WELCOME_IMAGE_ID = file_id
            logger.info(f"✅ Сохранена картинка приветствия")
        elif key == 'case_image':
            CASE_IMAGE_ID = file_id
            logger.info(f"✅ Сохранена картинка кейса")
    
    # ================== РАБОТА С ПОЛЬЗОВАТЕЛЯМИ ==================
    
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
            self.cursor.execute('''
                UPDATE users SET referrals = referrals + 1, snowflakes = snowflakes + 5
                WHERE user_id = ?
            ''', (referred_by,))
            self.conn.commit()
    
    def update_balance(self, user_id, amount):
        self.cursor.execute('''
            UPDATE users SET balance = balance + ?, last_active = CURRENT_TIMESTAMP WHERE user_id = ?
        ''', (amount, user_id))
        self.conn.commit()
    
    def update_snowflakes(self, user_id, amount):
        self.cursor.execute('''
            UPDATE users SET snowflakes = snowflakes + ?, last_active = CURRENT_TIMESTAMP WHERE user_id = ?
        ''', (amount, user_id))
        self.conn.commit()
    
    def add_lost_stars(self, user_id, amount):
        self.cursor.execute('''
            UPDATE users SET total_lost = total_lost + ?, 
            snowflakes = snowflakes + ?, last_active = CURRENT_TIMESTAMP WHERE user_id = ?
        ''', (amount, int(amount * 0.5), user_id))
        self.conn.commit()
    
    def update_crypto_id(self, user_id, crypto_id):
        self.cursor.execute('''
            UPDATE users SET crypto_id = ?, last_active = CURRENT_TIMESTAMP WHERE user_id = ?
        ''', (crypto_id, user_id))
        self.conn.commit()
    
    def update_telegram_username(self, user_id, telegram_username):
        self.cursor.execute('''
            UPDATE users SET telegram_username = ?, last_active = CURRENT_TIMESTAMP WHERE user_id = ?
        ''', (telegram_username, user_id))
        self.conn.commit()
    
    def get_all_users(self, sort_by='date', order='desc', limit=20, offset=0):
        """Получение пользователей с сортировкой и пагинацией"""
        sort_fields = {
            'balance': 'balance',
            'date': 'created_at',
            'activity': 'last_active',
            'snowflakes': 'snowflakes',
            'referrals': 'referrals'
        }
        
        sort_field = sort_fields.get(sort_by, 'created_at')
        order_dir = 'DESC' if order == 'desc' else 'ASC'
        
        self.cursor.execute(f'''
            SELECT user_id, username, first_name, balance, snowflakes, is_banned, is_admin, created_at, last_active 
            FROM users 
            ORDER BY {sort_field} {order_dir}
            LIMIT ? OFFSET ?
        ''', (limit, offset))
        
        return self.cursor.fetchall()
    
    def get_total_users_count(self):
        self.cursor.execute('SELECT COUNT(*) FROM users')
        return self.cursor.fetchone()[0]
    
    def search_users(self, query):
        """Поиск пользователей по ID или Username"""
        self.cursor.execute('''
            SELECT user_id, username, first_name, balance, snowflakes, is_banned, is_admin 
            FROM users 
            WHERE user_id LIKE ? OR username LIKE ? OR first_name LIKE ?
            LIMIT 20
        ''', (f'%{query}%', f'%{query}%', f'%{query}%'))
        return self.cursor.fetchall()
    
    def get_user_by_id(self, user_id):
        self.cursor.execute('SELECT * FROM users WHERE user_id = ?', (user_id,))
        return self.cursor.fetchone()
    
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
        self.cursor.execute('SELECT * FROM cases WHERE id = ?', (case_id,))
        case = self.cursor.fetchone()
        if not case:
            return None
        
        items = json.loads(case[3])
        total_chance = sum(item['chance'] for item in items)
        roll = random.uniform(0, total_chance)
        
        current = 0
        for item in items:
            current += item['chance']
            if roll <= current:
                self.cursor.execute('''
                    INSERT INTO inventory (user_id, item_name, item_price, source)
                    VALUES (?, ?, ?, ?)
                ''', (user_id, item['name'], item['value'], f"case_{case[1]}"))
                self.conn.commit()
                return item
        
        return None
    
    def get_user_stats(self, user_id):
        self.cursor.execute('''
            SELECT 
                COUNT(*) as total_games,
                SUM(CASE WHEN win > 0 THEN 1 ELSE 0 END) as wins,
                SUM(CASE WHEN win = 0 THEN 1 ELSE 0 END) as losses,
                SUM(bet) as total_bets,
                SUM(win) as total_wins
            FROM games 
            WHERE user_id = ?
        ''', (user_id,))
        return self.cursor.fetchone()
    
    def check_daily_bonus(self, user_id):
        today = datetime.now().date()
        self.cursor.execute(
            'SELECT daily_bonus FROM users WHERE user_id = ?',
            (user_id,)
        )
        result = self.cursor.fetchone()
        
        if not result or not result[0] or datetime.strptime(result[0], '%Y-%m-%d').date() < today:
            self.cursor.execute('''
                UPDATE users SET daily_bonus = ?, balance = balance + 5, last_active = CURRENT_TIMESTAMP WHERE user_id = ?
            ''', (today, user_id))
            self.conn.commit()
            return True
        return False
    
    # ================== ЗИМНИЙ МАГАЗИН ==================
    
    WINTER_NFTS = [
        {'name': '🧦 Носок', 'price': 1250},
        {'name': '📦 Змея в коробке', 'price': 1250},
        {'name': '🐍 Змея 2025', 'price': 1250},
        {'name': '🔔 Колокольчики', 'price': 1600},
        {'name': '🎆 Бенгальские огни', 'price': 1300},
        {'name': '🍪 Пряничный человечек', 'price': 1550}
    ]
    
    def buy_winter_nft(self, user_id, item_name):
        """Покупка зимнего NFT за снежинки (без добавления в инвентарь)"""
        for item in self.WINTER_NFTS:
            if item['name'] == item_name:
                user = self.get_user(user_id)
                if user[4] >= item['price']:  # Проверяем снежинки
                    self.update_snowflakes(user_id, -item['price'])
                    return True
        return False
    
    # ================== ЗАЯВКИ НА ВЫВОД NFT ==================
    
    def create_nft_withdrawal(self, user_id, nft_name, snowflakes_cost):
        """Создание заявки на вывод NFT"""
        self.cursor.execute('''
            INSERT INTO nft_withdrawals (user_id, nft_name, snowflakes_cost)
            VALUES (?, ?, ?)
        ''', (user_id, nft_name, snowflakes_cost))
        self.conn.commit()
        return self.cursor.lastrowid
    
    def get_nft_withdrawal(self, withdrawal_id):
        """Получение информации о заявке на NFT"""
        self.cursor.execute('SELECT * FROM nft_withdrawals WHERE id = ?', (withdrawal_id,))
        return self.cursor.fetchone()
    
    def get_pending_nft_withdrawals(self):
        """Получение всех ожидающих заявок на NFT"""
        self.cursor.execute('''
            SELECT w.*, u.username, u.first_name
            FROM nft_withdrawals w
            JOIN users u ON w.user_id = u.user_id
            WHERE w.status = 'pending'
            ORDER BY w.created_at ASC
        ''')
        return self.cursor.fetchall()
    
    def approve_nft_withdrawal(self, withdrawal_id, admin_id):
        """Одобрение заявки на вывод NFT"""
        self.cursor.execute('''
            UPDATE nft_withdrawals 
            SET status = 'approved', admin_id = ?, processed_at = CURRENT_TIMESTAMP
            WHERE id = ? AND status = 'pending'
        ''', (admin_id, withdrawal_id))
        self.conn.commit()
        return self.cursor.rowcount > 0
    
    def reject_nft_withdrawal(self, withdrawal_id, admin_id, reason):
        """Отклонение заявки на вывод NFT с возвратом снежинок"""
        # Получаем информацию о заявке
        self.cursor.execute('SELECT user_id, snowflakes_cost FROM nft_withdrawals WHERE id = ?', (withdrawal_id,))
        withdrawal = self.cursor.fetchone()
        
        if withdrawal:
            user_id, cost = withdrawal
            # Возвращаем снежинки
            self.update_snowflakes(user_id, cost)
        
        # Обновляем статус заявки
        self.cursor.execute('''
            UPDATE nft_withdrawals 
            SET status = 'rejected', admin_id = ?, reject_reason = ?, processed_at = CURRENT_TIMESTAMP
            WHERE id = ? AND status = 'pending'
        ''', (admin_id, reason, withdrawal_id))
        self.conn.commit()
        return self.cursor.rowcount > 0
    
    # ================== ПЛАТЕЖИ ==================
    
    def add_crypto_payment(self, user_id, amount, invoice_id):
        self.cursor.execute('''
            INSERT INTO payments (user_id, amount, method, invoice_id, status)
            VALUES (?, ?, 'crypto', ?, 'pending')
        ''', (user_id, amount, invoice_id))
        self.conn.commit()
    
    def confirm_crypto_payment(self, invoice_id):
        self.cursor.execute('''
            UPDATE payments SET status = 'completed' WHERE invoice_id = ? AND status = 'pending'
        ''', (invoice_id,))
        self.conn.commit()
        
        self.cursor.execute('SELECT user_id, amount FROM payments WHERE invoice_id = ?', (invoice_id,))
        result = self.cursor.fetchone()
        if result:
            user_id, amount = result
            self.update_balance(user_id, amount)
            return user_id, amount
        return None, None
    
    def add_stars_payment(self, user_id, amount, payload):
        self.cursor.execute('''
            INSERT INTO payments (user_id, amount, method, invoice_id, status)
            VALUES (?, ?, 'stars', ?, 'pending')
        ''', (user_id, amount, payload))
        self.conn.commit()
    
    def confirm_stars_payment(self, payload):
        self.cursor.execute('''
            UPDATE payments SET status = 'completed' WHERE invoice_id = ? AND status = 'pending'
        ''', (payload,))
        self.conn.commit()
        
        self.cursor.execute('SELECT user_id, amount FROM payments WHERE invoice_id = ?', (payload,))
        result = self.cursor.fetchone()
        if result:
            user_id, amount = result
            self.update_balance(user_id, amount)
            return user_id, amount
        return None, None
    
    # ================== ВЫВОД ЗВЁЗД ==================
    
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
            SELECT user_id, amount, method, wallet FROM withdrawals WHERE id = ? AND status = 'pending'
        ''', (withdrawal_id,))
        withdrawal = self.cursor.fetchone()
        
        if not withdrawal:
            return False
        
        user_id, amount, method, wallet = withdrawal
        
        user = self.get_user(user_id)
        if user[3] < amount:
            return False
        
        if method == 'crypto':
            if crypto.transfer(wallet, amount):
                self.update_balance(user_id, -amount)
                
                self.cursor.execute('''
                    UPDATE withdrawals 
                    SET status = 'approved', admin_id = ?, processed_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                ''', (admin_id, withdrawal_id))
                
                self.cursor.execute('''
                    UPDATE users SET total_withdrawn = total_withdrawn + ? WHERE user_id = ?
                ''', (amount, user_id))
                
                self.conn.commit()
                return True
            return False
        else:
            self.update_balance(user_id, -amount)
            
            self.cursor.execute('''
                UPDATE withdrawals 
                SET status = 'approved', admin_id = ?, processed_at = CURRENT_TIMESTAMP
                WHERE id = ?
            ''', (admin_id, withdrawal_id))
            
            self.cursor.execute('''
                UPDATE users SET total_withdrawn = total_withdrawn + ? WHERE user_id = ?
            ''', (amount, user_id))
            
            self.conn.commit()
            return True
    
    def reject_withdrawal(self, withdrawal_id, admin_id, reason):
        """Отклонение заявки на вывод с причиной"""
        self.cursor.execute('''
            UPDATE withdrawals 
            SET status = 'rejected', admin_id = ?, reject_reason = ?, processed_at = CURRENT_TIMESTAMP
            WHERE id = ? AND status = 'pending'
        ''', (admin_id, reason, withdrawal_id))
        self.conn.commit()
        return self.cursor.rowcount > 0
    
    def get_user_withdrawals(self, user_id):
        self.cursor.execute('''
            SELECT * FROM withdrawals 
            WHERE user_id = ? 
            ORDER BY created_at DESC
        ''', (user_id,))
        return self.cursor.fetchall()
    
    # ================== ПРОМОКОДЫ ==================
    
    def generate_promocode(self, amount, days_valid, max_uses, created_by):
        """Генерация нового промокода"""
        # Генерируем случайный код
        code = ''.join(random.choices(string.ascii_uppercase + string.digits, k=8))
        expires_at = (datetime.now() + timedelta(days=days_valid)).date()
        
        self.cursor.execute('''
            INSERT INTO promocodes (code, amount, expires_at, max_uses, created_by)
            VALUES (?, ?, ?, ?, ?)
        ''', (code, amount, expires_at, max_uses, created_by))
        self.conn.commit()
        return code
    
    def get_promocode_info(self, code):
        """Получение информации о промокоде"""
        self.cursor.execute('SELECT * FROM promocodes WHERE code = ?', (code,))
        return self.cursor.fetchone()
    
    def activate_promocode(self, user_id, code):
        """Активация промокода пользователем"""
        # Проверяем существование кода
        promo = self.get_promocode_info(code)
        if not promo:
            return {'success': False, 'reason': 'Код не найден'}
        
        # Проверяем срок действия
        if promo[3] and datetime.now().date() > datetime.strptime(promo[3], '%Y-%m-%d').date():
            return {'success': False, 'reason': 'Промокод истёк'}
        
        # Проверяем лимит использований
        if promo[4] > 0 and promo[5] >= promo[4]:
            return {'success': False, 'reason': 'Промокод использован максимальное количество раз'}
        
        # Проверяем, не активировал ли уже пользователь этот код
        self.cursor.execute('SELECT * FROM promocode_uses WHERE user_id = ? AND code = ?', (user_id, code))
        if self.cursor.fetchone():
            return {'success': False, 'reason': 'Вы уже активировали этот промокод'}
        
        # Начисляем бонус
        self.update_balance(user_id, promo[2])
        
        # Записываем использование
        self.cursor.execute('INSERT INTO promocode_uses (user_id, code) VALUES (?, ?)', (user_id, code))
        
        # Обновляем счётчик использований
        self.cursor.execute('UPDATE promocodes SET used_count = used_count + 1 WHERE code = ?', (code,))
        self.conn.commit()
        
        return {'success': True, 'amount': promo[2]}
    
    def get_all_promocodes(self):
        """Получение всех промокодов"""
        self.cursor.execute('SELECT * FROM promocodes ORDER BY created_at DESC')
        return self.cursor.fetchall()
    
    # ================== НАСТРОЙКИ ==================
    
    def get_setting(self, key, default=None):
        self.cursor.execute('SELECT value FROM settings WHERE key = ?', (key,))
        result = self.cursor.fetchone()
        return result[0] if result else default
    
    def set_setting(self, key, value):
        self.cursor.execute('''
            INSERT INTO settings (key, value) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
        ''', (key, value))
        self.conn.commit()
    
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
        total_balance = self.cursor.fetchone()[0] or 0
        
        self.cursor.execute('SELECT SUM(snowflakes) FROM users')
        total_snowflakes = self.cursor.fetchone()[0] or 0
        
        self.cursor.execute('SELECT SUM(total_withdrawn) FROM users')
        total_withdrawn = self.cursor.fetchone()[0] or 0
        
        self.cursor.execute('SELECT COUNT(*) FROM games')
        total_games = self.cursor.fetchone()[0]
        
        return {
            'total_users': total_users,
            'total_balance': total_balance,
            'total_snowflakes': total_snowflakes,
            'total_withdrawn': total_withdrawn,
            'total_games': total_games
        }
    
    def close(self):
        self.conn.close()


# ======================== БОТ ========================

db = Database()

# Шансы игр
GAME_ODDS = {
    'flip': {'win_chance': 45, 'multiplier': 1.7, 'name': '🎲 Орёл и решка'},
    'roulette': {'win_chance': 20, 'multiplier': 4.5, 'name': '💀 Русская рулетка'},
    'wheel': {'win_chance': 8, 'multiplier': 10, 'name': '🎡 Колесо удачи'},
    'mines': {'win_chance': 12, 'multiplier': 7.5, 'name': '💣 Минное поле'},
    'dice': {'win_chance': 30, 'multiplier': 2.5, 'name': '🎲 Кости'},
    'slots': {'win_chance': 25, 'multiplier': 3.0, 'name': '🎰 Слоты'}
}

async def edit_message(query, text, keyboard=None):
    """Универсальная функция для редактирования сообщений"""
    try:
        if query.message.photo:
            if keyboard:
                await query.edit_message_caption(
                    caption=text,
                    parse_mode=ParseMode.MARKDOWN,
                    reply_markup=keyboard
                )
            else:
                await query.edit_message_caption(
                    caption=text,
                    parse_mode=ParseMode.MARKDOWN
                )
        else:
            if keyboard:
                await query.edit_message_text(
                    text,
                    parse_mode=ParseMode.MARKDOWN,
                    reply_markup=keyboard
                )
            else:
                await query.edit_message_text(
                    text,
                    parse_mode=ParseMode.MARKDOWN
                )
    except Exception as e:
        logger.error(f"Error editing message: {e}")
        # Пробуем отправить новое сообщение если редактирование не удалось
        if keyboard:
            await query.message.reply_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=keyboard)
        else:
            await query.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

async def check_ban(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    if user_id in ADMIN_IDS:
        return True
    
    user = db.get_user(user_id)
    if user and user[11] == 1:
        if update.message:
            await update.message.reply_text("❌ Вы заблокированы в этом боте.")
        elif update.callback_query:
            await update.callback_query.edit_message_text("❌ Вы заблокированы в этом боте.")
        return False
    return True

async def check_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    return user_id in ADMIN_IDS

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_ban(update, context):
        return
    
    user = update.effective_user
    user_id = user.id
    referred_by = None
    
    if context.args and context.args[0].startswith('ref'):
        try:
            referred_by = int(context.args[0].replace('ref', ''))
        except:
            pass
    
    db.create_user(user.id, user.username, user.first_name, referred_by)
    user_data = db.get_user(user.id)
    
    keyboard = [
        [
            InlineKeyboardButton("🎰 Казино", callback_data="casino_menu"),
            InlineKeyboardButton("📦 Кейс", callback_data="case_menu")
        ],
        [
            InlineKeyboardButton("❄️ Зимний магазин", callback_data="winter_shop"),
            InlineKeyboardButton("🎁 Ежедневный бонус", callback_data="daily_bonus")
        ],
        [
            InlineKeyboardButton("👥 Реф ссылка", callback_data="referral"),
            InlineKeyboardButton("👤 Профиль", callback_data="profile")
        ],
        [
            InlineKeyboardButton("💰 Пополнить", callback_data="deposit_menu"),
            InlineKeyboardButton("💸 Вывод", callback_data="withdraw_menu")
        ],
        [
            InlineKeyboardButton("🎟️ Активировать промокод", callback_data="activate_promo"),
            InlineKeyboardButton("📊 Правила", callback_data="rules")
        ]
    ]
    
    if user_id in ADMIN_IDS:
        keyboard.append([InlineKeyboardButton("⚙️ Админ-панель", callback_data="admin_panel")])
    
    text = (
        f"🌟 *Добро пожаловать в {BOT_NAME}!*\n\n"
        f"🆔 *ID:* {user.id}\n"
        f"👤 *Имя:* {user.first_name}\n"
        f"💰 *Баланс:* {user_data[3]} ★\n"
        f"❄️ *Снежинки:* {user_data[4]} ✨\n\n"
        f"Выберите действие:"
    )
    
    if WELCOME_IMAGE_ID:
        await update.message.reply_photo(
            photo=WELCOME_IMAGE_ID,
            caption=text,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    else:
        await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(keyboard))

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    user_id = update.effective_user.id
    user = db.get_user(user_id)
    
    if not user:
        await query.edit_message_text("❌ Ошибка: пользователь не найден")
        return
    
    if user[11] == 1 and user_id not in ADMIN_IDS:
        await query.edit_message_text("❌ Вы заблокированы в этом боте.")
        return
    
    data = query.data
    
    # ================== ПРОФИЛЬ ==================
    
    if data == "profile":
        stats = db.get_user_stats(user_id)
        withdrawals = db.get_user_withdrawals(user_id)
        total_withdrawn = sum(w[2] for w in withdrawals if w[3] == 'approved')
        
        text = (
            f"👤 *Ваш профиль*\n\n"
            f"🆔 *ID:* {user_id}\n"
            f"👤 *Имя:* {user[2]}\n"
            f"📛 *Username:* @{user[1] or 'нет'}\n"
            f"💰 *Баланс:* {user[3]} ★\n"
            f"❄️ *Снежинки:* {user[4]} ✨\n"
            f"👥 *Рефералов:* {user[5]}\n\n"
            f"💳 *CryptoBot ID:* {user[8] or 'не указан'}\n"
            f"📱 *Telegram Username:* @{user[9] or 'не указан'}\n\n"
            f"📊 *Статистика игр:*\n"
            f"• Всего игр: {stats[0] if stats else 0}\n"
            f"• Выиграно: {stats[1] if stats else 0}\n"
            f"• Проиграно: {stats[2] if stats else 0}\n"
            f"• Сумма ставок: {stats[3] if stats else 0} ★\n"
            f"• Сумма выигрышей: {stats[4] if stats else 0} ★\n\n"
            f"💸 *Всего выведено:* {total_withdrawn} ★"
        )
        
        keyboard = [[InlineKeyboardButton("◀️ Главное меню", callback_data="main_menu")]]
        await edit_message(query, text, InlineKeyboardMarkup(keyboard))
    
    # ================== ПРАВИЛА ==================
    
    elif data == "rules":
        text = (
            f"📜 *Правила использования бота {BOT_NAME}*\n\n"
            f"🚫 *Запрещено:*\n"
            f"• 🤖 Использование ботов для накрутки\n"
            f"• 👥 Создание мультикакаунтов\n"
            f"• 🎭 Обман системы реферальной программы\n\n"
            f"✅ *Разрешено:*\n"
            f"• 👋 Приглашать реальных друзей\n"
            f"• 🎮 Активно участвовать в проекте\n\n"
            f"⚡ *Нарушение правил ведет к:*\n"
            f"• 🔒 Блокировке аккаунта\n"
            f"• 💰 Обнулению баланса\n\n"
            f"🎉 *Удачной игры!*"
        )
        
        keyboard = [[InlineKeyboardButton("◀️ Главное меню", callback_data="main_menu")]]
        await edit_message(query, text, InlineKeyboardMarkup(keyboard))
    
    # ================== КАЗИНО ==================
    
    elif data == "casino_menu":
        text = "🎰 *Казино*\n\nВыберите игру:"
        keyboard = [
            [
                InlineKeyboardButton("🎲 Орёл и решка (x1.7)", callback_data="game_flip"),
                InlineKeyboardButton("💀 Русская рулетка (x4.5)", callback_data="game_roulette")
            ],
            [
                InlineKeyboardButton("🎡 Колесо удачи (x10)", callback_data="game_wheel"),
                InlineKeyboardButton("💣 Минное поле (x7.5)", callback_data="game_mines")
            ],
            [
                InlineKeyboardButton("🎲 Кости (x2.5)", callback_data="game_dice"),
                InlineKeyboardButton("🎰 Слоты (x3.0)", callback_data="game_slots")
            ],
            [InlineKeyboardButton("◀️ Главное меню", callback_data="main_menu")]
        ]
        await edit_message(query, text, InlineKeyboardMarkup(keyboard))
    
    # ================== ПОПОЛНЕНИЕ ==================
    
    elif data == "deposit_menu":
        text = (
            f"💰 *Пополнение баланса*\n\n"
            f"Выберите способ пополнения:\n\n"
            f"⭐ Telegram Stars — 1:1\n"
            f"💎 CryptoBot — 1★ = 1.3 руб\n"
            f"Минимальная сумма: 10 ★"
        )
        
        keyboard = [
            [InlineKeyboardButton("⭐ Telegram Stars", callback_data="deposit_stars_menu")],
            [InlineKeyboardButton("💎 CryptoBot", callback_data="deposit_crypto_menu")],
            [InlineKeyboardButton("◀️ Главное меню", callback_data="main_menu")]
        ]
        
        await edit_message(query, text, InlineKeyboardMarkup(keyboard))
    
    elif data == "deposit_stars_menu":
        text = "⭐ *Пополнение Telegram Stars*\n\nВыберите сумму:"
        keyboard = [
            [
                InlineKeyboardButton("10 ⭐", callback_data="stars_10"),
                InlineKeyboardButton("25 ⭐", callback_data="stars_25"),
                InlineKeyboardButton("50 ⭐", callback_data="stars_50")
            ],
            [
                InlineKeyboardButton("100 ⭐", callback_data="stars_100"),
                InlineKeyboardButton("250 ⭐", callback_data="stars_250"),
                InlineKeyboardButton("500 ⭐", callback_data="stars_500")
            ],
            [InlineKeyboardButton("◀️ Назад", callback_data="deposit_menu")]
        ]
        await edit_message(query, text, InlineKeyboardMarkup(keyboard))
    
    elif data.startswith("stars_"):
        amount = int(data.replace("stars_", ""))
        
        prices = [LabeledPrice(label="XTR", amount=amount)]
        
        await context.bot.send_invoice(
            chat_id=user_id,
            title=f"Пополнение {BOT_NAME}",
            description=f"Пополнение на {amount} ⭐",
            payload=f"stars_{user_id}_{amount}_{int(time.time())}",
            provider_token="",
            currency="XTR",
            prices=prices
        )
    
    elif data == "deposit_crypto_menu":
        text = "💎 *Пополнение через CryptoBot*\n\nВыберите сумму:"
        keyboard = [
            [
                InlineKeyboardButton("10 ★ (13 руб)", callback_data="crypto_10"),
                InlineKeyboardButton("25 ★ (32.5 руб)", callback_data="crypto_25"),
                InlineKeyboardButton("50 ★ (65 руб)", callback_data="crypto_50")
            ],
            [
                InlineKeyboardButton("100 ★ (130 руб)", callback_data="crypto_100"),
                InlineKeyboardButton("250 ★ (325 руб)", callback_data="crypto_250"),
                InlineKeyboardButton("500 ★ (650 руб)", callback_data="crypto_500")
            ],
            [InlineKeyboardButton("◀️ Назад", callback_data="deposit_menu")]
        ]
        await edit_message(query, text, InlineKeyboardMarkup(keyboard))
    
    elif data.startswith("crypto_"):
        stars_amount = int(data.replace("crypto_", ""))
        
        invoice = crypto.create_invoice(stars_amount, "TON", f"Пополнение {BOT_NAME} на {stars_amount} ★")
        
        if invoice:
            pay_url = invoice['pay_url']
            invoice_id = invoice['invoice_id']
            
            db.add_crypto_payment(user_id, stars_amount, invoice_id)
            
            rub_amount = stars_amount * RUB_PER_STAR
            ton_amount = round(stars_amount * TON_PER_STAR, 2)
            
            text = (
                f"💎 *Пополнение на {stars_amount} ★*\n\n"
                f"💰 Сумма: {rub_amount:.2f} руб\n"
                f"💎 К оплате: {ton_amount} TON\n"
                f"🔗 [Оплатить]({pay_url})\n\n"
                f"После оплаты баланс зачислится автоматически."
            )
            keyboard = [[InlineKeyboardButton("◀️ Главное меню", callback_data="main_menu")]]
            await edit_message(query, text, InlineKeyboardMarkup(keyboard))
            
            for admin_id in ADMIN_IDS:
                try:
                    await context.bot.send_message(
                        admin_id,
                        f"💰 *Создан счет*\n\n👤 @{user[1] or user_id}\n💎 {stars_amount} ★\n💵 {rub_amount:.2f} руб\n🆔 {invoice_id}",
                        parse_mode=ParseMode.MARKDOWN
                    )
                except:
                    pass
        else:
            await edit_message(query, "❌ Ошибка создания счета")
    
    # ================== ВЫВОД ==================
    
    elif data == "withdraw_menu":
        text = (
            f"💸 *Вывод средств*\n\n"
            f"💰 *Баланс:* {user[3]} ★\n"
            f"📱 *Telegram:* @{user[9] or 'не указан'}\n"
            f"💳 *CryptoBot ID:* {user[8] or 'не указан'}\n\n"
            f"Минимум: 50 ★\n"
            f"Комиссия: 0%"
        )
        
        keyboard = [
            [InlineKeyboardButton("📱 На Telegram", callback_data="withdraw_telegram")],
            [InlineKeyboardButton("💳 На CryptoBot", callback_data="withdraw_crypto")],
            [InlineKeyboardButton("⚙️ Настройки", callback_data="withdraw_settings")],
            [InlineKeyboardButton("◀️ Главное меню", callback_data="main_menu")]
        ]
        
        await edit_message(query, text, InlineKeyboardMarkup(keyboard))
    
    elif data == "withdraw_settings":
        keyboard = [
            [InlineKeyboardButton("📱 Указать Telegram", callback_data="set_telegram")],
            [InlineKeyboardButton("💳 Указать CryptoBot ID", callback_data="set_crypto")],
            [InlineKeyboardButton("◀️ Назад", callback_data="withdraw_menu")]
        ]
        
        await edit_message(
            query,
            "⚙️ *Настройки*\n\nВыберите что хотите указать:",
            InlineKeyboardMarkup(keyboard)
        )
    
    elif data == "set_telegram":
        context.user_data['awaiting'] = 'telegram'
        await edit_message(
            query,
            "📱 *Укажите ваш Telegram Username*\n\nОтправьте username (без @):"
        )
    
    elif data == "set_crypto":
        context.user_data['awaiting'] = 'crypto'
        await edit_message(
            query,
            "💳 *Укажите ваш CryptoBot ID*\n\nОтправьте ID (только цифры):"
        )
    
    elif data == "withdraw_telegram":
        if user[3] < 50:
            await edit_message(query, "❌ Минимум 50 ★")
            return
        
        if not user[9]:
            await edit_message(
                query,
                "❌ Сначала укажите Telegram Username",
                InlineKeyboardMarkup([[InlineKeyboardButton("⚙️ Указать", callback_data="set_telegram")]])
            )
            return
        
        context.user_data['awaiting'] = 'withdraw_telegram_amount'
        await edit_message(
            query,
            f"📱 *Вывод на @{user[9]}*\n\nБаланс: {user[3]} ★\nВведите сумму:"
        )
    
    elif data == "withdraw_crypto":
        if user[3] < 50:
            await edit_message(query, "❌ Минимум 50 ★")
            return
        
        if not user[8]:
            await edit_message(
                query,
                "❌ Сначала укажите CryptoBot ID",
                InlineKeyboardMarkup([[InlineKeyboardButton("⚙️ Указать", callback_data="set_crypto")]])
            )
            return
        
        context.user_data['awaiting'] = 'withdraw_crypto_amount'
        await edit_message(
            query,
            f"💳 *Вывод на CryptoBot ID {user[8]}*\n\nБаланс: {user[3]} ★\nВведите сумму:"
        )
    
    # ================== ЗИМНИЙ МАГАЗИН ==================
    
    elif data == "winter_shop":
        text = (
            f"❄️ *Зимний магазин*\n\n"
            f"Ваши снежинки: {user[4]} ✨\n\n"
            f"**Доступно:**\n"
        )
        
        for item in db.WINTER_NFTS:
            text += f"• {item['name']} — {item['price']} ✨\n"
        
        text += "\n*Как получить снежинки?*\n• За проигрыш +0.5 ✨\n• За реферала +5 ✨"
        
        keyboard = []
        for item in db.WINTER_NFTS:
            keyboard.append([InlineKeyboardButton(f"🎁 {item['name']}", callback_data=f"buy_{item['name']}")])
        
        keyboard.append([InlineKeyboardButton("◀️ Главное меню", callback_data="main_menu")])
        await edit_message(query, text, InlineKeyboardMarkup(keyboard))
    
    elif data.startswith("buy_"):
        item_name = data.replace("buy_", "")
        
        for item in db.WINTER_NFTS:
            if item['name'] == item_name:
                if user[4] >= item['price']:
                    if db.buy_winter_nft(user_id, item_name):
                        # Создаем заявку на вывод NFT
                        withdraw_id = db.create_nft_withdrawal(user_id, item_name, item['price'])
                        
                        text = (
                            f"✅ *Покупка совершена!*\n\n"
                            f"🎁 {item_name}\n"
                            f"❄️ Цена: {item['price']} ✨\n\n"
                            f"📤 *Вывести NFT*\n"
                            f"Нажмите кнопку ниже, чтобы отправить заявку на вывод."
                        )
                        
                        keyboard = [
                            [InlineKeyboardButton(f"📤 Вывести {item_name}", callback_data=f"withdraw_nft_{withdraw_id}")],
                            [InlineKeyboardButton("◀️ В магазин", callback_data="winter_shop")]
                        ]
                        await edit_message(query, text, InlineKeyboardMarkup(keyboard))
                        return
                    else:
                        await edit_message(query, "❌ Ошибка")
                else:
                    await edit_message(query, f"❌ Не хватает {item['price'] - user[4]} ✨")
                break
    
    elif data.startswith("withdraw_nft_"):
        withdraw_id = int(data.replace("withdraw_nft_", ""))
        withdrawal = db.get_nft_withdrawal(withdraw_id)
        
        if not withdrawal or withdrawal[3] != 'pending':
            await edit_message(query, "❌ Заявка уже обработана")
            return
        
        # Уведомление админу
        for admin_id in ADMIN_IDS:
            try:
                keyboard_admin = [
                    [InlineKeyboardButton(f"✅ Вывести #{withdraw_id}", callback_data=f"approve_nft_{withdraw_id}"),
                     InlineKeyboardButton(f"❌ Отказать #{withdraw_id}", callback_data=f"reject_nft_{withdraw_id}")]
                ]
                
                await context.bot.send_message(
                    admin_id,
                    f"🖼️ *Новая заявка на вывод NFT*\n\n"
                    f"👤 Пользователь: @{user[1] or user_id}\n"
                    f"🎁 NFT: {withdrawal[2]}\n"
                    f"❄️ Куплен за: {withdrawal[3]} ✨\n"
                    f"🆔 Заявка: #{withdraw_id}",
                    parse_mode=ParseMode.MARKDOWN,
                    reply_markup=InlineKeyboardMarkup(keyboard_admin)
                )
            except:
                pass
        
        await edit_message(
            query,
            f"✅ *Заявка на вывод отправлена!*\n\n"
            f"🎁 {withdrawal[2]}\n"
            f"🆔 Номер заявки: #{withdraw_id}\n\n"
            f"⏳ Ожидайте подтверждения администратора.\n"
            f"После одобрения NFT придёт в течение 10-15 минут."
        )
    
    # ================== КЕЙС ==================
    
    elif data == "case_menu":
        cases = db.get_cases()
        case = cases[0] if cases else None
        
        if case:
            items = json.loads(case[3])
            text = (
                f"📦 *Кейс {BOT_NAME}*\n\n"
                f"💰 Цена: {case[2]} ★\n\n"
                f"**Шансы:**\n"
            )
            for item in items:
                text += f"• {item['name']} — {item['chance']}%\n"
            
            keyboard = [
                [InlineKeyboardButton(f"📦 Открыть ({case[2]} ★)", callback_data=f"open_case_{case[0]}")],
                [InlineKeyboardButton("◀️ Главное меню", callback_data="main_menu")]
            ]
            
            if CASE_IMAGE_ID:
                await query.edit_message_media(
                    media=InputMediaPhoto(media=CASE_IMAGE_ID, caption=text, parse_mode=ParseMode.MARKDOWN),
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )
            else:
                await edit_message(query, text, InlineKeyboardMarkup(keyboard))
    
    elif data.startswith("open_case_"):
        case_id = int(data.replace("open_case_", ""))
        case_price = 35
        
        if user[3] < case_price:
            await edit_message(query, f"❌ Нужно {case_price} ★")
            return
        
        db.update_balance(user_id, -case_price)
        result = db.open_case(case_id, user_id)
        
        if result:
            await edit_message(query, f"🎉 Вы выиграли: {result['name']}!")
        else:
            await edit_message(query, "❌ Ошибка")
    
    # ================== РЕФЕРАЛЫ ==================
    
    elif data == "referral":
        ref_link = f"https://t.me/{(await context.bot.get_me()).username}?start=ref{user_id}"
        
        text = (
            f"👥 *Рефералы*\n\n"
            f"🔗 `{ref_link}`\n\n"
            f"Приглашено: {user[5]}\n"
            f"Заработано: {user[5] * 5} ✨\n\n"
            f"За каждого друга: +5 ✨"
        )
        
        keyboard = [[InlineKeyboardButton("◀️ Главное меню", callback_data="main_menu")]]
        await edit_message(query, text, InlineKeyboardMarkup(keyboard))
    
    # ================== ЕЖЕДНЕВНЫЙ БОНУС ==================
    
    elif data == "daily_bonus":
        if db.check_daily_bonus(user_id):
            await edit_message(query, "🎁 +5 ★")
        else:
            await edit_message(query, "❌ Бонус уже получен")
    
    # ================== ПРОМОКОДЫ ==================
    
    elif data == "activate_promo":
        context.user_data['awaiting'] = 'promocode'
        await edit_message(
            query,
            "🎟️ *Активация промокода*\n\nВведите код:"
        )
    
    # ================== АДМИН-ПАНЕЛЬ ==================
    
    elif data == "admin_panel":
        if user_id not in ADMIN_IDS:
            await edit_message(query, "❌ Нет прав")
            return
        
        stats = db.get_total_stats()
        pending_stars = len(db.get_pending_withdrawals())
        pending_nft = len(db.get_pending_nft_withdrawals())
        
        text = (
            f"⚙️ *Админ-панель*\n\n"
            f"📊 *Статистика:*\n"
            f"👥 Пользователей: {stats['total_users']}\n"
            f"💰 Общий баланс: {stats['total_balance']} ★\n"
            f"❄️ Снежинок: {stats['total_snowflakes']} ✨\n"
            f"💸 Выведено: {stats['total_withdrawn']} ★\n"
            f"🎮 Игр: {stats['total_games']}\n\n"
            f"⏳ *Заявок:*\n"
            f"💎 На вывод звёзд: {pending_stars}\n"
            f"🖼️ На вывод NFT: {pending_nft}"
        )
        
        keyboard = [
            [InlineKeyboardButton("👥 Пользователи", callback_data="admin_users_page_1_balance_desc")],
            [InlineKeyboardButton("⏳ Заявки на вывод звёзд", callback_data="admin_withdrawals")],
            [InlineKeyboardButton("🖼️ Заявки на вывод NFT", callback_data="admin_nft_withdrawals")],
            [InlineKeyboardButton("🎟️ Промокоды", callback_data="admin_promocodes")],
            [InlineKeyboardButton("🔨 Баны", callback_data="admin_bans")],
            [InlineKeyboardButton("📢 Рассылка", callback_data="admin_broadcast")],
            [InlineKeyboardButton("🖼️ Картинки", callback_data="admin_images")],
            [InlineKeyboardButton("◀️ Главное меню", callback_data="main_menu")]
        ]
        
        await edit_message(query, text, InlineKeyboardMarkup(keyboard))
    
    # ================== УПРАВЛЕНИЕ ПОЛЬЗОВАТЕЛЯМИ (ПАГИНАЦИЯ, ПОИСК, СОРТИРОВКА) ==================
    
    elif data.startswith("admin_users_page_"):
        if user_id not in ADMIN_IDS:
            return
        
        # Формат: admin_users_page_1_balance_desc
        parts = data.replace("admin_users_page_", "").split('_')
        page = int(parts[0])
        sort_by = parts[1] if len(parts) > 1 else 'date'
        order = parts[2] if len(parts) > 2 else 'desc'
        
        users_per_page = 20
        total_users = db.get_total_users_count()
        total_pages = (total_users + users_per_page - 1) // users_per_page
        
        if page < 1:
            page = 1
        if page > total_pages:
            page = total_pages
        
        offset = (page - 1) * users_per_page
        users = db.get_all_users(sort_by=sort_by, order=order, limit=users_per_page, offset=offset)
        
        text = f"👥 *Страница {page} из {total_pages}*\n\n"
        
        for u in users:
            status = "🔴" if u[5] == 1 else "🟢"
            admin = "👑" if u[6] == 1 else ""
            last_active = u[8][:10] if u[8] else "никогда"
            text += f"{status}{admin} {u[2]} (@{u[1]}) — {u[3]} ★ | ✨ {u[4]} | {last_active}\n"
        
        keyboard = []
        
        # Кнопки сортировки
        sort_row = [
            InlineKeyboardButton("📅", callback_data=f"admin_users_page_1_date_desc"),
            InlineKeyboardButton("💰", callback_data=f"admin_users_page_1_balance_desc"),
            InlineKeyboardButton("✨", callback_data=f"admin_users_page_1_snowflakes_desc"),
            InlineKeyboardButton("👥", callback_data=f"admin_users_page_1_referrals_desc")
        ]
        keyboard.append(sort_row)
        
        # Кнопки навигации
        nav_row = []
        if page > 1:
            nav_row.append(InlineKeyboardButton("◀️", callback_data=f"admin_users_page_{page-1}_{sort_by}_{order}"))
        nav_row.append(InlineKeyboardButton(f"{page}/{total_pages}", callback_data="noop"))
        if page < total_pages:
            nav_row.append(InlineKeyboardButton("▶️", callback_data=f"admin_users_page_{page+1}_{sort_by}_{order}"))
        keyboard.append(nav_row)
        
        # Кнопка поиска
        keyboard.append([InlineKeyboardButton("🔍 Поиск пользователя", callback_data="admin_search_user")])
        keyboard.append([InlineKeyboardButton("◀️ Назад", callback_data="admin_panel")])
        
        await edit_message(query, text, InlineKeyboardMarkup(keyboard))
    
    elif data == "admin_search_user":
        if user_id not in ADMIN_IDS:
            return
        
        context.user_data['awaiting'] = 'search_user'
        await edit_message(
            query,
            "🔍 *Поиск пользователя*\n\nВведите ID или Username:"
        )
    
    # ================== ЗАЯВКИ НА ВЫВОД ЗВЁЗД С ПРИЧИНОЙ ==================
    
    elif data == "admin_withdrawals":
        if user_id not in ADMIN_IDS:
            return
        
        withdrawals = db.get_pending_withdrawals()
        
        if not withdrawals:
            await edit_message(
                query,
                "✅ Нет ожидающих заявок на вывод звёзд",
                InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Назад", callback_data="admin_panel")]])
            )
            return
        
        text = "⏳ *Заявки на вывод звёзд:*\n\n"
        keyboard = []
        
        for w in withdrawals[:5]:
            method_emoji = "📱" if w[3] == 'telegram' else "💳"
            text += (
                f"🆔 #{w[0]}\n"
                f"👤 @{w[7]}\n"
                f"{method_emoji} {w[4]}\n"
                f"💰 {w[2]} ★\n"
                f"🕐 {w[6][:16]}\n\n"
            )
            keyboard.append([
                InlineKeyboardButton(f"✅ #{w[0]}", callback_data=f"approve_withdrawal_{w[0]}"),
                InlineKeyboardButton(f"❌ #{w[0]}", callback_data=f"reject_withdrawal_{w[0]}")
            ])
        
        keyboard.append([InlineKeyboardButton("◀️ Назад", callback_data="admin_panel")])
        await edit_message(query, text, InlineKeyboardMarkup(keyboard))
    
    elif data.startswith("reject_withdrawal_"):
        if user_id not in ADMIN_IDS:
            return
        
        withdrawal_id = int(data.replace("reject_withdrawal_", ""))
        context.user_data['reject_withdrawal_id'] = withdrawal_id
        context.user_data['awaiting'] = 'reject_withdrawal_reason'
        
        await edit_message(
            query,
            f"❌ *Отклонение заявки #{withdrawal_id}*\n\nНапишите причину отказа:"
        )
    
    elif data.startswith("approve_withdrawal_"):
        if user_id not in ADMIN_IDS:
            return
        
        withdrawal_id = int(data.replace("approve_withdrawal_", ""))
        
        if db.approve_withdrawal(withdrawal_id, user_id):
            await edit_message(query, f"✅ Заявка #{withdrawal_id} одобрена")
            
            # Уведомляем пользователя
            db.cursor.execute('SELECT user_id, amount FROM withdrawals WHERE id = ?', (withdrawal_id,))
            w_user_id, amount = db.cursor.fetchone()
            try:
                await context.bot.send_message(
                    w_user_id,
                    f"✅ *Заявка на вывод одобрена!*\n\n"
                    f"💰 Сумма: {amount} ★\n"
                    f"⏳ В течение 10-15 минут средства поступят.",
                    parse_mode=ParseMode.MARKDOWN
                )
            except:
                pass
        else:
            await edit_message(query, f"❌ Ошибка")
    
    # ================== ЗАЯВКИ НА ВЫВОД NFT ==================
    
    elif data == "admin_nft_withdrawals":
        if user_id not in ADMIN_IDS:
            return
        
        withdrawals = db.get_pending_nft_withdrawals()
        
        if not withdrawals:
            await edit_message(
                query,
                "✅ Нет ожидающих заявок на вывод NFT",
                InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Назад", callback_data="admin_panel")]])
            )
            return
        
        text = "🖼️ *Заявки на вывод NFT:*\n\n"
        keyboard = []
        
        for w in withdrawals[:5]:
            text += (
                f"🆔 #{w[0]}\n"
                f"👤 @{w[7]}\n"
                f"🎁 {w[2]}\n"
                f"❄️ {w[3]} ✨\n"
                f"🕐 {w[6][:16]}\n\n"
            )
            keyboard.append([
                InlineKeyboardButton(f"✅ #{w[0]}", callback_data=f"approve_nft_{w[0]}"),
                InlineKeyboardButton(f"❌ #{w[0]}", callback_data=f"reject_nft_{w[0]}")
            ])
        
        keyboard.append([InlineKeyboardButton("◀️ Назад", callback_data="admin_panel")])
        await edit_message(query, text, InlineKeyboardMarkup(keyboard))
    
    elif data.startswith("approve_nft_"):
        if user_id not in ADMIN_IDS:
            return
        
        withdrawal_id = int(data.replace("approve_nft_", ""))
        
        if db.approve_nft_withdrawal(withdrawal_id, user_id):
            await edit_message(query, f"✅ Заявка #{withdrawal_id} одобрена")
            
            # Уведомляем пользователя
            withdrawal = db.get_nft_withdrawal(withdrawal_id)
            try:
                await context.bot.send_message(
                    withdrawal[1],
                    f"✅ *Заявка на вывод NFT одобрена!*\n\n"
                    f"🎁 {withdrawal[2]}\n"
                    f"🆔 Номер заявки: #{withdrawal_id}\n\n"
                    f"⏳ В течение 10-15 минут NFT придёт вам в Telegram.\n"
                    f"Проверьте личные сообщения!",
                    parse_mode=ParseMode.MARKDOWN
                )
            except:
                pass
        else:
            await edit_message(query, f"❌ Ошибка")
    
    elif data.startswith("reject_nft_"):
        if user_id not in ADMIN_IDS:
            return
        
        withdrawal_id = int(data.replace("reject_nft_", ""))
        context.user_data['reject_nft_id'] = withdrawal_id
        context.user_data['awaiting'] = 'reject_nft_reason'
        
        await edit_message(
            query,
            f"❌ *Отклонение заявки #{withdrawal_id}*\n\nНапишите причину отказа:"
        )
    
    # ================== ПРОМОКОДЫ В АДМИНКЕ ==================
    
    elif data == "admin_promocodes":
        if user_id not in ADMIN_IDS:
            return
        
        promocodes = db.get_all_promocodes()
        
        text = "🎟️ *Промокоды*\n\n"
        
        if promocodes:
            for p in promocodes[:10]:
                expiry = p[3] or "никогда"
                text += f"• `{p[1]}` — {p[2]} ★ | использован {p[5]}/{p[4]} | до {expiry}\n"
        else:
            text += "Нет созданных промокодов\n"
        
        keyboard = [
            [InlineKeyboardButton("➕ Создать промокод", callback_data="admin_create_promo")],
            [InlineKeyboardButton("◀️ Назад", callback_data="admin_panel")]
        ]
        
        await edit_message(query, text, InlineKeyboardMarkup(keyboard))
    
    elif data == "admin_create_promo":
        if user_id not in ADMIN_IDS:
            return
        
        context.user_data['promo_step'] = 'amount'
        context.user_data['awaiting'] = 'promo_amount'
        await edit_message(
            query,
            "🎟️ *Создание промокода*\n\nВведите сумму в ★:"
        )
    
    # ================== КАРТИНКИ ==================
    
    elif data == "admin_images":
        if user_id not in ADMIN_IDS:
            return
        
        text = (
            f"🖼️ *Картинки*\n\n"
            f"Приветствие: {'✅' if WELCOME_IMAGE_ID else '❌'}\n"
            f"Кейс: {'✅' if CASE_IMAGE_ID else '❌'}\n\n"
            f"Выберите:"
        )
        
        keyboard = [
            [InlineKeyboardButton("🖼️ Загрузить приветствие", callback_data="upload_welcome")],
            [InlineKeyboardButton("🖼️ Загрузить кейс", callback_data="upload_case")],
            [InlineKeyboardButton("◀️ Назад", callback_data="admin_panel")]
        ]
        
        await edit_message(query, text, InlineKeyboardMarkup(keyboard))
    
    elif data == "upload_welcome":
        if user_id not in ADMIN_IDS:
            return
        
        context.user_data['awaiting'] = 'upload_welcome'
        await edit_message(
            query,
            "🖼️ *Загрузите картинку для приветствия*\n\nОтправьте фото:"
        )
    
    elif data == "upload_case":
        if user_id not in ADMIN_IDS:
            return
        
        context.user_data['awaiting'] = 'upload_case'
        await edit_message(
            query,
            "🖼️ *Загрузите картинку для кейса*\n\nОтправьте фото:"
        )
    
    # ================== БАНЫ ==================
    
    elif data == "admin_bans":
        if user_id not in ADMIN_IDS:
            return
        
        banned = db.get_banned_users()
        
        if not banned:
            await edit_message(
                query,
                "✅ Нет забаненных",
                InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Назад", callback_data="admin_panel")]])
            )
            return
        
        text = "🔨 *Забанены:*\n\n"
        keyboard = []
        
        for b in banned[:10]:
            text += f"• {b[2]} (@{b[1]}) — ID: {b[0]}\n"
            keyboard.append([InlineKeyboardButton(f"✅ Разбанить {b[0]}", callback_data=f"unban_{b[0]}")])
        
        keyboard.append([InlineKeyboardButton("◀️ Назад", callback_data="admin_panel")])
        await edit_message(query, text, InlineKeyboardMarkup(keyboard))
    
    elif data.startswith("unban_"):
        if user_id not in ADMIN_IDS:
            return
        
        ban_user_id = int(data.replace("unban_", ""))
        
        if db.unban_user(user_id, ban_user_id):
            await edit_message(query, f"✅ Пользователь {ban_user_id} разбанен")
        else:
            await edit_message(query, "❌ Ошибка")
    
    # ================== РАССЫЛКА ==================
    
    elif data == "admin_broadcast":
        if user_id not in ADMIN_IDS:
            return
        
        context.user_data['awaiting'] = 'broadcast'
        await edit_message(
            query,
            "📢 *Рассылка*\n\nОтправьте сообщение (можно с фото):"
        )
    
    # ================== ИГРЫ ==================
    
    elif data in ['game_flip', 'game_roulette', 'game_wheel', 'game_mines', 'game_dice', 'game_slots']:
        game_key = data.replace('game_', '')
        context.user_data['game'] = game_key
        
        text = f"🎮 *{GAME_ODDS[game_key]['name']}*\n\n💰 Баланс: {user[3]} ★\n\nВыберите ставку:"
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("10 ★", callback_data="bet_10"),
                InlineKeyboardButton("25 ★", callback_data="bet_25"),
                InlineKeyboardButton("50 ★", callback_data="bet_50")
            ],
            [
                InlineKeyboardButton("100 ★", callback_data="bet_100"),
                InlineKeyboardButton("250 ★", callback_data="bet_250"),
                InlineKeyboardButton("500 ★", callback_data="bet_500")
            ],
            [InlineKeyboardButton("◀️ Назад", callback_data="casino_menu")]
        ])
        await edit_message(query, text, keyboard)
    
    elif data.startswith("bet_"):
        bet = int(data.replace("bet_", ""))
        game = context.user_data.get('game', 'flip')
        
        if bet > user[3]:
            await edit_message(query, f"❌ Недостаточно! Баланс: {user[3]} ★")
            return
        
        odds = GAME_ODDS[game]
        
        db.update_balance(user_id, -bet)
        
        if game == 'flip':
            msg = await context.bot.send_dice(chat_id=user_id, emoji='🪙')
            result = msg.dice.value
            win = (result == 1)  # 1 - орёл, 2 - решка
        else:
            win_chance = odds['win_chance']
            roll = random.randint(1, 100)
            win = roll <= win_chance
        
        if win:
            win_amount = int(bet * odds['multiplier'])
            db.update_balance(user_id, win_amount)
            db.add_game(user_id, game, bet, odds['multiplier'], win_amount, 'win')
            await edit_message(query, f"🎉 *Выигрыш!*\n\n💰 {win_amount} ★ (x{odds['multiplier']})")
        else:
            db.add_lost_stars(user_id, bet)
            db.add_game(user_id, game, bet, 0, 0, 'lose')
            await edit_message(query, f"😢 *Проигрыш*\n\n✨ +{int(bet * 0.5)} снежинок")
    
    # ================== ГЛАВНОЕ МЕНЮ ==================
    
    elif data == "main_menu":
        keyboard = [
            [
                InlineKeyboardButton("🎰 Казино", callback_data="casino_menu"),
                InlineKeyboardButton("📦 Кейс", callback_data="case_menu")
            ],
            [
                InlineKeyboardButton("❄️ Зимний магазин", callback_data="winter_shop"),
                InlineKeyboardButton("🎁 Бонус", callback_data="daily_bonus")
            ],
            [
                InlineKeyboardButton("👥 Рефералы", callback_data="referral"),
                InlineKeyboardButton("👤 Профиль", callback_data="profile")
            ],
            [
                InlineKeyboardButton("💰 Пополнить", callback_data="deposit_menu"),
                InlineKeyboardButton("💸 Вывод", callback_data="withdraw_menu")
            ],
            [
                InlineKeyboardButton("🎟️ Промокод", callback_data="activate_promo"),
                InlineKeyboardButton("📊 Правила", callback_data="rules")
            ]
        ]
        
        if user_id in ADMIN_IDS:
            keyboard.append([InlineKeyboardButton("⚙️ Админ-панель", callback_data="admin_panel")])
        
        text = (
            f"🌟 *{BOT_NAME}*\n\n"
            f"🆔 ID: {user_id}\n"
            f"👤 Имя: {user[2]}\n"
            f"💰 Баланс: {user[3]} ★\n"
            f"❄️ Снежинки: {user[4]} ✨"
        )
        
        await edit_message(query, text, InlineKeyboardMarkup(keyboard))

async def precheckout_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.pre_checkout_query
    if query.invoice_payload.startswith("stars_"):
        await query.answer(ok=True)
    else:
        await query.answer(ok=False, error_message="Ошибка")

async def successful_payment_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    payment = update.message.successful_payment
    payload = payment.invoice_payload
    
    if payload.startswith("stars_"):
        parts = payload.split('_')
        user_id = int(parts[1])
        amount = int(parts[2])
        
        db.confirm_stars_payment(payload)
        
        await update.message.reply_text(f"✅ Зачислено {amount} ★")
        
        user = db.get_user(user_id)
        for admin_id in ADMIN_IDS:
            try:
                await context.bot.send_message(
                    admin_id,
                    f"💰 Пополнение Stars\n👤 @{user[1] or user_id}\n💎 {amount} ★"
                )
            except:
                pass

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_ban(update, context):
        return
    
    user_id = update.effective_user.id
    text = update.message.text
    
    # ===== ОБРАБОТКА ЗАГРУЗКИ КАРТИНОК =====
    if user_id in ADMIN_IDS:
        if context.user_data.get('awaiting') == 'upload_welcome':
            if update.message.photo:
                file_id = update.message.photo[-1].file_id
                db.save_image('welcome_image', file_id)
                context.user_data.pop('awaiting', None)
                await update.message.reply_text(
                    "✅ Картинка для приветствия сохранена!",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◀️ В админ-панель", callback_data="admin_panel")]])
                )
                return
            else:
                await update.message.reply_text("❌ Отправьте фото")
                return
        
        if context.user_data.get('awaiting') == 'upload_case':
            if update.message.photo:
                file_id = update.message.photo[-1].file_id
                db.save_image('case_image', file_id)
                context.user_data.pop('awaiting', None)
                await update.message.reply_text(
                    "✅ Картинка для кейса сохранена!",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◀️ В админ-панель", callback_data="admin_panel")]])
                )
                return
            else:
                await update.message.reply_text("❌ Отправьте фото")
                return
    
    if 'awaiting' not in context.user_data:
        return
    
    state = context.user_data['awaiting']
    
    # ===== ОБРАБОТКА ПОИСКА ПОЛЬЗОВАТЕЛЯ =====
    
    if state == 'search_user':
        if user_id not in ADMIN_IDS:
            return
        
        results = db.search_users(text)
        
        if not results:
            await update.message.reply_text("❌ Пользователи не найдены")
        else:
            response = "🔍 *Результаты поиска:*\n\n"
            for r in results[:10]:
                status = "🔴" if r[5] == 1 else "🟢"
                admin = "👑" if r[6] == 1 else ""
                response += f"{status}{admin} {r[2]} (@{r[1]}) — ID: `{r[0]}` | {r[3]} ★\n"
            
            await update.message.reply_text(response, parse_mode=ParseMode.MARKDOWN)
        
        context.user_data.pop('awaiting')
        return
    
    # ===== ОБРАБОТКА ОТКАЗА С ПРИЧИНОЙ =====
    
    if state == 'reject_withdrawal_reason':
        if user_id not in ADMIN_IDS:
            return
        
        withdrawal_id = context.user_data.get('reject_withdrawal_id')
        reason = text
        
        if db.reject_withdrawal(withdrawal_id, user_id, reason):
            await update.message.reply_text(f"✅ Заявка #{withdrawal_id} отклонена")
            
            # Уведомляем пользователя
            db.cursor.execute('SELECT user_id, amount FROM withdrawals WHERE id = ?', (withdrawal_id,))
            w_user_id, amount = db.cursor.fetchone()
            try:
                await context.bot.send_message(
                    w_user_id,
                    f"❌ *Заявка на вывод отклонена*\n\n"
                    f"💰 Сумма: {amount} ★\n"
                    f"📝 Причина: {reason}",
                    parse_mode=ParseMode.MARKDOWN
                )
            except:
                pass
        else:
            await update.message.reply_text(f"❌ Ошибка")
        
        context.user_data.pop('awaiting')
        context.user_data.pop('reject_withdrawal_id')
        return
    
    # ===== ОБРАБОТКА ОТКАЗА NFT =====
    
    if state == 'reject_nft_reason':
        if user_id not in ADMIN_IDS:
            return
        
        withdrawal_id = context.user_data.get('reject_nft_id')
        reason = text
        
        if db.reject_nft_withdrawal(withdrawal_id, user_id, reason):
            await update.message.reply_text(f"✅ Заявка #{withdrawal_id} отклонена, снежинки возвращены")
            
            # Уведомляем пользователя
            withdrawal = db.get_nft_withdrawal(withdrawal_id)
            try:
                await context.bot.send_message(
                    withdrawal[1],
                    f"❌ *Заявка на вывод NFT отклонена*\n\n"
                    f"🎁 {withdrawal[2]}\n"
                    f"📝 Причина: {reason}\n"
                    f"❄️ Снежинки возвращены на баланс.",
                    parse_mode=ParseMode.MARKDOWN
                )
            except:
                pass
        else:
            await update.message.reply_text(f"❌ Ошибка")
        
        context.user_data.pop('awaiting')
        context.user_data.pop('reject_nft_id')
        return
    
    # ===== ОБРАБОТКА ПРОМОКОДОВ =====
    
    if state == 'promocode':
        result = db.activate_promocode(user_id, text.upper().strip())
        
        if result['success']:
            await update.message.reply_text(f"✅ Промокод активирован!\n💰 +{result['amount']} ★")
        else:
            await update.message.reply_text(f"❌ {result['reason']}")
        
        context.user_data.pop('awaiting')
        return
    
    if state == 'promo_amount':
        if user_id not in ADMIN_IDS:
            return
        
        try:
            amount = int(text)
            if amount <= 0:
                await update.message.reply_text("❌ Сумма должна быть больше 0")
                return
            
            context.user_data['promo_amount'] = amount
            context.user_data['promo_step'] = 'days'
            context.user_data['awaiting'] = 'promo_days'
            await update.message.reply_text("📅 Введите срок действия (дни):")
        except:
            await update.message.reply_text("❌ Введите число")
        return
    
    if state == 'promo_days':
        if user_id not in ADMIN_IDS:
            return
        
        try:
            days = int(text)
            if days <= 0:
                await update.message.reply_text("❌ Срок должен быть больше 0")
                return
            
            context.user_data['promo_days'] = days
            context.user_data['promo_step'] = 'uses'
            context.user_data['awaiting'] = 'promo_uses'
            await update.message.reply_text("🔄 Введите максимальное количество использований (0 = безлимит):")
        except:
            await update.message.reply_text("❌ Введите число")
        return
    
    if state == 'promo_uses':
        if user_id not in ADMIN_IDS:
            return
        
        try:
            max_uses = int(text)
            amount = context.user_data['promo_amount']
            days = context.user_data['promo_days']
            
            code = db.generate_promocode(amount, days, max_uses, user_id)
            
            await update.message.reply_text(
                f"✅ *Промокод создан!*\n\n"
                f"Код: `{code}`\n"
                f"Сумма: {amount} ★\n"
                f"Срок: {days} дней\n"
                f"Макс. использований: {max_uses if max_uses > 0 else '∞'}",
                parse_mode=ParseMode.MARKDOWN
            )
            
            context.user_data.pop('awaiting')
            context.user_data.pop('promo_amount')
            context.user_data.pop('promo_days')
            context.user_data.pop('promo_uses')
            context.user_data.pop('promo_step')
        except:
            await update.message.reply_text("❌ Введите число")
        return
    
    # ===== ОБРАБОТКА ВЫВОДА =====
    
    if state == 'telegram':
        username = text.strip().replace('@', '')
        db.update_telegram_username(user_id, username)
        context.user_data.pop('awaiting')
        await update.message.reply_text("✅ Telegram Username сохранён!")
    
    elif state == 'crypto':
        try:
            crypto_id = int(text)
            db.update_crypto_id(user_id, str(crypto_id))
            context.user_data.pop('awaiting')
            await update.message.reply_text("✅ CryptoBot ID сохранён!")
        except:
            await update.message.reply_text("❌ Введите число")
    
    elif state == 'withdraw_telegram_amount':
        try:
            amount = int(text)
            user = db.get_user(user_id)
            
            if amount < 50:
                await update.message.reply_text("❌ Минимум 50 ★")
                return
            
            if amount > user[3]:
                await update.message.reply_text("❌ Недостаточно")
                return
            
            withdrawal_id = db.create_withdrawal(user_id, amount, 'telegram', user[9])
            
            context.user_data.pop('awaiting')
            await update.message.reply_text(f"✅ Заявка #{withdrawal_id} создана")
            
            for admin_id in ADMIN_IDS:
                try:
                    await context.bot.send_message(
                        admin_id,
                        f"⏳ *Новая заявка на вывод*\n\n"
                        f"👤 @{update.effective_user.username or user_id}\n"
                        f"📱 На Telegram: @{user[9]}\n"
                        f"💰 Сумма: {amount} ★\n"
                        f"🆔 #{withdrawal_id}",
                        parse_mode=ParseMode.MARKDOWN
                    )
                except:
                    pass
            
        except:
            await update.message.reply_text("❌ Введите число")
    
    elif state == 'withdraw_crypto_amount':
        try:
            amount = int(text)
            user = db.get_user(user_id)
            
            if amount < 50:
                await update.message.reply_text("❌ Минимум 50 ★")
                return
            
            if amount > user[3]:
                await update.message.reply_text("❌ Недостаточно")
                return
            
            withdrawal_id = db.create_withdrawal(user_id, amount, 'crypto', user[8])
            
            context.user_data.pop('awaiting')
            await update.message.reply_text(f"✅ Заявка #{withdrawal_id} создана")
            
            for admin_id in ADMIN_IDS:
                try:
                    await context.bot.send_message(
                        admin_id,
                        f"⏳ *Новая заявка на вывод*\n\n"
                        f"👤 @{update.effective_user.username or user_id}\n"
                        f"💳 CryptoBot ID: {user[8]}\n"
                        f"💰 Сумма: {amount} ★\n"
                        f"🆔 #{withdrawal_id}",
                        parse_mode=ParseMode.MARKDOWN
                    )
                except:
                    pass
            
        except:
            await update.message.reply_text("❌ Введите число")
    
    # ===== ОБРАБОТКА РАССЫЛКИ =====
    
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

def main():
    print("=" * 60)
    print(f"🚀 ЗАПУСК {BOT_NAME} (ОБНОВЛЕНИЕ 1.0)")
    print("=" * 60)
    print("✅ Пагинация пользователей")
    print("✅ Поиск пользователей")
    print("✅ Сортировка")
    print("✅ Причина отказа вывода (звёзды и NFT)")
    print("✅ Промокоды")
    print("✅ Вывод NFT по заявкам")
    print(f"✅ Твой ID {ADMIN_IDS[0]} - АДМИН")
    print("=" * 60)
    
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(PreCheckoutQueryHandler(precheckout_callback))
    app.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment_callback))
    app.add_handler(MessageHandler(filters.TEXT | filters.PHOTO, handle_message))
    
    print("🤖 Бот запущен!")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
