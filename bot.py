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

# ======================== НАСТРОЙКА ========================
TELEGRAM_TOKEN = os.environ.get("BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")
CRYPTOBOT_API_KEY = os.environ.get("CRYPTOBOT_API_KEY", "YOUR_CRYPTOBOT_API_KEY")
CRYPTOBOT_API_URL = "https://pay.crypt.bot/api"

ADMIN_IDS = [5697184715]  # ТВОЙ ID

BOT_NAME = "FEENDY STARS"

# Глобальные переменные для хранения ID картинок
WELCOME_IMAGE_ID = AgACAgIAAxkBAAPxZ7sK8R7_8Q5Jk5m7N8Q9R2s3LmYAAgxtj2UcvZSF8r9LmN8Q9R2s3LmYAAQADAgADeQADNgQ
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
            
            # Конвертируем звезды в TON (1 звезда = 1.3 рубля = 0.01238 TON)
            ton_amount = round(stars_amount * TON_PER_STAR, 2)
            rub_amount = stars_amount * RUB_PER_STAR
            
            payload = {
                "asset": currency,
                "amount": str(ton_amount),
                "description": f"{description} на {stars_amount} ★ (≈ {rub_amount:.2f} руб)",
                "paid_btn_name": "callback",
                "paid_btn_url": "https://t.me/FeendyStars_robot",  # ИСПРАВЛЕНО # или "openBot", "viewItem", "openChannel"
                "payload": f"crypto_{stars_amount}_{int(time.time())}"
            }
            
            logger.info(f"Creating CryptoBot invoice: {stars_amount} ★ = {ton_amount} TON (≈ {rub_amount:.2f} руб)")
            response = requests.post(url, headers=self.headers, json=payload, timeout=10)
            
            if response.status_code == 200:
                data = response.json()
                logger.info(f"CryptoBot response: {data}")
                if data.get('ok'):
                    return data['result']
                else:
                    logger.error(f"CryptoBot error: {data}")
            else:
                logger.error(f"CryptoBot HTTP error: {response.status_code} - {response.text}")
            return None
        except Exception as e:
            logger.error(f"CryptoBot API error: {e}")
            return None
    
    def get_balance(self):
        """Получение баланса бота в CryptoBot"""
        try:
            url = f"{CRYPTOBOT_API_URL}/getBalance"
            response = requests.get(url, headers=self.headers, timeout=10)
            if response.status_code == 200:
                data = response.json()
                if data.get('ok'):
                    return data['result']
            return []
        except Exception as e:
            logger.error(f"CryptoBot balance error: {e}")
            return []
    
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
    
    def get_invoice(self, invoice_id):
        """Получение информации о счете"""
        try:
            url = f"{CRYPTOBOT_API_URL}/getInvoices"
            payload = {
                "invoice_ids": [invoice_id]
            }
            response = requests.post(url, headers=self.headers, json=payload, timeout=10)
            if response.status_code == 200:
                data = response.json()
                if data.get('ok') and data.get('result'):
                    return data['result']['items'][0]
            return None
        except Exception as e:
            logger.error(f"CryptoBot get invoice error: {e}")
            return None

crypto = CryptoBotAPI(CRYPTOBOT_API_KEY)

# ======================== БАЗА ДАННЫХ ========================

