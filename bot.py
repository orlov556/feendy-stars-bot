import logging
import random
import sqlite3
import asyncio
import json
import os
import tempfile
import requests
import time
import string
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, LabeledPrice, PreCheckoutQuery, InputMediaPhoto
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes, MessageHandler, filters, PreCheckoutQueryHandler
from telegram.constants import ParseMode

# ======================== НАСТРОЙКА ========================
TELEGRAM_TOKEN = os.environ.get("BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")
CRYPTOBOT_API_KEY = os.environ.get("CRYPTOBOT_API_KEY", "YOUR_CRYPTOBOT_API_KEY")
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
        # Определяем путь к базе данных с учётом Railway Volume
        db_path = self._find_writable_path()
        self.db_path = db_path
        db_exists = os.path.exists(db_path)
        
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.cursor = self.conn.cursor()
        
        if db_exists:
            logger.info(f"✅ База данных загружена из {db_path}")
        else:
            logger.info(f"🆕 Создаём новую базу данных в {db_path}")
        
        self._create_tables()
        self._init_admin()
        self._load_images()
        self._init_promocodes()
    
    def _find_writable_path(self):
        """Пытается найти доступную для записи папку для базы данных"""
        # Приоритет: переменная окружения -> стандартные пути Railway -> временная папка
        candidates = [
            os.environ.get("DB_PATH", ""),
            '/app/data/feendy_stars.db',
            '/data/feendy_stars.db',
            './feendy_stars.db'
        ]
        
        for path in candidates:
            if not path:
                continue
            try:
                dirname = os.path.dirname(path) or '.'
                os.makedirs(dirname, exist_ok=True)
                # Проверка записи
                test_file = os.path.join(dirname, 'write_test.tmp')
                with open(test_file, 'w') as f:
                    f.write('test')
                os.remove(test_file)
                logger.info(f"✅ Выбран путь для БД: {path}")
                return path
            except Exception as e:
                logger.warning(f"⚠️ Нельзя использовать {path}: {e}")
                continue
        
        # Запасной вариант – временная папка (данные могут пропадать)
        fallback = os.path.join(tempfile.gettempdir(), 'feendy_stars.db')
        logger.warning(f"⚠️ Использую временную БД: {fallback} (данные могут быть потеряны при перезапуске!)")
        return fallback
    
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
                last_active TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_bonus_reminder TIMESTAMP
            )
        ''')
        
        # Таблица для статистики выводов
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS withdrawal_stats (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date DATE,
                amount INTEGER,
                user_id INTEGER,
                admin_id INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
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
        """Ежедневный бонус со случайной суммой 1-5 ★"""
        today = datetime.now().date()
        self.cursor.execute(
            'SELECT daily_bonus FROM users WHERE user_id = ?',
            (user_id,)
        )
        result = self.cursor.fetchone()
        
        if not result or not result[0] or datetime.strptime(result[0], '%Y-%m-%d').date() < today:
            # Шансы на выпадение сумм (в пользу казино)
            # 1★ - 40%, 2★ - 30%, 3★ - 15%, 4★ - 10%, 5★ - 5%
            rand = random.random()
            if rand < 0.4:
                bonus = 1
            elif rand < 0.7:
                bonus = 2
            elif rand < 0.85:
                bonus = 3
            elif rand < 0.95:
                bonus = 4
            else:
                bonus = 5
            
            self.cursor.execute('''
                UPDATE users SET daily_bonus = ?, balance = balance + ?, last_active = CURRENT_TIMESTAMP WHERE user_id = ?
            ''', (today, bonus, user_id))
            self.conn.commit()
            return bonus
        return 0
    
    def should_remind_bonus(self, user_id):
        """Проверка, нужно ли напомнить о бонусе"""
        self.cursor.execute('''
            SELECT daily_bonus, last_bonus_reminder FROM users WHERE user_id = ?
        ''', (user_id,))
        result = self.cursor.fetchone()
        
        if not result:
            return False
        
        daily_bonus, last_reminder = result
        today = datetime.now().date()
        
        # Если бонус не получен сегодня и не напоминали сегодня
        if (not daily_bonus or datetime.strptime(daily_bonus, '%Y-%m-%d').date() < today) and \
           (not last_reminder or datetime.strptime(last_reminder, '%Y-%m-%d %H:%M:%S').date() < today):
            return True
        return False
    
    def mark_reminder_sent(self, user_id):
        """Отметить, что напоминание отправлено"""
        self.cursor.execute('''
            UPDATE users SET last_bonus_reminder = CURRENT_TIMESTAMP WHERE user_id = ?
        ''', (user_id,))
        self.conn.commit()
    
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
        self.cursor.execute('SELECT user_id, snowflakes_cost FROM nft_withdrawals WHERE id = ?', (withdrawal_id,))
        withdrawal = self.cursor.fetchone()
        
        if withdrawal:
            user_id, cost = withdrawal
            self.update_snowflakes(user_id, cost)
        
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
        """Создание заявки на вывод звёзд"""
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
        """Одобрение заявки на вывод с записью в статистику"""
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
        
        # Списываем баланс
        self.update_balance(user_id, -amount)
        
        # Обновляем статус заявки
        self.cursor.execute('''
            UPDATE withdrawals 
            SET status = 'approved', admin_id = ?, processed_at = CURRENT_TIMESTAMP
            WHERE id = ?
        ''', (admin_id, withdrawal_id))
        
        # Записываем в статистику выводов
        self.cursor.execute('''
            INSERT INTO withdrawal_stats (date, amount, user_id, admin_id)
            VALUES (date('now'), ?, ?, ?)
        ''', (amount, user_id, admin_id))
        
        # Обновляем общую сумму выводов пользователя
        self.cursor.execute('''
            UPDATE users SET total_withdrawn = total_withdrawn + ? WHERE user_id = ?
        ''', (amount, user_id))
        
        self.conn.commit()
        return True
    
    def mark_withdrawal_sent(self, withdrawal_id, admin_id):
        """Отметка о том, что средства отправлены"""
        self.cursor.execute('''
            UPDATE withdrawals 
            SET status = 'completed', admin_id = ?, processed_at = CURRENT_TIMESTAMP
            WHERE id = ? AND status = 'approved'
        ''', (admin_id, withdrawal_id))
        self.conn.commit()
        return self.cursor.rowcount > 0
    
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
        """История выводов пользователя"""
        self.cursor.execute('''
            SELECT * FROM withdrawals 
            WHERE user_id = ? 
            ORDER BY created_at DESC
        ''', (user_id,))
        return self.cursor.fetchall()
    
    # ================== СТАТИСТИКА ДЛЯ АДМИНА ==================
    
    def get_withdrawal_stats(self, period='day'):
        """Статистика выводов за период"""
        if period == 'day':
            date_filter = "date = date('now')"
        elif period == 'week':
            date_filter = "date >= date('now', '-7 days')"
        elif period == 'month':
            date_filter = "date >= date('now', '-30 days')"
        else:
            date_filter = "1=1"
        
        self.cursor.execute(f'''
            SELECT SUM(amount), COUNT(*) FROM withdrawal_stats WHERE {date_filter}
        ''')
        result = self.cursor.fetchone()
        return {'total': result[0] or 0, 'count': result[1] or 0}
    
    def get_daily_stats(self):
        """Ежедневная статистика"""
        today = datetime.now().date()
        
        # Новые пользователи сегодня
        self.cursor.execute('SELECT COUNT(*) FROM users WHERE date(created_at) = date("now")')
        new_users = self.cursor.fetchone()[0]
        
        # Активные сегодня
        self.cursor.execute('SELECT COUNT(*) FROM users WHERE date(last_active) = date("now")')
        active_today = self.cursor.fetchone()[0]
        
        # Пополнения сегодня
        self.cursor.execute('SELECT SUM(amount) FROM payments WHERE date(created_at) = date("now") AND status = "completed"')
        deposits = self.cursor.fetchone()[0] or 0
        
        # Выводы сегодня
        withdrawals = self.get_withdrawal_stats('day')
        
        # Игры сегодня
        self.cursor.execute('SELECT COUNT(*) FROM games WHERE date(created_at) = date("now")')
        games_today = self.cursor.fetchone()[0]
        
        # Прибыль (депозиты - выводы)
        profit = deposits - withdrawals['total']
        
        return {
            'new_users': new_users,
            'active_today': active_today,
            'deposits': deposits,
            'withdrawals': withdrawals['total'],
            'withdrawal_count': withdrawals['count'],
            'games': games_today,
            'profit': profit
        }
    
    def get_weekly_stats(self):
        """Недельная статистика"""
        week_ago = (datetime.now() - timedelta(days=7)).date()
        
        # Новые пользователи за неделю
        self.cursor.execute('SELECT COUNT(*) FROM users WHERE date(created_at) >= ?', (week_ago,))
        new_users = self.cursor.fetchone()[0]
        
        # Активные за неделю
        self.cursor.execute('SELECT COUNT(DISTINCT user_id) FROM games WHERE date(created_at) >= ?', (week_ago,))
        active_week = self.cursor.fetchone()[0]
        
        # Пополнения за неделю
        self.cursor.execute('SELECT SUM(amount) FROM payments WHERE date(created_at) >= ? AND status = "completed"', (week_ago,))
        deposits = self.cursor.fetchone()[0] or 0
        
        # Выводы за неделю
        withdrawals = self.get_withdrawal_stats('week')
        
        # Игры за неделю
        self.cursor.execute('SELECT COUNT(*) FROM games WHERE date(created_at) >= ?', (week_ago,))
        games_week = self.cursor.fetchone()[0]
        
        return {
            'new_users': new_users,
            'active_week': active_week,
            'deposits': deposits,
            'withdrawals': withdrawals['total'],
            'withdrawal_count': withdrawals['count'],
            'games': games_week,
            'profit': deposits - withdrawals['total']
        }
    
    def get_monthly_stats(self):
        """Месячная статистика"""
        month_ago = (datetime.now() - timedelta(days=30)).date()
        
        # Новые пользователи за месяц
        self.cursor.execute('SELECT COUNT(*) FROM users WHERE date(created_at) >= ?', (month_ago,))
        new_users = self.cursor.fetchone()[0]
        
        # Активные за месяц
        self.cursor.execute('SELECT COUNT(DISTINCT user_id) FROM games WHERE date(created_at) >= ?', (month_ago,))
        active_month = self.cursor.fetchone()[0]
        
        # Пополнения за месяц
        self.cursor.execute('SELECT SUM(amount) FROM payments WHERE date(created_at) >= ? AND status = "completed"', (month_ago,))
        deposits = self.cursor.fetchone()[0] or 0
        
        # Выводы за месяц
        withdrawals = self.get_withdrawal_stats('month')
        
        # Игры за месяц
        self.cursor.execute('SELECT COUNT(*) FROM games WHERE date(created_at) >= ?', (month_ago,))
        games_month = self.cursor.fetchone()[0]
        
        return {
            'new_users': new_users,
            'active_month': active_month,
            'deposits': deposits,
            'withdrawals': withdrawals['total'],
            'withdrawal_count': withdrawals['count'],
            'games': games_month,
            'profit': deposits - withdrawals['total']
        }
    
    # ================== ПРОМОКОДЫ ==================
    
    def generate_promocode(self, amount, days_valid, max_uses, created_by):
        """Генерация нового промокода"""
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
        promo = self.get_promocode_info(code)
        if not promo:
            return {'success': False, 'reason': '❌ Код не найден'}
        
        if promo[3] and datetime.now().date() > datetime.strptime(promo[3], '%Y-%m-%d').date():
            return {'success': False, 'reason': '❌ Промокод истёк'}
        
        if promo[4] > 0 and promo[5] >= promo[4]:
            return {'success': False, 'reason': '❌ Промокод уже использован максимальное количество раз'}
        
        self.cursor.execute('SELECT * FROM promocode_uses WHERE user_id = ? AND code = ?', (user_id, code))
        if self.cursor.fetchone():
            return {'success': False, 'reason': '❌ Вы уже активировали этот промокод'}
        
        self.update_balance(user_id, promo[2])
        
        self.cursor.execute('INSERT INTO promocode_uses (user_id, code) VALUES (?, ?)', (user_id, code))
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
    'flip': {'win_chance': 45, 'multiplier': 1.7, 'name': '🪙 Орёл и решка', 'emoji': '🪙'},
    'roulette': {'win_chance': 20, 'multiplier': 4.5, 'name': '💀 Русская рулетка', 'emoji': '💀'},
    'wheel': {'win_chance': 8, 'multiplier': 10, 'name': '🎡 Колесо удачи', 'emoji': '🎰'},
    'dice': {'win_chance': 30, 'multiplier': 2.5, 'name': '🎲 Кости', 'emoji': '🎲'},
    'slots': {'win_chance': 25, 'multiplier': 3.0, 'name': '🎰 Слоты', 'emoji': '🎰'}
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
            InlineKeyboardButton("🎟️ Промокод", callback_data="activate_promo"),
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
        text = "🎰 *Казино FEENDY STARS*\n\nВыберите игру:"
        keyboard = [
            [
                InlineKeyboardButton("🎰 Слоты", callback_data="game_slots_menu"),
                InlineKeyboardButton("💀 Русская рулетка", callback_data="game_roulette_menu")
            ],
            [
                InlineKeyboardButton("💣 Минное поле", callback_data="game_mines_menu"),
                InlineKeyboardButton("🎡 Рулетка", callback_data="game_roulette_classic_menu")
            ],
            [
                InlineKeyboardButton("🎲 Кости", callback_data="game_dice_menu")
            ],
            [InlineKeyboardButton("◀️ Главное меню", callback_data="main_menu")]
        ]
        await edit_message(query, text, InlineKeyboardMarkup(keyboard))
    
    # ================== СЛОТЫ ==================
    
    elif data == "game_slots_menu":
        text = (
            f"🎰 *Слоты*\n\n"
            f"💰 Ваш баланс: {user[3]} ★\n\n"
            f"Выберите ставку:\n"
            f"Комбинации:\n"
            f"• Разные (x1.4)\n"
            f"• 2 одинаковых (x1.5)\n"
            f"• 3 одинаковых (x5.0)"
        )
        keyboard = [
            [
                InlineKeyboardButton("10 ★", callback_data="slots_10"),
                InlineKeyboardButton("25 ★", callback_data="slots_25"),
                InlineKeyboardButton("50 ★", callback_data="slots_50")
            ],
            [
                InlineKeyboardButton("100 ★", callback_data="slots_100"),
                InlineKeyboardButton("250 ★", callback_data="slots_250"),
                InlineKeyboardButton("500 ★", callback_data="slots_500")
            ],
            [InlineKeyboardButton("◀️ Назад", callback_data="casino_menu")]
        ]
        await edit_message(query, text, InlineKeyboardMarkup(keyboard))
    
    elif data.startswith("slots_"):
        bet = int(data.replace("slots_", ""))
        
        if bet > user[3]:
            await edit_message(query, f"❌ Недостаточно! Баланс: {user[3]} ★")
            return
        
        db.update_balance(user_id, -bet)
        
        msg = await context.bot.send_dice(chat_id=user_id, emoji='🎰')
        result = msg.dice.value
        
        if result == 64:
            multiplier = 5.0
            win = int(bet * multiplier)
            result_text = "🎰 ДЖЕКПОТ! ТРИ ОДИНАКОВЫХ!"
        elif result in [22, 43]:
            multiplier = 1.5
            win = int(bet * multiplier)
            result_text = "🎰 ДВА ОДИНАКОВЫХ!"
        else:
            multiplier = 0
            win = 0
            result_text = "❌ Разные символы"
        
        if win > 0:
            db.update_balance(user_id, win)
            db.add_game(user_id, 'slots', bet, multiplier, win, 'win')
            await edit_message(query,
                f"🎉 *ВЫИГРЫШ В СЛОТАХ!*\n\n"
                f"{result_text}\n"
                f"💰 Ставка: {bet} ★\n"
                f"📈 Множитель: x{multiplier}\n"
                f"💎 Выигрыш: {win} ★"
            )
        else:
            db.add_lost_stars(user_id, bet)
            db.add_game(user_id, 'slots', bet, 0, 0, 'lose')
            await edit_message(query,
                f"😢 *ПРОИГРЫШ В СЛОТАХ*\n\n"
                f"{result_text}\n"
                f"💰 Ставка: {bet} ★ проиграна\n"
                f"✨ +{int(bet * 0.5)} снежинок"
            )
    
    # ================== РУССКАЯ РУЛЕТКА ==================
    
    elif data == "game_roulette_menu":
        text = (
            f"💀 *Русская рулетка*\n\n"
            f"💰 Ваш баланс: {user[3]} ★\n\n"
            f"Выберите количество патронов в барабане:\n"
            f"Чем больше патронов, тем выше множитель!"
        )
        keyboard = [
            [
                InlineKeyboardButton("1 патрон (x1.1)", callback_data="roulette_1"),
                InlineKeyboardButton("2 патрона (x1.35)", callback_data="roulette_2"),
                InlineKeyboardButton("3 патрона (x1.75)", callback_data="roulette_3")
            ],
            [
                InlineKeyboardButton("4 патрона (x2.5)", callback_data="roulette_4"),
                InlineKeyboardButton("5 патронов (x4.5)", callback_data="roulette_5")
            ],
            [InlineKeyboardButton("◀️ Назад", callback_data="casino_menu")]
        ]
        await edit_message(query, text, InlineKeyboardMarkup(keyboard))
    
    elif data.startswith("roulette_"):
        bullets = int(data.replace("roulette_", ""))
        context.user_data['roulette_bullets'] = bullets
        context.user_data['awaiting'] = 'roulette_bet'
        await edit_message(
            query,
            f"💀 *Русская рулетка*\n\nПатронов: {bullets}\n\nВведите сумму ставки:"
        )
    
    # ================== МИННОЕ ПОЛЕ ==================
    
    elif data == "game_mines_menu":
        text = (
            f"💣 *Минное поле*\n\n"
            f"💰 Ваш баланс: {user[3]} ★\n\n"
            f"Выберите количество мин на поле 5x5:\n"
            f"Больше мин = выше множитель!"
        )
        keyboard = [
            [
                InlineKeyboardButton("3 мины (x1.2)", callback_data="mines_3"),
                InlineKeyboardButton("4 мины (x1.45)", callback_data="mines_4"),
                InlineKeyboardButton("5 мин (x1.75)", callback_data="mines_5")
            ],
            [
                InlineKeyboardButton("6 мин (x2.2)", callback_data="mines_6"),
                InlineKeyboardButton("7 мин (x2.8)", callback_data="mines_7"),
                InlineKeyboardButton("8 мин (x4.0)", callback_data="mines_8")
            ],
            [
                InlineKeyboardButton("9 мин (x7.5)", callback_data="mines_9")
            ],
            [InlineKeyboardButton("◀️ Назад", callback_data="casino_menu")]
        ]
        await edit_message(query, text, InlineKeyboardMarkup(keyboard))
    
    elif data.startswith("mines_"):
        mines = int(data.replace("mines_", ""))
        context.user_data['mines_count'] = mines
        context.user_data['awaiting'] = 'mines_bet'
        await edit_message(
            query,
            f"💣 *Минное поле*\n\nМин: {mines}\n\nВведите сумму ставки:"
        )
    
    # ================== РУЛЕТКА ==================
    
    elif data == "game_roulette_classic_menu":
        text = (
            f"🎡 *Рулетка*\n\n"
            f"💰 Ваш баланс: {user[3]} ★\n\n"
            f"Выберите ставку:"
        )
        keyboard = [
            [InlineKeyboardButton("🔴 Красное (x1.75)", callback_data="roulette_red"),
             InlineKeyboardButton("⚫ Черное (x1.75)", callback_data="roulette_black")],
            [InlineKeyboardButton("🟢 Зеленое 0 (x10)", callback_data="roulette_green")],
            [InlineKeyboardButton("1-18 (x1.75)", callback_data="roulette_low"),
             InlineKeyboardButton("19-36 (x1.75)", callback_data="roulette_high")],
            [InlineKeyboardButton("◀️ Назад", callback_data="casino_menu")]
        ]
        await edit_message(query, text, InlineKeyboardMarkup(keyboard))
    
    elif data.startswith("roulette_"):
        bet_type = data.replace("roulette_", "")
        context.user_data['roulette_type'] = bet_type
        context.user_data['awaiting'] = 'roulette_classic_bet'
        await edit_message(
            query,
            f"🎡 *Рулетка*\n\nТип ставки: {bet_type}\n\nВведите сумму ставки:"
        )
    
    # ================== КОСТИ ==================
    
    elif data == "game_dice_menu":
        text = (
            f"🎲 *Кости*\n\n"
            f"💰 Ваш баланс: {user[3]} ★\n\n"
            f"Выберите ставку:"
        )
        keyboard = [
            [
                InlineKeyboardButton("1 (x4.75)", callback_data="dice_1"),
                InlineKeyboardButton("2 (x4.75)", callback_data="dice_2"),
                InlineKeyboardButton("3 (x4.75)", callback_data="dice_3")
            ],
            [
                InlineKeyboardButton("4 (x4.75)", callback_data="dice_4"),
                InlineKeyboardButton("5 (x4.75)", callback_data="dice_5"),
                InlineKeyboardButton("6 (x4.75)", callback_data="dice_6")
            ],
            [
                InlineKeyboardButton("Чёт (x1.7)", callback_data="dice_even"),
                InlineKeyboardButton("Нечёт (x1.7)", callback_data="dice_odd")
            ],
            [InlineKeyboardButton("◀️ Назад", callback_data="casino_menu")]
        ]
        await edit_message(query, text, InlineKeyboardMarkup(keyboard))
    
    elif data.startswith("dice_"):
        bet_type = data.replace("dice_", "")
        context.user_data['dice_type'] = bet_type
        context.user_data['awaiting'] = 'dice_bet'
        await edit_message(
            query,
            f"🎲 *Кости*\n\nТип ставки: {bet_type}\n\nВведите сумму ставки:"
        )
    
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
        
        for admin_id in ADMIN_IDS:
            try:
                keyboard_admin = [
                    [InlineKeyboardButton(f"✅ Принять #{withdraw_id}", callback_data=f"approve_nft_{withdraw_id}"),
                     InlineKeyboardButton(f"❌ Отклонить #{withdraw_id}", callback_data=f"reject_nft_{withdraw_id}")]
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
            except Exception as e:
                logger.error(f"Ошибка отправки NFT заявки админу: {e}")
        
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
        bonus = db.check_daily_bonus(user_id)
        if bonus > 0:
            await edit_message(query, f"🎁 +{bonus} ★")
        else:
            await edit_message(query, "❌ Бонус уже получен сегодня")
    
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
        
        daily_stats = db.get_daily_stats()
        
        text = (
            f"⚙️ *Админ-панель {BOT_NAME}*\n\n"
            f"📊 *Общая статистика:*\n"
            f"👥 Пользователей: {stats['total_users']}\n"
            f"💰 Общий баланс: {stats['total_balance']} ★\n"
            f"❄️ Снежинок: {stats['total_snowflakes']} ✨\n"
            f"💸 Выведено всего: {stats['total_withdrawn']} ★\n"
            f"🎮 Игр: {stats['total_games']}\n\n"
            f"📈 *Статистика за сегодня:*\n"
            f"👤 Новых: {daily_stats['new_users']}\n"
            f"🎮 Игр: {daily_stats['games']}\n"
            f"💰 Пополнений: {daily_stats['deposits']} ★\n"
            f"💸 Выплачено: {daily_stats['withdrawals']} ★ ({daily_stats['withdrawal_count']} шт)\n"
            f"📊 Прибыль: {daily_stats['profit']} ★\n\n"
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
            [InlineKeyboardButton("📊 Дневная статистика", callback_data="admin_stats_daily")],
            [InlineKeyboardButton("📊 Недельная статистика", callback_data="admin_stats_weekly")],
            [InlineKeyboardButton("📊 Месячная статистика", callback_data="admin_stats_monthly")],
            [InlineKeyboardButton("◀️ Главное меню", callback_data="main_menu")]
        ]
        
        await edit_message(query, text, InlineKeyboardMarkup(keyboard))
    
    # ================== СТАТИСТИКА ДЛЯ АДМИНА ==================
    
    elif data == "admin_stats_daily":
        if user_id not in ADMIN_IDS:
            return
        
        stats = db.get_daily_stats()
        
        text = (
            f"📊 *Дневная статистика*\n\n"
            f"👤 *Новые пользователи:* {stats['new_users']}\n"
            f"🎮 *Активных сегодня:* {stats['active_today']}\n"
            f"💰 *Пополнения:* {stats['deposits']} ★\n"
            f"💸 *Выплаты:* {stats['withdrawals']} ★ ({stats['withdrawal_count']} шт)\n"
            f"🎲 *Сыграно игр:* {stats['games']}\n"
            f"📈 *Прибыль:* {stats['profit']} ★"
        )
        
        keyboard = [[InlineKeyboardButton("◀️ Назад", callback_data="admin_panel")]]
        await edit_message(query, text, InlineKeyboardMarkup(keyboard))
    
    elif data == "admin_stats_weekly":
        if user_id not in ADMIN_IDS:
            return
        
        stats = db.get_weekly_stats()
        
        text = (
            f"📊 *Недельная статистика*\n\n"
            f"👤 *Новые пользователи:* {stats['new_users']}\n"
            f"🎮 *Активных за неделю:* {stats['active_week']}\n"
            f"💰 *Пополнения:* {stats['deposits']} ★\n"
            f"💸 *Выплаты:* {stats['withdrawals']} ★ ({stats['withdrawal_count']} шт)\n"
            f"🎲 *Сыграно игр:* {stats['games']}\n"
            f"📈 *Прибыль:* {stats['profit']} ★"
        )
        
        keyboard = [[InlineKeyboardButton("◀️ Назад", callback_data="admin_panel")]]
        await edit_message(query, text, InlineKeyboardMarkup(keyboard))
    
    elif data == "admin_stats_monthly":
        if user_id not in ADMIN_IDS:
            return
        
        stats = db.get_monthly_stats()
        
        text = (
            f"📊 *Месячная статистика*\n\n"
            f"👤 *Новые пользователи:* {stats['new_users']}\n"
            f"🎮 *Активных за месяц:* {stats['active_month']}\n"
            f"💰 *Пополнения:* {stats['deposits']} ★\n"
            f"💸 *Выплаты:* {stats['withdrawals']} ★ ({stats['withdrawal_count']} шт)\n"
            f"🎲 *Сыграно игр:* {stats['games']}\n"
            f"📈 *Прибыль:* {stats['profit']} ★"
        )
        
        keyboard = [[InlineKeyboardButton("◀️ Назад", callback_data="admin_panel")]]
        await edit_message(query, text, InlineKeyboardMarkup(keyboard))
    
    # ================== УПРАВЛЕНИЕ ПОЛЬЗОВАТЕЛЯМИ ==================
    
    elif data.startswith("admin_users_page_"):
        if user_id not in ADMIN_IDS:
            return
        
        try:
            parts = data.replace("admin_users_page_", "").split('_')
            page = int(parts[0])
            sort_by = parts[1] if len(parts) > 1 else 'date'
            order = parts[2] if len(parts) > 2 else 'desc'
            
            users_per_page = 10
            total_users = db.get_total_users_count()
            total_pages = (total_users + users_per_page - 1) // users_per_page
            
            if page < 1:
                page = 1
            if page > total_pages and total_pages > 0:
                page = total_pages
            
            offset = (page - 1) * users_per_page
            users = db.get_all_users(sort_by=sort_by, order=order, limit=users_per_page, offset=offset)
            
            if not users:
                await edit_message(query, "👥 Нет пользователей")
                return
            
            text = f"👥 *Страница {page} из {total_pages}*\n\n"
            
            for u in users:
                user_id_db = u[0]
                username = u[1] or "нет"
                first_name = u[2] or "Без имени"
                balance = u[3]
                snowflakes = u[4]
                status = "🔴" if u[5] == 1 else "🟢"
                admin = "👑" if u[6] == 1 else ""
                last_active = u[8][:10] if u[8] and len(u) > 8 else "никогда"
                
                text += f"{status}{admin} {first_name[:15]} (@{username}) — {balance} ★ | ✨ {snowflakes} | {last_active}\n"
            
            keyboard = []
            
            sort_row = [
                InlineKeyboardButton("📅", callback_data=f"admin_users_page_1_date_desc"),
                InlineKeyboardButton("💰", callback_data=f"admin_users_page_1_balance_desc"),
                InlineKeyboardButton("✨", callback_data=f"admin_users_page_1_snowflakes_desc"),
            ]
            keyboard.append(sort_row)
            
            nav_row = []
            if page > 1:
                nav_row.append(InlineKeyboardButton("◀️", callback_data=f"admin_users_page_{page-1}_{sort_by}_{order}"))
            nav_row.append(InlineKeyboardButton(f"📄 {page}/{total_pages}", callback_data="noop"))
            if page < total_pages:
                nav_row.append(InlineKeyboardButton("▶️", callback_data=f"admin_users_page_{page+1}_{sort_by}_{order}"))
            keyboard.append(nav_row)
            
            keyboard.append([InlineKeyboardButton("🔍 Поиск пользователя", callback_data="admin_search_user")])
            keyboard.append([InlineKeyboardButton("◀️ Назад", callback_data="admin_panel")])
            
            await edit_message(query, text, InlineKeyboardMarkup(keyboard))
            
        except Exception as e:
            logger.error(f"Ошибка в пагинации: {e}")
            await edit_message(query, f"❌ Ошибка: {str(e)}")
    
    elif data == "admin_search_user":
        if user_id not in ADMIN_IDS:
            return
        
        context.user_data['awaiting'] = 'search_user'
        await edit_message(
            query,
            "🔍 *Поиск пользователя*\n\nВведите ID или Username:"
        )
    
    # ================== ЗАЯВКИ НА ВЫВОД ЗВЁЗД ==================
    
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
                InlineKeyboardButton(f"✅ Принять #{w[0]}", callback_data=f"approve_withdrawal_{w[0]}"),
                InlineKeyboardButton(f"❌ Отклонить #{w[0]}", callback_data=f"reject_withdrawal_{w[0]}")
            ])
        
        keyboard.append([InlineKeyboardButton("◀️ Назад", callback_data="admin_panel")])
        await edit_message(query, text, InlineKeyboardMarkup(keyboard))
    
    elif data.startswith("approve_withdrawal_"):
        if user_id not in ADMIN_IDS:
            return
        
        withdrawal_id = int(data.replace("approve_withdrawal_", ""))
        
        if db.approve_withdrawal(withdrawal_id, user_id):
            await edit_message(query, f"✅ Заявка #{withdrawal_id} одобрена")
            
            db.cursor.execute('SELECT user_id, amount FROM withdrawals WHERE id = ?', (withdrawal_id,))
            w_user_id, amount = db.cursor.fetchone()
            
            try:
                keyboard_user = [
                    [InlineKeyboardButton("💰 Выведено", callback_data=f"mark_sent_{withdrawal_id}")]
                ]
                
                await context.bot.send_message(
                    w_user_id,
                    f"✅ *Заявка на вывод одобрена!*\n\n"
                    f"💰 Сумма: {amount} ★\n"
                    f"⏳ Ожидайте, средства будут отправлены в течение 10-15 минут.\n\n"
                    f"После получения нажмите кнопку ниже, чтобы подтвердить.",
                    parse_mode=ParseMode.MARKDOWN,
                    reply_markup=InlineKeyboardMarkup(keyboard_user)
                )
            except:
                pass
        else:
            await edit_message(query, f"❌ Ошибка")
    
    elif data.startswith("mark_sent_"):
        withdrawal_id = int(data.replace("mark_sent_", ""))
        
        if db.mark_withdrawal_sent(withdrawal_id, user_id):
            await edit_message(query, f"✅ Вывод #{withdrawal_id} отмечен как выполненный")
            
            db.cursor.execute('SELECT user_id, amount FROM withdrawals WHERE id = ?', (withdrawal_id,))
            w_user_id, amount = db.cursor.fetchone()
            
            for admin_id in ADMIN_IDS:
                try:
                    await context.bot.send_message(
                        admin_id,
                        f"💰 *Вывод выполнен*\n\n"
                        f"👤 Пользователь подтвердил получение\n"
                        f"🆔 Заявка #{withdrawal_id}\n"
                        f"💰 Сумма: {amount} ★",
                        parse_mode=ParseMode.MARKDOWN
                    )
                except:
                    pass
        else:
            await edit_message(query, f"❌ Ошибка")
    
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
                InlineKeyboardButton(f"✅ Принять #{w[0]}", callback_data=f"approve_nft_{w[0]}"),
                InlineKeyboardButton(f"❌ Отклонить #{w[0]}", callback_data=f"reject_nft_{w[0]}")
            ])
        
        keyboard.append([InlineKeyboardButton("◀️ Назад", callback_data="admin_panel")])
        await edit_message(query, text, InlineKeyboardMarkup(keyboard))
    
    elif data.startswith("approve_nft_"):
        if user_id not in ADMIN_IDS:
            return
        
        withdrawal_id = int(data.replace("approve_nft_", ""))
        
        if db.approve_nft_withdrawal(withdrawal_id, user_id):
            await edit_message(query, f"✅ Заявка #{withdrawal_id} одобрена")
            
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
                    f"💰 *Пополнение Stars*\n\n👤 @{user[1] or user_id}\n💎 {amount} ★",
                    parse_mode=ParseMode.MARKDOWN
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
        # Проверяем, нужно ли напомнить о бонусе
        if db.should_remind_bonus(user_id):
            await context.bot.send_message(
                user_id,
                "🎁 *Напоминание о ежедневном бонусе!*\n\n"
                "Не забудь забрать свой бонус в разделе 🎁 Ежедневный бонус\n"
                "Сегодня может выпасть от 1 до 5 ★!",
                parse_mode=ParseMode.MARKDOWN
            )
            db.mark_reminder_sent(user_id)
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
                response += f"{status}{admin} {r[2]} (@{r[1]}) — ID: `{r[0]}` | {r[3]} ★ | ✨ {r[4]}\n"
            
            await update.message.reply_text(response, parse_mode=ParseMode.MARKDOWN)
        
        context.user_data.pop('awaiting')
        return
    
    # ===== ОБРАБОТКА СТАВОК =====
    
    if state == 'roulette_bet':
        try:
            bet = int(text)
            bullets = context.user_data.get('roulette_bullets', 3)
            
            if bet < 10:
                await update.message.reply_text("❌ Минимальная ставка 10 ★")
                return
            
            user = db.get_user(user_id)
            if bet > user[3]:
                await update.message.reply_text("❌ Недостаточно средств")
                return
            
            db.update_balance(user_id, -bet)
            
            # Логика русской рулетки
            win_chance = (6 - bullets) / 6
            roll = random.random()
            
            if roll <= win_chance:
                multipliers = {1: 1.1, 2: 1.35, 3: 1.75, 4: 2.5, 5: 4.5}
                win_amount = int(bet * multipliers[bullets])
                db.update_balance(user_id, win_amount)
                db.add_game(user_id, 'roulette', bet, multipliers[bullets], win_amount, 'win')
                
                await update.message.reply_text(
                    f"🎉 *ВЫ ВЫЖИЛИ!*\n\n"
                    f"💀 Патронов: {bullets}\n"
                    f"💰 Ставка: {bet} ★\n"
                    f"📈 Множитель: x{multipliers[bullets]}\n"
                    f"💎 Выигрыш: {win_amount} ★"
                )
            else:
                db.add_lost_stars(user_id, bet)
                db.add_game(user_id, 'roulette', bet, 0, 0, 'lose')
                
                await update.message.reply_text(
                    f"💀 *ВЫСТРЕЛ! ВЫ ПРОИГРАЛИ*\n\n"
                    f"💰 Ставка: {bet} ★ проиграна\n"
                    f"✨ +{int(bet * 0.5)} снежинок"
                )
            
            context.user_data.pop('awaiting')
            context.user_data.pop('roulette_bullets')
            
        except ValueError:
            await update.message.reply_text("❌ Введите число")
        return
    
    if state == 'roulette_classic_bet':
        try:
            bet = int(text)
            bet_type = context.user_data.get('roulette_type', 'red')
            
            if bet < 10:
                await update.message.reply_text("❌ Минимальная ставка 10 ★")
                return
            
            user = db.get_user(user_id)
            if bet > user[3]:
                await update.message.reply_text("❌ Недостаточно средств")
                return
            
            db.update_balance(user_id, -bet)
            
            number = random.randint(0, 36)
            color = 'green' if number == 0 else ('red' if number in [1,3,5,7,9,12,14,16,18,19,21,23,25,27,30,32,34,36] else 'black')
            
            win = False
            multiplier = 0
            
            if bet_type == 'red' and color == 'red':
                win = True
                multiplier = 1.75
            elif bet_type == 'black' and color == 'black':
                win = True
                multiplier = 1.75
            elif bet_type == 'green' and color == 'green':
                win = True
                multiplier = 10
            elif bet_type == 'low' and 1 <= number <= 18:
                win = True
                multiplier = 1.75
            elif bet_type == 'high' and 19 <= number <= 36:
                win = True
                multiplier = 1.75
            
            if win:
                win_amount = int(bet * multiplier)
                db.update_balance(user_id, win_amount)
                db.add_game(user_id, 'roulette_classic', bet, multiplier, win_amount, 'win')
                
                await update.message.reply_text(
                    f"🎉 *ВЫИГРЫШ В РУЛЕТКЕ!*\n\n"
                    f"🎡 Выпало число: {number} {color}\n"
                    f"💰 Ставка: {bet} ★\n"
                    f"📈 Множитель: x{multiplier}\n"
                    f"💎 Выигрыш: {win_amount} ★"
                )
            else:
                db.add_lost_stars(user_id, bet)
                db.add_game(user_id, 'roulette_classic', bet, 0, 0, 'lose')
                
                await update.message.reply_text(
                    f"😢 *ПРОИГРЫШ В РУЛЕТКЕ*\n\n"
                    f"🎡 Выпало число: {number} {color}\n"
                    f"💰 Ставка: {bet} ★ проиграна\n"
                    f"✨ +{int(bet * 0.5)} снежинок"
                )
            
            context.user_data.pop('awaiting')
            context.user_data.pop('roulette_type')
            
        except ValueError:
            await update.message.reply_text("❌ Введите число")
        return
    
    if state == 'dice_bet':
        try:
            bet = int(text)
            bet_type = context.user_data.get('dice_type', '1')
            
            if bet < 10:
                await update.message.reply_text("❌ Минимальная ставка 10 ★")
                return
            
            user = db.get_user(user_id)
            if bet > user[3]:
                await update.message.reply_text("❌ Недостаточно средств")
                return
            
            db.update_balance(user_id, -bet)
            
            msg = await context.bot.send_dice(chat_id=user_id, emoji='🎲')
            result = msg.dice.value
            
            win = False
            multiplier = 0
            
            if bet_type in ['1','2','3','4','5','6'] and result == int(bet_type):
                win = True
                multiplier = 4.75
            elif bet_type == 'even' and result % 2 == 0:
                win = True
                multiplier = 1.7
            elif bet_type == 'odd' and result % 2 == 1:
                win = True
                multiplier = 1.7
            
            if win:
                win_amount = int(bet * multiplier)
                db.update_balance(user_id, win_amount)
                db.add_game(user_id, 'dice', bet, multiplier, win_amount, 'win')
                
                await update.message.reply_text(
                    f"🎉 *ВЫИГРЫШ В КОСТЯХ!*\n\n"
                    f"🎲 Выпало: {result}\n"
                    f"💰 Ставка: {bet} ★\n"
                    f"📈 Множитель: x{multiplier}\n"
                    f"💎 Выигрыш: {win_amount} ★"
                )
            else:
                db.add_lost_stars(user_id, bet)
                db.add_game(user_id, 'dice', bet, 0, 0, 'lose')
                
                await update.message.reply_text(
                    f"😢 *ПРОИГРЫШ В КОСТЯХ*\n\n"
                    f"🎲 Выпало: {result}\n"
                    f"💰 Ставка: {bet} ★ проиграна\n"
                    f"✨ +{int(bet * 0.5)} снежинок"
                )
            
            context.user_data.pop('awaiting')
            context.user_data.pop('dice_type')
            
        except ValueError:
            await update.message.reply_text("❌ Введите число")
        return
    
    if state == 'mines_bet':
        try:
            bet = int(text)
            mines = context.user_data.get('mines_count', 5)
            
            if bet < 10:
                await update.message.reply_text("❌ Минимальная ставка 10 ★")
                return
            
            user = db.get_user(user_id)
            if bet > user[3]:
                await update.message.reply_text("❌ Недостаточно средств")
                return
            
            db.update_balance(user_id, -bet)
            
            # Простая логика минного поля (можно усложнить позже)
            safe_cells = 25 - mines
            win_chance = safe_cells / 25
            roll = random.random()
            
            if roll <= win_chance:
                multipliers = {3: 1.2, 4: 1.45, 5: 1.75, 6: 2.2, 7: 2.8, 8: 4.0, 9: 7.5}
                win_amount = int(bet * multipliers[mines])
                db.update_balance(user_id, win_amount)
                db.add_game(user_id, 'mines', bet, multipliers[mines], win_amount, 'win')
                
                await update.message.reply_text(
                    f"🎉 *ВЫ ПРОШЛИ МИННОЕ ПОЛЕ!*\n\n"
                    f"💣 Мин: {mines}\n"
                    f"💰 Ставка: {bet} ★\n"
                    f"📈 Множитель: x{multipliers[mines]}\n"
                    f"💎 Выигрыш: {win_amount} ★"
                )
            else:
                db.add_lost_stars(user_id, bet)
                db.add_game(user_id, 'mines', bet, 0, 0, 'lose')
                
                await update.message.reply_text(
                    f"💥 *ВЗРЫВ! ВЫ ПРОИГРАЛИ*\n\n"
                    f"💰 Ставка: {bet} ★ проиграна\n"
                    f"✨ +{int(bet * 0.5)} снежинок"
                )
            
            context.user_data.pop('awaiting')
            context.user_data.pop('mines_count')
            
        except ValueError:
            await update.message.reply_text("❌ Введите число")
        return
    
    # ===== ОБРАБОТКА ОТКАЗА ВЫВОДА ЗВЁЗД =====
    
    if state == 'reject_withdrawal_reason':
        if user_id not in ADMIN_IDS:
            return
        
        withdrawal_id = context.user_data.get('reject_withdrawal_id')
        reason = text
        
        if db.reject_withdrawal(withdrawal_id, user_id, reason):
            await update.message.reply_text(f"✅ Заявка #{withdrawal_id} отклонена")
            
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
    
    # ===== ОБРАБОТКА ОТКАЗА ВЫВОДА NFT =====
    
    if state == 'reject_nft_reason':
        if user_id not in ADMIN_IDS:
            return
        
        withdrawal_id = context.user_data.get('reject_nft_id')
        reason = text
        
        if db.reject_nft_withdrawal(withdrawal_id, user_id, reason):
            await update.message.reply_text(f"✅ Заявка #{withdrawal_id} отклонена, снежинки возвращены")
            
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
            await update.message.reply_text(f"{result['reason']}")
        
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
                    keyboard_admin = [
                        [InlineKeyboardButton(f"✅ Принять #{withdrawal_id}", callback_data=f"approve_withdrawal_{withdrawal_id}"),
                         InlineKeyboardButton(f"❌ Отклонить #{withdrawal_id}", callback_data=f"reject_withdrawal_{withdrawal_id}")]
                    ]
                    
                    await context.bot.send_message(
                        admin_id,
                        f"⏳ *Новая заявка на вывод звёзд!*\n\n"
                        f"👤 Пользователь: @{update.effective_user.username or user_id}\n"
                        f"💰 Сумма: {amount} ★\n"
                        f"💳 Способ: 📱 Telegram\n"
                        f"📬 Кошелёк: @{user[9]}\n"
                        f"🆔 Заявка: #{withdrawal_id}",
                        parse_mode=ParseMode.MARKDOWN,
                        reply_markup=InlineKeyboardMarkup(keyboard_admin)
                    )
                except Exception as e:
                    logger.error(f"Ошибка отправки уведомления админу: {e}")
            
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
                    keyboard_admin = [
                        [InlineKeyboardButton(f"✅ Принять #{withdrawal_id}", callback_data=f"approve_withdrawal_{withdrawal_id}"),
                         InlineKeyboardButton(f"❌ Отклонить #{withdrawal_id}", callback_data=f"reject_withdrawal_{withdrawal_id}")]
                    ]
                    
                    await context.bot.send_message(
                        admin_id,
                        f"⏳ *Новая заявка на вывод звёзд!*\n\n"
                        f"👤 Пользователь: @{update.effective_user.username or user_id}\n"
                        f"💰 Сумма: {amount} ★\n"
                        f"💳 Способ: 💳 CryptoBot\n"
                        f"📬 Кошелёк: {user[8]}\n"
                        f"🆔 Заявка: #{withdrawal_id}",
                        parse_mode=ParseMode.MARKDOWN,
                        reply_markup=InlineKeyboardMarkup(keyboard_admin)
                    )
                except Exception as e:
                    logger.error(f"Ошибка отправки уведомления админу: {e}")
            
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
                    await context.bot.send_photo(chat_id=u[0], photo=photo, caption=caption, parse_mode=ParseMode.MARKDOWN)
                    sent += 1
                    await asyncio.sleep(0.05)
                except:
                    failed += 1
        else:
            for u in users:
                try:
                    await context.bot.send_message(chat_id=u[0], text=text, parse_mode=ParseMode.MARKDOWN)
                    sent += 1
                    await asyncio.sleep(0.05)
                except:
                    failed += 1
        
        await update.message.reply_text(f"✅ Отправлено: {sent}\n❌ Ошибок: {failed}")

def main():
    print("=" * 60)
    print(f"🚀 ЗАПУСК {BOT_NAME} (ФИНАЛЬНАЯ ВЕРСИЯ)")
    print("=" * 60)
    print("✅ 5 игр в казино с анимациями")
    print("✅ Пагинация пользователей")
    print("✅ Поиск пользователей")
    print("✅ Сортировка")
    print("✅ Причина отказа вывода")
    print("✅ Промокоды")
    print("✅ Вывод NFT по заявкам")
    print("✅ Дневная/недельная/месячная статистика")
    print("✅ Напоминания о бонусе")
    print("✅ Кнопка 'Выведено' для подтверждения")
    print("✅ Постоянное хранение БД (Railway Volume)")
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