class Database:
    def __init__(self):
        self.conn = sqlite3.connect('feendy_stars.db', check_same_thread=False)
        self.cursor = self.conn.cursor()
        self._create_tables()
        self._init_admin()
    
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
                total_lost INTEGER DEFAULT 0
            )
        ''')
        
        # Таблица для хранения ID картинок
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS images (
                key TEXT PRIMARY KEY,
                file_id TEXT,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
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
        
        # Таблица заявок на вывод
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS withdrawals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                amount INTEGER,
                method TEXT,
                wallet TEXT,
                status TEXT DEFAULT 'pending',
                admin_id INTEGER,
                processed_at TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
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
        self._load_images()
    
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
            # Сначала проверяем есть ли пользователь
            self.cursor.execute('SELECT * FROM users WHERE user_id = ?', (admin_id,))
            user = self.cursor.fetchone()
            
            if user:
                # Если есть - обновляем
                self.cursor.execute('''
                    UPDATE users SET is_admin = 1, is_banned = 0 WHERE user_id = ?
                ''', (admin_id,))
            else:
                # Если нет - создаем
                self.cursor.execute('''
                    INSERT INTO users (user_id, username, first_name, is_admin, is_banned)
                    VALUES (?, 'admin', 'Admin', 1, 0)
                ''', (admin_id,))
        
        self.conn.commit()
        logger.info(f"✅ Админ с ID {ADMIN_IDS[0]} принудительно установлен и разбанен")
    
    def _init_settings(self):
        settings = {
            'min_withdrawal': '50',
            'withdrawal_fee': '0',
            'case_price': '35',
            'house_edge': '10',
            'stars_rate': '1',        # 1 звезда в боте = 1 звезда Telegram
            'rub_per_star': '1.3',    # 1 звезда = 1.3 рубля
            'rub_per_ton': '105'       # 1 TON = 105 рублей
        }
        for key, value in settings.items():
            self.cursor.execute(
                'INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)',
                (key, value)
            )
        self.conn.commit()
    
    def _load_images(self):
        """Загрузка ID картинок из базы данных"""
        global WELCOME_IMAGE_ID, CASE_IMAGE_ID
        
        self.cursor.execute('SELECT value FROM settings WHERE key = ?', ('welcome_image',))
        result = self.cursor.fetchone()
        if result:
            WELCOME_IMAGE_ID = result[0]
        
        self.cursor.execute('SELECT value FROM settings WHERE key = ?', ('case_image',))
        result = self.cursor.fetchone()
        if result:
            CASE_IMAGE_ID = result[0]
    
    def save_image(self, key, file_id):
        """Сохранение ID картинки в базу данных"""
        global WELCOME_IMAGE_ID, CASE_IMAGE_ID
        
        self.cursor.execute('''
            INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)
        ''', (key, file_id))
        self.conn.commit()
        
        if key == 'welcome_image':
            WELCOME_IMAGE_ID = file_id
        elif key == 'case_image':
            CASE_IMAGE_ID = file_id
    
    def get_user(self, user_id):
        self.cursor.execute('SELECT * FROM users WHERE user_id = ?', (user_id,))
        return self.cursor.fetchone()
    
    def create_user(self, user_id, username, first_name, referred_by=None):
        # Проверяем, не админ ли это
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
            UPDATE users SET balance = balance + ? WHERE user_id = ?
        ''', (amount, user_id))
        self.conn.commit()
    
    def update_snowflakes(self, user_id, amount):
        self.cursor.execute('''
            UPDATE users SET snowflakes = snowflakes + ? WHERE user_id = ?
        ''', (amount, user_id))
        self.conn.commit()
    
    def add_lost_stars(self, user_id, amount):
        """Добавляем проигранные звёзды для расчёта снежинок"""
        self.cursor.execute('''
            UPDATE users SET total_lost = total_lost + ?, 
            snowflakes = snowflakes + ? WHERE user_id = ?
        ''', (amount, int(amount * 0.5), user_id))
        self.conn.commit()
    
    def update_crypto_id(self, user_id, crypto_id):
        self.cursor.execute('''
            UPDATE users SET crypto_id = ? WHERE user_id = ?
        ''', (crypto_id, user_id))
        self.conn.commit()
    
    def update_telegram_username(self, user_id, telegram_username):
        self.cursor.execute('''
            UPDATE users SET telegram_username = ? WHERE user_id = ?
        ''', (telegram_username, user_id))
        self.conn.commit()
    
    def get_all_users(self):
        self.cursor.execute('SELECT user_id, username, first_name, balance, snowflakes, is_banned, is_admin, created_at FROM users ORDER BY created_at DESC')
        return self.cursor.fetchall()
    
    def get_active_users_count(self, days=7):
        since = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d %H:%M:%S')
        self.cursor.execute('''
            SELECT COUNT(DISTINCT user_id) FROM games WHERE created_at > ?
        ''', (since,))
        return self.cursor.fetchone()[0]
    
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
            self.cursor.execute(
                'UPDATE users SET daily_bonus = ?, balance = balance + 5 WHERE user_id = ?',
                (today, user_id)
            )
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
        """Покупка зимнего NFT за снежинки"""
        for item in self.WINTER_NFTS:
            if item['name'] == item_name:
                user = self.get_user(user_id)
                if user[4] >= item['price']:
                    self.update_snowflakes(user_id, -item['price'])
                    self.cursor.execute('''
                        INSERT INTO inventory (user_id, item_name, item_price, source)
                        VALUES (?, ?, ?, 'winter_shop')
                    ''', (user_id, item['name'], item['price']))
                    self.conn.commit()
                    return True
        return False
    
    def get_user_inventory(self, user_id):
        self.cursor.execute('''
            SELECT item_name, item_price, created_at FROM inventory 
            WHERE user_id = ? ORDER BY created_at DESC
        ''', (user_id,))
        return self.cursor.fetchall()
    
    # ================== ПЛАТЕЖИ ==================
    
    def add_stars_payment(self, user_id, amount, payload):
        """Добавление платежа через Telegram Stars"""
        self.cursor.execute('''
            INSERT INTO payments (user_id, amount, method, invoice_id, status)
            VALUES (?, ?, 'stars', ?, 'pending')
        ''', (user_id, amount, payload))
        self.conn.commit()
        return True
    
    def confirm_stars_payment(self, payload):
        """Подтверждение платежа через Telegram Stars"""
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
    
    def add_crypto_payment(self, user_id, amount, invoice_id):
        """Добавление платежа через CryptoBot"""
        self.cursor.execute('''
            INSERT INTO payments (user_id, amount, method, invoice_id, status)
            VALUES (?, ?, 'crypto', ?, 'pending')
        ''', (user_id, amount, invoice_id))
        self.conn.commit()
    
    def confirm_crypto_payment(self, invoice_id):
        """Подтверждение платежа через CryptoBot"""
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
    
    # ================== ВЫВОД ==================
    
    def create_withdrawal(self, user_id, amount, method, wallet):
        """Создание заявки на вывод"""
        self.cursor.execute('''
            INSERT INTO withdrawals (user_id, amount, method, wallet)
            VALUES (?, ?, ?, ?)
        ''', (user_id, amount, method, wallet))
        self.conn.commit()
        return self.cursor.lastrowid
    
    def get_pending_withdrawals(self):
        """Получение всех ожидающих заявок"""
        self.cursor.execute('''
            SELECT w.*, u.username, u.first_name
            FROM withdrawals w
            JOIN users u ON w.user_id = u.user_id
            WHERE w.status = 'pending'
            ORDER BY w.created_at ASC
        ''')
        return self.cursor.fetchall()
    
    def approve_withdrawal(self, withdrawal_id, admin_id):
        """Одобрение заявки на вывод"""
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
        
        # Для CryptoBot переводим автоматически
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
            # Для Telegram Stars - только подтверждение админом
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
    
    def reject_withdrawal(self, withdrawal_id, admin_id):
        """Отклонение заявки на вывод"""
        self.cursor.execute('''
            UPDATE withdrawals 
            SET status = 'rejected', admin_id = ?, processed_at = CURRENT_TIMESTAMP
            WHERE id = ?
        ''', (admin_id, withdrawal_id))
        self.conn.commit()
        return True
    
    def get_user_withdrawals(self, user_id):
        """История выводов пользователя"""
        self.cursor.execute('''
            SELECT * FROM withdrawals 
            WHERE user_id = ? 
            ORDER BY created_at DESC
        ''', (user_id,))
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
    
    # ================== УПРАВЛЕНИЕ БАНАМИ ==================
    
    def ban_user(self, admin_id, user_id):
        """Бан пользователя (только админ)"""
        admin = self.get_user(admin_id)
        if not admin or admin[10] != 1:  # is_admin
            return False
        
        target = self.get_user(user_id)
        if target and target[10] == 1:
            return False
            
        self.cursor.execute('UPDATE users SET is_banned = 1 WHERE user_id = ?', (user_id,))
        self.conn.commit()
        return True
    
    def unban_user(self, admin_id, user_id):
        """Разбан пользователя (только админ)"""
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

# Шансы игр (в пользу казино)
GAME_ODDS = {
    'flip': {'win_chance': 45, 'multiplier': 1.7, 'name': '🎲 Орёл и решка'},
    'roulette': {'win_chance': 20, 'multiplier': 4.5, 'name': '💀 Русская рулетка'},
    'wheel': {'win_chance': 8, 'multiplier': 10, 'name': '🎡 Колесо удачи'},
    'mines': {'win_chance': 12, 'multiplier': 7.5, 'name': '💣 Минное поле'},
    'dice': {'win_chance': 30, 'multiplier': 2.5, 'name': '🎲 Кости'},
    'slots': {'win_chance': 25, 'multiplier': 3.0, 'name': '🎰 Слоты'}
}

async def check_ban(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Проверка бана - админы всегда пропускаются"""
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
    
    # Отправляем с картинкой если есть
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
        await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(keyboard))
    
    # ================== ПРАВИЛА ==================
    
    elif data == "rules":
        text = (
            f"📜 *Правила использования бота {BOT_NAME}*\n\n"
            f"🚫 *Запрещено:*\n"
            f"• 🤖 Использование ботов для накрутки\n"
            f"• 👥 Создание мультикакаунтов\n"
            f"• 🎭 Обман системы реферальной программы\n"
            f"• 💀 Любые попытки обмана администрации\n\n"
            f"✅ *Разрешено:*\n"
            f"• 👋 Приглашать реальных друзей\n"
            f"• 🎮 Активно участвовать в проекте\n"
            f"• ⭐ Соблюдать правила каналов\n\n"
            f"⚡ *Нарушение правил ведет к:*\n"
            f"• 🔒 Блокировке аккаунта\n"
            f"• 💰 Обнулению баланса\n"
            f"• 🚫 Запрету на участие в проекте\n\n"
            f"👑 Администрация оставляет за собой право блокировать пользователей "
            f"без объяснения причин при подозрении в мошенничестве.\n\n"
            f"🎉 *Удачной игры!*"
        )
        
        keyboard = [[InlineKeyboardButton("◀️ Главное меню", callback_data="main_menu")]]
        await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(keyboard))
    
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
        await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(keyboard))
    
    # ================== ПОПОЛНЕНИЕ ==================
    
    elif data == "deposit_menu":
        text = (
            f"💰 *Пополнение баланса*\n\n"
            f"Выберите способ пополнения:\n\n"
            f"⭐ Telegram Stars — 1★ = 1 Telegram Star\n"
            f"💎 CryptoBot (TON) — 1★ = 1.3 руб (≈ {TON_PER_STAR:.4f} TON)\n"
            f"Минимальная сумма: 10 ★"
        )
        
        keyboard = [
            [InlineKeyboardButton("⭐ Telegram Stars", callback_data="deposit_stars_menu")],
            [InlineKeyboardButton("💎 CryptoBot (TON)", callback_data="deposit_crypto_menu")],
            [InlineKeyboardButton("◀️ Главное меню", callback_data="main_menu")]
        ]
        
        await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(keyboard))
    
    elif data == "deposit_stars_menu":
        text = "⭐ *Пополнение Telegram Stars*\n\nВыберите сумму (1★ = 1 Telegram Star):"
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
        await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(keyboard))
    
    elif data.startswith("stars_"):
        amount = int(data.replace("stars_", ""))
        
        # Создаем счет на оплату через Telegram Stars
        prices = [LabeledPrice(label="XTR", amount=amount)]
        
        await context.bot.send_invoice(
            chat_id=user_id,
            title=f"Пополнение баланса {BOT_NAME}",
            description=f"Пополнение на {amount} ⭐",
            payload=f"stars_payment_{user_id}_{amount}_{int(time.time())}",
            provider_token="",  # Пусто для Telegram Stars
            currency="XTR",
            prices=prices,
            start_parameter="time-machine-example"
        )
    
    elif data == "deposit_crypto_menu":
        text = (
            f"💎 *Пополнение через CryptoBot*\n\n"
            f"1★ = 1.3 рубля\n"
            f"1 TON = 105 рублей\n\n"
            f"Выберите сумму:"
        )
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
        await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(keyboard))
    
    elif data.startswith("crypto_"):
        stars_amount = int(data.replace("crypto_", ""))
        rub_amount = stars_amount * RUB_PER_STAR
        ton_amount = round(stars_amount * TON_PER_STAR, 2)
        
        invoice = crypto.create_invoice(stars_amount, "TON", f"Пополнение {BOT_NAME} на {stars_amount} ★")
        
        if invoice:
            pay_url = invoice['pay_url']
            invoice_id = invoice['invoice_id']
            
            db.add_crypto_payment(user_id, stars_amount, invoice_id)
            
            text = (
                f"💎 *Пополнение на {stars_amount} ★*\n\n"
                f"💰 Сумма в рублях: {rub_amount:.2f} руб\n"
                f"💎 К оплате: {ton_amount} TON\n"
                f"🔗 [Оплатить через CryptoBot]({pay_url})\n\n"
                f"После оплаты баланс будет зачислен автоматически."
            )
            keyboard = [[InlineKeyboardButton("◀️ Главное меню", callback_data="main_menu")]]
            await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(keyboard))
            
            # Уведомление админу о создании счета
            for admin_id in ADMIN_IDS:
                try:
                    await context.bot.send_message(
                        admin_id,
                        f"💰 *Создан счет на пополнение*\n\n"
                        f"👤 Пользователь: @{user[1] or user_id}\n"
                        f"💎 Сумма: {stars_amount} ★\n"
                        f"💵 К оплате: {ton_amount} TON (≈ {rub_amount:.2f} руб)\n"
                        f"🆔 Invoice: {invoice_id}",
                        parse_mode=ParseMode.MARKDOWN
                    )
                except:
                    pass
        else:
            await query.edit_message_text("❌ Ошибка создания счета. Попробуйте позже.")
    
    # ================== ВЫВОД ==================
    
    elif data == "withdraw_menu":
        text = (
            f"💸 *Вывод средств*\n\n"
            f"💰 *Баланс:* {user[3]} ★\n"
            f"📱 *Telegram Username:* @{user[9] or 'не указан'}\n"
            f"💳 *CryptoBot ID:* {user[8] or 'не указан'}\n\n"
            f"Минимальная сумма: 50 ★\n"
            f"Комиссия: 0%\n\n"
            f"Выберите способ вывода:"
        )
        
        keyboard = [
            [InlineKeyboardButton("📱 На Telegram Username", callback_data="withdraw_telegram")],
            [InlineKeyboardButton("💳 На CryptoBot ID", callback_data="withdraw_crypto")],
            [InlineKeyboardButton("⚙️ Настройки кошельков", callback_data="withdraw_settings")],
            [InlineKeyboardButton("◀️ Главное меню", callback_data="main_menu")]
        ]
        
        await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(keyboard))
    
    elif data == "withdraw_settings":
        keyboard = [
            [InlineKeyboardButton("📱 Указать Telegram Username", callback_data="set_telegram_username")],
            [InlineKeyboardButton("💳 Указать CryptoBot ID", callback_data="set_crypto_id")],
            [InlineKeyboardButton("◀️ Назад", callback_data="withdraw_menu")]
        ]
        
        await query.edit_message_text(
            "⚙️ *Настройки кошельков*\n\nВыберите что хотите указать:",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    
    elif data == "set_telegram_username":
        context.user_data['awaiting'] = 'telegram_username'
        await query.edit_message_text(
            "📱 *Укажите ваш Telegram Username*\n\n"
            "Отправьте ваш username (без @):",
            parse_mode=ParseMode.MARKDOWN
        )
    
    elif data == "set_crypto_id":
        context.user_data['awaiting'] = 'crypto_id'
        await query.edit_message_text(
            "💳 *Укажите ваш CryptoBot ID*\n\n"
            "Отправьте ID вашего кошелька (только цифры):",
            parse_mode=ParseMode.MARKDOWN
        )
    
    elif data == "withdraw_telegram":
        if user[3] < 50:
            text = f"❌ *Недостаточно средств!*\n\nМинимум 50 ★, у вас {user[3]} ★"
            keyboard = [[InlineKeyboardButton("◀️ Назад", callback_data="withdraw_menu")]]
            await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(keyboard))
            return
        
        if not user[9]:
            await query.edit_message_text(
                "❌ Сначала укажите Telegram Username",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⚙️ Указать", callback_data="set_telegram_username")]])
            )
            return
        
        context.user_data['awaiting'] = 'withdrawal_amount_telegram'
        await query.edit_message_text(
            f"📱 *Вывод на Telegram Username*\n\n"
            f"Ваш username: @{user[9]}\n"
            f"Баланс: {user[3]} ★\n\n"
            f"Введите сумму для вывода (мин. 50 ★):",
            parse_mode=ParseMode.MARKDOWN
        )
    
    elif data == "withdraw_crypto":
        if user[3] < 50:
            text = f"❌ *Недостаточно средств!*\n\nМинимум 50 ★, у вас {user[3]} ★"
            keyboard = [[InlineKeyboardButton("◀️ Назад", callback_data="withdraw_menu")]]
            await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(keyboard))
            return
        
        if not user[8]:
            await query.edit_message_text(
                "❌ Сначала укажите CryptoBot ID",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⚙️ Указать", callback_data="set_crypto_id")]])
            )
            return
        
        context.user_data['awaiting'] = 'withdrawal_amount_crypto'
        await query.edit_message_text(
            f"💳 *Вывод на CryptoBot ID*\n\n"
            f"Ваш CryptoBot ID: {user[8]}\n"
            f"Баланс: {user[3]} ★\n\n"
            f"Введите сумму для вывода (мин. 50 ★):",
            parse_mode=ParseMode.MARKDOWN
        )
    
    # ================== ЗИМНИЙ МАГАЗИН ==================
    
    elif data == "winter_shop":
        text = (
            f"❄️ *Зимний магазин NFT*\n\n"
            f"**Ваши снежинки:** {user[4]} ✨\n\n"
            f"**Доступные NFT:**\n\n"
        )
        
        for item in db.WINTER_NFTS:
            text += f"• {item['name']} — {item['price']} ✨\n"
        
        text += (
            f"\n**Как получить снежинки?**\n"
            f"• За каждую проигранную звезду: +0.5 ✨\n"
            f"• За каждого приглашенного друга: +5 ✨"
        )
        
        keyboard = []
        for item in db.WINTER_NFTS:
            keyboard.append([InlineKeyboardButton(
                f"🎁 Купить {item['name']}",
                callback_data=f"buy_nft_{item['name']}"
            )])
        
        keyboard.append([InlineKeyboardButton("◀️ Главное меню", callback_data="main_menu")])
        await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(keyboard))
    
    elif data.startswith("buy_nft_"):
        item_name = data.replace("buy_nft_", "")
        
        for item in db.WINTER_NFTS:
            if item['name'] == item_name:
                if user[4] >= item['price']:
                    if db.buy_winter_nft(user_id, item_name):
                        text = f"✅ *Покупка успешна!*\n\nВы приобрели: {item['name']}"
                    else:
                        text = "❌ Ошибка при покупке"
                else:
                    missing = item['price'] - user[4]
                    text = f"❌ *Недостаточно снежинок!*\n\nНужно ещё {missing} ✨"
                
                keyboard = [[InlineKeyboardButton("◀️ В магазин", callback_data="winter_shop")]]
                await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(keyboard))
                break
    
    # ================== КЕЙС ==================
    
    elif data == "case_menu":
        cases = db.get_cases()
        case = cases[0] if cases else None
        
        if case:
            items = json.loads(case[3])
            items_text = "\n".join([f"• {item['name']} — {item['chance']}%" for item in items[:5]])
            
            text = (
                f"📦 *Кейс {BOT_NAME}*\n\n"
                f"💰 *Цена:* {case[2]} ★\n"
                f"❄️ *Снежинки:* {user[4]} ✨\n\n"
                f"**Возможные предметы:**\n{items_text}\n..."
            )
            
            keyboard = [
                [InlineKeyboardButton(f"📦 Открыть ({case[2]} ★)", callback_data=f"open_case_{case[0]}")],
                [InlineKeyboardButton("◀️ Главное меню", callback_data="main_menu")]
            ]
            
            # Отправляем с картинкой если есть
            if CASE_IMAGE_ID:
                await query.edit_message_media(
                    media=InputMediaPhoto(
                        media=CASE_IMAGE_ID,
                        caption=text,
                        parse_mode=ParseMode.MARKDOWN
                    ),
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )
            else:
                await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(keyboard))
    
    elif data.startswith("open_case_"):
        case_id = int(data.replace("open_case_", ""))
        case_price = 35
        
        if user[3] < case_price:
            text = f"❌ *Недостаточно средств!*\n\nНужно {case_price} ★"
            keyboard = [[InlineKeyboardButton("◀️ Назад", callback_data="case_menu")]]
            await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(keyboard))
            return
        
        db.update_balance(user_id, -case_price)
        result = db.open_case(case_id, user_id)
        
        if result:
            text = f"🎉 *Поздравляем!*\n\nВы выиграли: **{result['name']}** (шанс {result['chance']}%)"
        else:
            text = "❌ Ошибка открытия кейса"
        
        keyboard = [
            [InlineKeyboardButton("📦 Ещё кейс", callback_data="case_menu")],
            [InlineKeyboardButton("◀️ Главное меню", callback_data="main_menu")]
        ]
        await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(keyboard))
    
    # ================== РЕФЕРАЛЫ ==================
    
    elif data == "referral":
        ref_link = f"https://t.me/{(await context.bot.get_me()).username}?start=ref{user_id}"
        
        text = (
            f"👥 *Реферальная программа*\n\n"
            f"🔗 *Ваша ссылка:*\n`{ref_link}`\n\n"
            f"📊 *Приглашено:* {user[5]}\n"
            f"💰 *Заработано:* {user[5] * 5} ✨\n\n"
            f"🎁 *За каждого друга:* +5 ✨\n"
            f"💫 Друг должен начать пользоваться ботом"
        )
        
        keyboard = [[InlineKeyboardButton("◀️ Главное меню", callback_data="main_menu")]]
        await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(keyboard))
    
    # ================== ЕЖЕДНЕВНЫЙ БОНУС ==================
    
    elif data == "daily_bonus":
        if db.check_daily_bonus(user_id):
            text = "🎁 *Ежедневный бонус получен!*\n\n+5 ★"
        else:
            text = "❌ Бонус уже получен сегодня"
        
        keyboard = [[InlineKeyboardButton("◀️ Главное меню", callback_data="main_menu")]]
        await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(keyboard))
    
    # ================== АДМИН-ПАНЕЛЬ ==================
    
    elif data == "admin_panel":
        if user_id not in ADMIN_IDS:
            await query.edit_message_text("❌ У вас нет прав администратора")
            return
        
        stats = db.get_total_stats()
        pending = len(db.get_pending_withdrawals())
        
        text = (
            f"⚙️ *Админ-панель {BOT_NAME}*\n\n"
            f"📊 **Статистика:**\n"
            f"• 👥 Пользователей: {stats['total_users']}\n"
            f"• 💰 Общий баланс: {stats['total_balance']} ★\n"
            f"• ❄️ Всего снежинок: {stats['total_snowflakes']} ✨\n"
            f"• 💸 Выведено: {stats['total_withdrawn']} ★\n"
            f"• 🎮 Всего игр: {stats['total_games']}\n\n"
            f"⏳ **Заявок на вывод:** {pending}"
        )
        
        keyboard = [
            [InlineKeyboardButton("👥 Все пользователи", callback_data="admin_users")],
            [InlineKeyboardButton("⏳ Заявки на вывод", callback_data="admin_withdrawals")],
            [InlineKeyboardButton("🔨 Управление банами", callback_data="admin_bans")],
            [InlineKeyboardButton("📢 Рассылка", callback_data="admin_broadcast")],
            [InlineKeyboardButton("🖼️ Управление картинками", callback_data="admin_images")],
            [InlineKeyboardButton("◀️ Главное меню", callback_data="main_menu")]
        ]
        
        await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(keyboard))
    
    elif data == "admin_images":
        if user_id not in ADMIN_IDS:
            return
        
        text = (
            f"🖼️ *Управление картинками*\n\n"
            f"Текущие картинки:\n"
            f"• Приветствие: {'✅' if WELCOME_IMAGE_ID else '❌'}\n"
            f"• Кейс: {'✅' if CASE_IMAGE_ID else '❌'}\n\n"
            f"Выберите что хотите изменить:"
        )
        
        keyboard = [
            [InlineKeyboardButton("🖼️ Загрузить приветствие", callback_data="upload_welcome")],
            [InlineKeyboardButton("🖼️ Загрузить кейс", callback_data="upload_case")],
            [InlineKeyboardButton("◀️ Назад", callback_data="admin_panel")]
        ]
        
        await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(keyboard))
    
    elif data == "upload_welcome":
        if user_id not in ADMIN_IDS:
            return
        
        context.user_data['awaiting'] = 'upload_welcome'
        await query.edit_message_text(
            "🖼️ *Загрузите картинку для приветствия*\n\n"
            "Отправьте фото, которое будет показываться при /start",
            parse_mode=ParseMode.MARKDOWN
        )
    
    elif data == "upload_case":
        if user_id not in ADMIN_IDS:
            return
        
        context.user_data['awaiting'] = 'upload_case'
        await query.edit_message_text(
            "🖼️ *Загрузите картинку для кейса*\n\n"
            "Отправьте фото, которое будет показываться в разделе кейса",
            parse_mode=ParseMode.MARKDOWN
        )
    
    elif data == "admin_users":
        if user_id not in ADMIN_IDS:
            return
        
        users = db.get_all_users()
        text = f"👥 *Всего пользователей: {len(users)}*\n\n"
        
        for u in users[:20]:
            status = "🔴" if u[5] == 1 else "🟢"
            admin = "👑" if u[6] == 1 else ""
            text += f"{status}{admin} {u[2]} (@{u[1]}) — {u[3]} ★ | ✨ {u[4]}\n"
        
        if len(users) > 20:
            text += f"\n...и ещё {len(users)-20} пользователей"
        
        keyboard = [[InlineKeyboardButton("◀️ Назад", callback_data="admin_panel")]]
        await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(keyboard))
    
    elif data == "admin_bans":
        if user_id not in ADMIN_IDS:
            return
        
        banned = db.get_banned_users()
        
        text = "🔨 *Забаненные пользователи*\n\n"
        keyboard = []
        
        if banned:
            for b in banned:
                text += f"• {b[2]} (@{b[1]}) — ID: {b[0]}\n"
                keyboard.append([InlineKeyboardButton(f"✅ Разбанить {b[0]}", callback_data=f"unban_{b[0]}")])
        else:
            text += "✅ Нет забаненных пользователей"
        
        keyboard.append([InlineKeyboardButton("◀️ Назад", callback_data="admin_panel")])
        await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(keyboard))
    
    elif data.startswith("unban_"):
        if user_id not in ADMIN_IDS:
            return
        
        ban_user_id = int(data.replace("unban_", ""))
        
        if db.unban_user(user_id, ban_user_id):
            await query.edit_message_text(f"✅ Пользователь {ban_user_id} разбанен")
        else:
            await query.edit_message_text("❌ Ошибка: недостаточно прав")
    
    elif data == "admin_withdrawals":
        if user_id not in ADMIN_IDS:
            return
        
        withdrawals = db.get_pending_withdrawals()
        
        if not withdrawals:
            await query.edit_message_text(
                "✅ Нет ожидающих заявок на вывод",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Назад", callback_data="admin_panel")]])
            )
            return
        
        text = "⏳ *Ожидающие заявки на вывод:*\n\n"
        keyboard = []
        
        for w in withdrawals[:5]:
            method_emoji = "📱" if w[3] == 'telegram' else "💳"
            text += (
                f"🆔 *#{w[0]}*\n"
                f"👤 {w[8]} (@{w[7]})\n"
                f"{method_emoji} {w[3]}: {w[4]}\n"
                f"💰 {w[2]} ★\n"
                f"🕐 {w[6][:16]}\n\n"
            )
            keyboard.append([
                InlineKeyboardButton(f"✅ Одобрить #{w[0]}", callback_data=f"approve_withdrawal_{w[0]}"),
                InlineKeyboardButton(f"❌ Отклонить #{w[0]}", callback_data=f"reject_withdrawal_{w[0]}")
            ])
        
        keyboard.append([InlineKeyboardButton("◀️ Назад", callback_data="admin_panel")])
        await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(keyboard))
    
    elif data.startswith("approve_withdrawal_"):
        if user_id not in ADMIN_IDS:
            return
        
        withdrawal_id = int(data.replace("approve_withdrawal_", ""))
        
        if db.approve_withdrawal(withdrawal_id, user_id):
            await query.edit_message_text("✅ Заявка одобрена")
            
            # Уведомляем пользователя
            db.cursor.execute('SELECT user_id, amount FROM withdrawals WHERE id = ?', (withdrawal_id,))
            w_user_id, amount = db.cursor.fetchone()
            try:
                await context.bot.send_message(
                    w_user_id,
                    f"✅ *Заявка на вывод одобрена!*\n\n💰 Сумма: {amount} ★",
                    parse_mode=ParseMode.MARKDOWN
                )
            except:
                pass
        else:
            await query.edit_message_text("❌ Ошибка при одобрении")
    
    elif data.startswith("reject_withdrawal_"):
        if user_id not in ADMIN_IDS:
            return
        
        withdrawal_id = int(data.replace("reject_withdrawal_", ""))
        
        if db.reject_withdrawal(withdrawal_id, user_id):
            await query.edit_message_text("❌ Заявка отклонена")
            
            # Уведомляем пользователя
            db.cursor.execute('SELECT user_id, amount FROM withdrawals WHERE id = ?', (withdrawal_id,))
            w_user_id, amount = db.cursor.fetchone()
            try:
                await context.bot.send_message(
                    w_user_id,
                    f"❌ *Заявка на вывод отклонена*\n\n💰 Сумма: {amount} ★",
                    parse_mode=ParseMode.MARKDOWN
                )
            except:
                pass
        else:
            await query.edit_message_text("❌ Ошибка при отклонении")
    
    elif data == "admin_broadcast":
        if user_id not in ADMIN_IDS:
            return
        
        context.user_data['awaiting'] = 'broadcast'
        await query.edit_message_text(
            "📢 *Создание рассылки*\n\n"
            "Отправьте сообщение (можно с фото), которое хотите разослать всем пользователям:",
            parse_mode=ParseMode.MARKDOWN
        )
    
    # ================== ИГРЫ ==================
    
    elif data in ['game_flip', 'game_roulette', 'game_wheel', 'game_mines', 'game_dice', 'game_slots']:
        game_key = data.replace('game_', '')
        context.user_data['game'] = game_key
        
        text = f"🎮 *{GAME_ODDS[game_key]['name']}*\n\n💰 Ваш баланс: {user[3]} ★\n\nВыберите сумму ставки:"
        keyboard = [
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
        ]
        await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(keyboard))
    
    elif data.startswith("bet_"):
        bet = int(data.replace("bet_", ""))
        game = context.user_data.get('game', 'flip')
        
        if bet > user[3]:
            text = f"❌ *Недостаточно средств!*\n\nУ вас {user[3]} ★"
            keyboard = [[InlineKeyboardButton("◀️ Назад", callback_data=f"game_{game}")]]
            await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(keyboard))
            return
        
        odds = GAME_ODDS[game]
        win_chance = odds['win_chance']
        multiplier = odds['multiplier']
        
        db.update_balance(user_id, -bet)
        
        roll = random.randint(1, 100)
        win = roll <= win_chance
        
        if win:
            win_amount = int(bet * multiplier)
            db.update_balance(user_id, win_amount)
            db.add_game(user_id, game, bet, multiplier, win_amount, 'win')
            
            text = (
                f"🎉 *ВЫ ВЫИГРАЛИ!*\n\n"
                f"🎮 {odds['name']}\n"
                f"💰 Ставка: {bet} ★\n"
                f"📈 Множитель: x{multiplier}\n"
                f"💎 Выигрыш: {win_amount} ★"
            )
        else:
            db.add_lost_stars(user_id, bet)
            db.add_game(user_id, game, bet, 0, 0, 'lose')
            
            text = f"😢 *ВЫ ПРОИГРАЛИ*\n\n💰 Ставка {bet} ★ проиграна\n✨ +{int(bet * 0.5)} снежинок"
        
        keyboard = [
            [InlineKeyboardButton("🎮 Играть ещё", callback_data=f"game_{game}")],
            [InlineKeyboardButton("◀️ Главное меню", callback_data="main_menu")]
        ]
        
        await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(keyboard))
        context.user_data.pop('bet', None)
    
    # ================== ГЛАВНОЕ МЕНЮ ==================
    
    elif data == "main_menu":
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
                InlineKeyboardButton("📊 Правила", callback_data="rules")
            ]
        ]
        
        if user_id in ADMIN_IDS:
            keyboard.append([InlineKeyboardButton("⚙️ Админ-панель", callback_data="admin_panel")])
        
        text = (
            f"🌟 *{BOT_NAME}*\n\n"
            f"🆔 *ID:* {user_id}\n"
            f"👤 *Имя:* {user[2]}\n"
            f"💰 *Баланс:* {user[3]} ★\n"
            f"❄️ *Снежинки:* {user[4]} ✨\n\n"
            f"Выберите действие:"
        )
        
        await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(keyboard))

async def precheckout_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка предварительной проверки платежа"""
    query = update.pre_checkout_query
    payload = query.invoice_payload
    
    if payload.startswith("stars_payment"):
        await query.answer(ok=True)
    else:
        await query.answer(ok=False, error_message="Ошибка платежа")

async def successful_payment_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка успешного платежа"""
    payment = update.message.successful_payment
    payload = payment.invoice_payload
    
    if payload.startswith("stars_payment"):
        parts = payload.split('_')
        user_id = int(parts[2])
        amount = int(parts[3])
        
        user_id, amount = db.confirm_stars_payment(payload)
        
        if user_id:
            await update.message.reply_text(
                f"✅ *Платеж успешно зачислен!*\n\n💰 Сумма: {amount} ★",
                parse_mode=ParseMode.MARKDOWN
            )
            
            # Уведомление админу
            user = db.get_user(user_id)
            for admin_id in ADMIN_IDS:
                try:
                    await context.bot.send_message(
                        admin_id,
                        f"💰 *Пополнение через Stars*\n\n"
                        f"👤 Пользователь: @{user[1] or user_id}\n"
                        f"💎 Сумма: {amount} ★",
                        parse_mode=ParseMode.MARKDOWN
                    )
                except:
                    pass

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_ban(update, context):
        return
    
    user_id = update.effective_user.id
    text = update.message.text
    
    if 'awaiting' not in context.user_data:
        # Проверяем не фото ли это для загрузки картинок
        if update.message.photo and user_id in ADMIN_IDS:
            # Проверяем есть ли ожидание загрузки
            if 'upload_welcome' in context.user_data:
                file_id = update.message.photo[-1].file_id
                db.save_image('welcome_image', file_id)
                context.user_data.pop('upload_welcome')
                await update.message.reply_text("✅ Картинка для приветствия сохранена!")
                return
            elif 'upload_case' in context.user_data:
                file_id = update.message.photo[-1].file_id
                db.save_image('case_image', file_id)
                context.user_data.pop('upload_case')
                await update.message.reply_text("✅ Картинка для кейса сохранена!")
                return
        return
    
    state = context.user_data['awaiting']
    
    if state == 'telegram_username':
        username = text.strip().replace('@', '')
        db.update_telegram_username(user_id, username)
        context.user_data.pop('awaiting')
        await update.message.reply_text(
            "✅ Telegram Username сохранён!",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Назад к выводу", callback_data="withdraw_menu")]])
        )
    
    elif state == 'crypto_id':
        try:
            crypto_id = int(text)
            db.update_crypto_id(user_id, str(crypto_id))
            context.user_data.pop('awaiting')
            await update.message.reply_text(
                "✅ CryptoBot ID сохранён!",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Назад к выводу", callback_data="withdraw_menu")]])
            )
        except ValueError:
            await update.message.reply_text("❌ Введите корректный ID (только цифры)")
    
    elif state == 'withdrawal_amount_telegram':
        try:
            amount = int(text)
            
            if amount < 50:
                await update.message.reply_text("❌ Минимальная сумма — 50 ★")
                return
            
            user = db.get_user(user_id)
            if amount > user[3]:
                await update.message.reply_text("❌ Недостаточно средств")
                return
            
            withdrawal_id = db.create_withdrawal(user_id, amount, 'telegram', user[9])
            
            context.user_data.pop('awaiting')
            await update.message.reply_text(
                f"✅ *Заявка на вывод создана!*\n\n"
                f"💰 Сумма: {amount} ★\n"
                f"📱 Username: @{user[9]}\n"
                f"🆔 Номер заявки: #{withdrawal_id}\n\n"
                f"Ожидайте подтверждения администратора.",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Назад", callback_data="withdraw_menu")]])
            )
            
            # Уведомление админу
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
            
        except ValueError:
            await update.message.reply_text("❌ Введите число")
    
    elif state == 'withdrawal_amount_crypto':
        try:
            amount = int(text)
            
            if amount < 50:
                await update.message.reply_text("❌ Минимальная сумма — 50 ★")
                return
            
            user = db.get_user(user_id)
            if amount > user[3]:
                await update.message.reply_text("❌ Недостаточно средств")
                return
            
            withdrawal_id = db.create_withdrawal(user_id, amount, 'crypto', user[8])
            
            context.user_data.pop('awaiting')
            await update.message.reply_text(
                f"✅ *Заявка на вывод создана!*\n\n"
                f"💰 Сумма: {amount} ★\n"
                f"💳 CryptoBot ID: {user[8]}\n"
                f"🆔 Номер заявки: #{withdrawal_id}\n\n"
                f"Ожидайте подтверждения администратора.",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Назад", callback_data="withdraw_menu")]])
            )
            
            # Уведомление админу
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
            
        except ValueError:
            await update.message.reply_text("❌ Введите число")
    
    elif state == 'broadcast':
        if user_id not in ADMIN_IDS:
            return
        
        context.user_data.pop('awaiting')
        
        users = db.get_all_users()
        sent = 0
        failed = 0
        
        await update.message.reply_text(f"📢 Начинаю рассылку {len(users)} пользователям...")
        
        if update.message.photo:
            photo = update.message.photo[-1].file_id
            caption = update.message.caption or ""
            
            for u in users:
                try:
                    await context.bot.send_photo(
                        chat_id=u[0],
                        photo=photo,
                        caption=caption,
                        parse_mode=ParseMode.MARKDOWN
                    )
                    sent += 1
                    await asyncio.sleep(0.05)
                except:
                    failed += 1
        else:
            for u in users:
                try:
                    await context.bot.send_message(
                        chat_id=u[0],
                        text=text,
                        parse_mode=ParseMode.MARKDOWN
                    )
                    sent += 1
                    await asyncio.sleep(0.05)
                except:
                    failed += 1
        
        await update.message.reply_text(
            f"✅ *Рассылка завершена*\n\n"
            f"📊 Отправлено: {sent}\n"
            f"❌ Ошибок: {failed}",
            parse_mode=ParseMode.MARKDOWN
        )
    
    elif state == 'upload_welcome' and user_id in ADMIN_IDS:
        # Это обрабатывается выше в проверке фото
        pass
    elif state == 'upload_case' and user_id in ADMIN_IDS:
        # Это обрабатывается выше в проверке фото
        pass

def main():
    print("=" * 60)
    print(f"🚀 ЗАПУСК БОТА {BOT_NAME}")
    print("=" * 60)
    print("✅ Telegram Stars пополнение (1★ = 1 Telegram Star)")
    print(f"✅ CryptoBot пополнение (1★ = {RUB_PER_STAR} руб = {TON_PER_STAR:.4f} TON)")
    print("✅ Вывод на Telegram Username")
    print("✅ Вывод на CryptoBot ID")
    print("✅ Админ-панель с рассылкой")
    print("✅ Управление картинками")
    print("✅ Зимний магазин NFT")
    print(f"✅ Твой ID {ADMIN_IDS[0]} - АДМИН")
    print("=" * 60)
    
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(PreCheckoutQueryHandler(precheckout_callback))
    app.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment_callback))
    app.add_handler(MessageHandler(filters.TEXT | filters.PHOTO, handle_message))
    
    print("🤖 Бот запущен и готов к работе!")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()



