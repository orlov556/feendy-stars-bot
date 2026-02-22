import logging
import random
import sqlite3
import asyncio
import json
import uuid
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes, MessageHandler, filters, PreCheckoutQueryHandler
from telegram.constants import ParseMode
import os
import requests

# ======================== НАСТРОЙКА ========================
TELEGRAM_TOKEN = os.environ.get("BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")  # Токен бота
CRYPTOBOT_API_KEY = os.environ.get("CRYPTOBOT_API_KEY", "")  # API ключ CryptoBot (https://t.me/CryptoBot)
ADMIN_IDS = [5697184715]  # Сюда вставь свой Telegram ID

# Курс обмена
STAR_TO_RUB = 1.3  # 1 звезда = 1.3 рубля
MIN_STARS = 10  # Минимум звёзд для пополнения
MIN_RUB = MIN_STARS * STAR_TO_RUB  # 13 рублей

# ID картинок (замени на свои после загрузки)
MAIN_MENU_IMAGE = 'AgACAgIAAxkBAAIBzGc7pj6l8XQ5Jk5m7N8Q9R2s3LmYAAI13jEUmLvZSF8r9LmN8Q9R2s3LmYAAQAD'
CASE_IMAGE = 'AgACAgIAAxkBAAIBzWc7pj6l8XQ5Jk5m7N8Q9R2s3LmYAAI13jEUmLvZSF8r9LmN8Q9R2s3LmYAAQAD'

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ======================== CRYPTOBOT API ========================

class CryptoBotAPI:
    def __init__(self, api_key, bot_username=None):
        self.api_key = api_key
        self.bot_username = bot_username or "FEENDY_STARS_bot"  # Заглушка, потом обновится
        self.base_url = "https://pay.crypt.bot/api"
        self.headers = {
            "Crypto-Pay-API-Token": api_key,
            "Content-Type": "application/json"
        }
    
    def create_invoice(self, amount_rub, description, payload):
        """Создание счёта в CryptoBot"""
        try:
            data = {
                "asset": "RUB",
                "amount": str(amount_rub),
                "description": description,
                "payload": payload,
                "paid_btn_name": "openBot",
                "paid_btn_url": f"https://t.me/{self.bot_username}"
            }
            
            response = requests.post(
                f"{self.base_url}/createInvoice",
                headers=self.headers,
                json=data
            )
            
            if response.status_code == 200:
                return response.json().get('result')
            else:
                logger.error(f"CryptoBot error: {response.text}")
                return None
        except Exception as e:
            logger.error(f"CryptoBot exception: {e}")
            return None
    
    def get_invoice_status(self, invoice_id):
        """Проверка статуса счёта"""
        try:
            response = requests.get(
                f"{self.base_url}/getInvoices",
                headers=self.headers,
                params={"invoice_ids": invoice_id}
            )
            
            if response.status_code == 200:
                items = response.json().get('result', {}).get('items', [])
                return items[0] if items else None
            return None
        except Exception as e:
            logger.error(f"CryptoBot status error: {e}")
            return None

# Инициализация CryptoBot (пока без username, обновится при старте)
crypto_bot = None
if CRYPTOBOT_API_KEY:
    crypto_bot = CryptoBotAPI(CRYPTOBOT_API_KEY, "FEENDY_STARS_bot")

# ======================== БАЗА ДАННЫХ ========================

class Database:
    def __init__(self):
        self.conn = sqlite3.connect('feendy_stars.db', check_same_thread=False)
        self.cursor = self.conn.cursor()
        self._create_tables()
    
    def _create_tables(self):
        # Таблица пользователей
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                balance REAL DEFAULT 0,
                stars REAL DEFAULT 0,
                snowflakes INTEGER DEFAULT 0,
                referrals INTEGER DEFAULT 0,
                referred_by INTEGER,
                daily_bonus DATE,
                is_admin INTEGER DEFAULT 0,
                is_banned INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                withdrawal_wallet TEXT,
                total_withdrawn REAL DEFAULT 0,
                total_deposited REAL DEFAULT 0
            )
        ''')
        
        # Таблица пополнений
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS deposits (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                amount_stars REAL,
                amount_rub REAL,
                payment_method TEXT,
                payment_id TEXT,
                status TEXT DEFAULT 'pending',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Таблица игр
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
        
        # Таблица кейсов
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS cases (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT,
                price REAL,
                items TEXT
            )
        ''')
        
        # Таблица инвентаря
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
        
        # Таблица заявок на вывод
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS withdrawals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                amount REAL,
                wallet TEXT,
                status TEXT DEFAULT 'pending',
                admin_id INTEGER,
                processed_at TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Таблица настроек бота
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        ''')
        
        self.conn.commit()
        self._init_cases()
        self._init_admin()
        self._init_settings()
    
    def _init_cases(self):
        cases = [
            {
                'name': 'FANTASY BOX',
                'price': 35,
                'items': [
                    {'name': 'Сердце', 'chance': 60, 'value': 15, 'type': 'gift'},
                    {'name': 'Роза', 'chance': 17, 'value': 25, 'type': 'gift'},
                    {'name': 'Ракета', 'chance': 7, 'value': 50, 'type': 'gift'},
                    {'name': 'Цветы', 'chance': 7, 'value': 50, 'type': 'gift'},
                    {'name': 'Кольцо', 'chance': 3, 'value': 100, 'type': 'gift'},
                    {'name': 'Алмаз', 'chance': 1.5, 'value': 100, 'type': 'nft'},
                    {'name': 'Люлом', 'chance': 1, 'value': 325, 'type': 'nft'},
                    {'name': 'Chyn Dogg', 'chance': 1, 'value': 425, 'type': 'nft'}
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
        for admin_id in ADMIN_IDS:
            self.cursor.execute(
                'UPDATE users SET is_admin = 1 WHERE user_id = ?',
                (admin_id,)
            )
        self.conn.commit()
    
    def _init_settings(self):
        settings = {
            'min_withdrawal': '10',
            'withdrawal_fee': '5',
            'case_price_fantasy': '35',
            'house_edge': '10',
            'min_deposit_stars': str(MIN_STARS),
            'star_to_rub': str(STAR_TO_RUB)
        }
        for key, value in settings.items():
            self.cursor.execute(
                'INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)',
                (key, value)
            )
        self.conn.commit()
    
    def get_user(self, user_id):
        self.cursor.execute('SELECT * FROM users WHERE user_id = ?', (user_id,))
        return self.cursor.fetchone()
    
    def create_user(self, user_id, username, first_name, referred_by=None):
        self.cursor.execute('''
            INSERT OR IGNORE INTO users (user_id, username, first_name, referred_by)
            VALUES (?, ?, ?, ?)
        ''', (user_id, username, first_name, referred_by))
        self.conn.commit()
        
        if referred_by:
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
    
    def add_deposit(self, user_id, amount_stars, amount_rub, payment_method, payment_id):
        self.cursor.execute('''
            INSERT INTO deposits (user_id, amount_stars, amount_rub, payment_method, payment_id, status)
            VALUES (?, ?, ?, ?, ?, 'completed')
        ''', (user_id, amount_stars, amount_rub, payment_method, payment_id))
        self.conn.commit()
        
        # Обновляем общую сумму пополнений
        self.cursor.execute('''
            UPDATE users SET total_deposited = total_deposited + ? WHERE user_id = ?
        ''', (amount_stars, user_id))
        self.conn.commit()
        
        return self.cursor.lastrowid
    
    def get_user_deposits(self, user_id):
        self.cursor.execute('''
            SELECT * FROM deposits WHERE user_id = ? ORDER BY created_at DESC
        ''', (user_id,))
        return self.cursor.fetchall()
    
    def get_all_users(self):
        self.cursor.execute('SELECT user_id, username, first_name, balance, stars, created_at, total_deposited FROM users ORDER BY created_at DESC')
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
                    INSERT INTO inventory (user_id, item_name, item_type, item_value, source)
                    VALUES (?, ?, ?, ?, ?)
                ''', (user_id, item['name'], item['type'], item['value'], f"case_{case[1]}"))
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
                'UPDATE users SET daily_bonus = ?, stars = stars + 5, snowflakes = snowflakes + 1 WHERE user_id = ?',
                (today, user_id)
            )
            self.conn.commit()
            return True
        return False
    
    # ================== МЕТОДЫ ДЛЯ ВЫВОДА ==================
    
    def create_withdrawal(self, user_id, amount, wallet):
        self.cursor.execute('''
            INSERT INTO withdrawals (user_id, amount, wallet)
            VALUES (?, ?, ?)
        ''', (user_id, amount, wallet))
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
        withdrawal = self.cursor.fetchone()
        
        if not withdrawal:
            return False
        
        user_id, amount = withdrawal
        
        user = self.get_user(user_id)
        if user[3] < amount:
            return False
        
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
        self.cursor.execute('''
            UPDATE withdrawals 
            SET status = 'rejected', admin_id = ?, processed_at = CURRENT_TIMESTAMP
            WHERE id = ?
        ''', (admin_id, withdrawal_id))
        self.conn.commit()
        return True
    
    def get_user_withdrawals(self, user_id):
        self.cursor.execute('''
            SELECT * FROM withdrawals 
            WHERE user_id = ? 
            ORDER BY created_at DESC
        ''', (user_id,))
        return self.cursor.fetchall()
    
    # ================== МЕТОДЫ ДЛЯ АДМИНА ==================
    
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
    
    def ban_user(self, user_id):
        self.cursor.execute('UPDATE users SET is_banned = 1 WHERE user_id = ?', (user_id,))
        self.conn.commit()
    
    def unban_user(self, user_id):
        self.cursor.execute('UPDATE users SET is_banned = 0 WHERE user_id = ?', (user_id,))
        self.conn.commit()
    
    def get_banned_users(self):
        self.cursor.execute('SELECT user_id, username, first_name FROM users WHERE is_banned = 1')
        return self.cursor.fetchall()
    
    def get_total_stats(self):
        self.cursor.execute('SELECT COUNT(*) FROM users')
        total_users = self.cursor.fetchone()[0]
        
        self.cursor.execute('SELECT SUM(balance) FROM users')
        total_balance = self.cursor.fetchone()[0] or 0
        
        self.cursor.execute('SELECT SUM(total_withdrawn) FROM users')
        total_withdrawn = self.cursor.fetchone()[0] or 0
        
        self.cursor.execute('SELECT SUM(total_deposited) FROM users')
        total_deposited = self.cursor.fetchone()[0] or 0
        
        self.cursor.execute('SELECT COUNT(*) FROM games')
        total_games = self.cursor.fetchone()[0]
        
        self.cursor.execute('SELECT SUM(win) FROM games')
        total_winnings = self.cursor.fetchone()[0] or 0
        
        self.cursor.execute('SELECT SUM(bet) FROM games')
        total_bets = self.cursor.fetchone()[0] or 0
        
        return {
            'total_users': total_users,
            'total_balance': total_balance,
            'total_withdrawn': total_withdrawn,
            'total_deposited': total_deposited,
            'total_games': total_games,
            'total_winnings': total_winnings,
            'total_bets': total_bets,
            'profit': total_bets - total_winnings
        }
    
    def close(self):
        self.conn.close()


# ======================== БОТ ========================

db = Database()
BOT_NAME = "FEENDY STARS"

# Шансы игр (в пользу казино)
GAME_ODDS = {
    'flip': {'win_chance': 45, 'multiplier': 1.7},
    'roulette': {'win_chance': 20, 'multiplier': 4.5},
    'wheel': {'win_chance': 8, 'multiplier': 10},
    'mines': {'win_chance': 12, 'multiplier': 7.5},
    'dice': {'win_chance': 30, 'multiplier': 2.5},
    'slots': {'win_chance': 25, 'multiplier': 3.0}
}

async def check_ban(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user = db.get_user(user_id)
    if user and user[10] == 1:
        await update.message.reply_text("❌ Вы заблокированы в этом боте.")
        return False
    return True

async def check_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user = db.get_user(user_id)
    return user and user[9] == 1

async def update_bot_username(application):
    """Обновляет username бота в CryptoBot при старте"""
    global crypto_bot
    if crypto_bot:
        me = await application.bot.get_me()
        crypto_bot.bot_username = me.username
        logger.info(f"✅ Бот: @{me.username}, CryptoBot обновлён")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_ban(update, context):
        return
    
    user = update.effective_user
    referred_by = None
    
    if context.args and context.args[0].startswith('ref'):
        try:
            referred_by = int(context.args[0].replace('ref', ''))
        except:
            pass
    
    db.create_user(user.id, user.username, user.first_name, referred_by)
    user_data = db.get_user(user.id)
    
    keyboard = [
        [InlineKeyboardButton("🎰 Казино", callback_data="casino_menu")],
        [InlineKeyboardButton("📦 Кейс", callback_data="case_menu")],
        [InlineKeyboardButton("🎁 Ежедневный бонус", callback_data="daily_bonus")],
        [InlineKeyboardButton("👥 Реф ссылка", callback_data="referral")],
        [InlineKeyboardButton("👤 Профиль", callback_data="profile")],
        [InlineKeyboardButton("💸 Вывод", callback_data="withdraw_menu")],
        [InlineKeyboardButton("📜 Правила", callback_data="rules")]
    ]
    
    if user_data and user_data[9] == 1:
        keyboard.append([InlineKeyboardButton("⚙️ Админ-панель", callback_data="admin_panel")])
    
    welcome_text = (
        f"🌟 *Добро пожаловать в {BOT_NAME}!*\n\n"
        f"👤 *ID:* {user.id}\n"
        f"📝 *Имя:* {user.first_name}\n"
        f"💰 *Баланс:* {user_data[3]} ★\n"
        f"❄️ *Снежинки:* {user_data[5]} ✨\n\n"
        f"Выберите действие:"
    )
    
    if MAIN_MENU_IMAGE:
        await update.message.reply_photo(
            photo=MAIN_MENU_IMAGE,
            caption=welcome_text,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    else:
        await update.message.reply_text(
            welcome_text,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    user_id = update.effective_user.id
    user = db.get_user(user_id)
    
    if not user:
        await query.edit_message_text("❌ Ошибка: пользователь не найден")
        return
    
    if user[10] == 1:
        await query.edit_message_text("❌ Вы заблокированы в этом боте.")
        return
    
    data = query.data
    
    # ================== ПРОФИЛЬ ==================
    
    if data == "profile":
        stats = db.get_user_stats(user_id)
        withdrawals = db.get_user_withdrawals(user_id)
        deposits = db.get_user_deposits(user_id)
        
        total_withdrawn = sum(w[2] for w in withdrawals if w[3] == 'approved')
        total_deposited = sum(d[2] for d in deposits)
        
        text = (
            f"👤 *Ваш профиль*\n\n"
            f"🆔 *ID:* {user_id}\n"
            f"👤 *Имя:* {update.effective_user.first_name}\n"
            f"📛 *Username:* @{update.effective_user.username or 'нет'}\n"
            f"💰 *Баланс:* {user[3]} ★\n"
            f"❄️ *Снежинки:* {user[5]} ✨\n"
            f"💸 *Всего выведено:* {total_withdrawn} ★\n"
            f"💳 *Всего пополнено:* {total_deposited} ★\n"
            f"👥 *Рефералов:* {user[6]}\n\n"
            f"📊 *Статистика игр:*\n"
            f"• Всего игр: {stats[0] if stats else 0}\n"
            f"• Выиграно: {stats[1] if stats else 0}\n"
            f"• Проиграно: {stats[2] if stats else 0}\n"
            f"• Сумма ставок: {stats[3] if stats else 0} ★\n"
            f"• Сумма выигрышей: {stats[4] if stats else 0} ★"
        )
        
        keyboard = [
            [InlineKeyboardButton("💳 Пополнить баланс", callback_data="deposit_menu")],
            [InlineKeyboardButton("◀️ Главное меню", callback_data="main_menu")]
        ]
        await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(keyboard))
    
    # ================== ПОПОЛНЕНИЕ ==================
    
    elif data == "deposit_menu":
        text = (
            f"💳 *Пополнение баланса*\n\n"
            f"💰 Ваш баланс: {user[3]} ★\n\n"
            f"Выберите способ пополнения:\n\n"
            f"🎫 *Telegram Stars* — мгновенно\n"
            f"• 1 ★ = 1 рубль\n"
            f"• Минимум: {MIN_STARS} ★\n\n"
            f"💎 *CryptoBot* — рубли\n"
            f"• Курс: 1 ★ = {STAR_TO_RUB} руб\n"
            f"• Минимум: {MIN_RUB} руб"
        )
        
        keyboard = [
            [InlineKeyboardButton("🎫 Пополнить Stars'ами", callback_data="deposit_stars_menu")],
            [InlineKeyboardButton("💎 Пополнить CryptoBot", callback_data="deposit_crypto_menu")],
            [InlineKeyboardButton("◀️ Назад в профиль", callback_data="profile")]
        ]
        
        await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(keyboard))
    
    elif data == "deposit_stars_menu":
        text = (
            f"🎫 *Пополнение Telegram Stars*\n\n"
            f"💰 Ваш баланс: {user[3]} ★\n\n"
            f"Выберите сумму пополнения:\n"
            f"Минимальная сумма: {MIN_STARS} ★"
        )
        
        keyboard = [
            [InlineKeyboardButton("🔟 10 ★", callback_data="stars_10"),
             InlineKeyboardButton("2️⃣5️⃣ 25 ★", callback_data="stars_25"),
             InlineKeyboardButton("5️⃣0️⃣ 50 ★", callback_data="stars_50")],
            [InlineKeyboardButton("1️⃣0️⃣0️⃣ 100 ★", callback_data="stars_100"),
             InlineKeyboardButton("2️⃣5️⃣0️⃣ 250 ★", callback_data="stars_250"),
             InlineKeyboardButton("5️⃣0️⃣0️⃣ 500 ★", callback_data="stars_500")],
            [InlineKeyboardButton("◀️ Назад", callback_data="deposit_menu")]
        ]
        
        await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(keyboard))
    
    elif data.startswith("stars_"):
        stars = int(data.replace("stars_", ""))
        
        context.user_data['deposit_stars'] = stars
        
        # Создаём счёт в Telegram Stars
        prices = [{"amount": stars * 100, "label": f"Пополнение на {stars} ★"}]
        
        await context.bot.send_invoice(
            chat_id=user_id,
            title=f"Пополнение баланса {BOT_NAME}",
            description=f"Пополнение на {stars} ★",
            payload=f"deposit_stars_{stars}_{user_id}",
            provider_token="",  # Пусто для Stars
            currency="XTR",  # Специальная валюта для Stars
            prices=prices,
            start_parameter="deposit",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("💳 Оплатить", pay=True)
            ]])
        )
    
    elif data == "deposit_crypto_menu":
        if not crypto_bot:
            await query.edit_message_text(
                "❌ CryptoBot не настроен. Обратитесь к администратору.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Назад", callback_data="deposit_menu")]])
            )
            return
        
        text = (
            f"💎 *Пополнение через CryptoBot*\n\n"
            f"💰 Ваш баланс: {user[3]} ★\n"
            f"💱 Курс: 1 ★ = {STAR_TO_RUB} руб\n"
            f"Минимальная сумма: {MIN_RUB} руб\n\n"
            f"Введите сумму в рублях (число):"
        )
        
        context.user_data['awaiting'] = 'crypto_amount'
        await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN)
    
    # ================== ОСТАЛЬНЫЕ РАЗДЕЛЫ ==================
    
    elif data == "daily_bonus":
        if db.check_daily_bonus(user_id):
            text = "🎁 *Ежедневный бонус получен!*\n\n+5 ★ звёзд\n+1 ✨ снежинка"
        else:
            text = "❌ Вы уже получали бонус сегодня. Приходите завтра!"
        
        keyboard = [[InlineKeyboardButton("◀️ Главное меню", callback_data="main_menu")]]
        await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(keyboard))
    
    elif data == "referral":
        me = await context.bot.get_me()
        ref_link = f"https://t.me/{me.username}?start=ref{user_id}"
        
        text = (
            f"👥 *Реферальная программа*\n\n"
            f"Приглашайте друзей и зарабатывайте!\n\n"
            f"📊 *Статистика:*\n"
            f"• Приглашено: {user[6]} друзей\n"
            f"• Заработано: {user[5]} ✨ снежинок\n\n"
            f"🎁 *Бонусы:*\n"
            f"• +5 ✨ за каждого друга\n"
            f"• 35% с прибыли от их покупок\n\n"
            f"🔗 *Ваша ссылка:*\n`{ref_link}`"
        )
        
        keyboard = [[InlineKeyboardButton("◀️ Главное меню", callback_data="main_menu")]]
        await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(keyboard))
    
    elif data == "casino_menu":
        text = (
            f"🎰 *Казино {BOT_NAME}*\n\n"
            f"Выберите игру, на которую хотите сделать ставку\n\n"
            f"В скобках указан максимальный множитель ставки в игре"
        )
        
        keyboard = [
            [InlineKeyboardButton("🎲 Орёл и решка (x1.7)", callback_data="game_flip_menu")],
            [InlineKeyboardButton("💀 Русская рулетка (x4.5)", callback_data="game_roulette_menu")],
            [InlineKeyboardButton("🎡 Колесо удачи (x10)", callback_data="game_wheel_menu")],
            [InlineKeyboardButton("💣 Минное поле (x7.5)", callback_data="game_mines_menu")],
            [InlineKeyboardButton("🎲 Кости (x2.5)", callback_data="game_dice_menu")],
            [InlineKeyboardButton("🎰 Слоты (x3.0)", callback_data="game_slots_menu")],
            [InlineKeyboardButton("◀️ Главное меню", callback_data="main_menu")]
        ]
        
        await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(keyboard))
    
    # Универсальная функция для меню игры
    async def show_game_menu(game_key, game_name):
        context.user_data['game'] = game_key
        
        text = (
            f"🎮 *{game_name}*\n\n"
            f"💰 Ваш баланс: {user[3]} ★\n\n"
            f"Выберите сумму ставки:"
        )
        
        keyboard = [
            [InlineKeyboardButton("🔟 10 ★", callback_data="bet_10"),
             InlineKeyboardButton("2️⃣5️⃣ 25 ★", callback_data="bet_25"),
             InlineKeyboardButton("5️⃣0️⃣ 50 ★", callback_data="bet_50")],
            [InlineKeyboardButton("1️⃣0️⃣0️⃣ 100 ★", callback_data="bet_100"),
             InlineKeyboardButton("2️⃣5️⃣0️⃣ 250 ★", callback_data="bet_250"),
             InlineKeyboardButton("5️⃣0️⃣0️⃣ 500 ★", callback_data="bet_500")],
            [InlineKeyboardButton("◀️ Назад в казино", callback_data="casino_menu")]
        ]
        
        await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(keyboard))
    
    if data == "game_flip_menu":
        await show_game_menu('flip', '🎲 Орёл и решка')
    elif data == "game_roulette_menu":
        await show_game_menu('roulette', '💀 Русская рулетка')
    elif data == "game_wheel_menu":
        await show_game_menu('wheel', '🎡 Колесо удачи')
    elif data == "game_mines_menu":
        await show_game_menu('mines', '💣 Минное поле')
    elif data == "game_dice_menu":
        await show_game_menu('dice', '🎲 Кости')
    elif data == "game_slots_menu":
        await show_game_menu('slots', '🎰 Слоты')
    
    elif data.startswith("bet_"):
        bet = int(data.replace("bet_", ""))
        game = context.user_data.get('game', 'flip')
        
        if bet > user[3]:
            await query.edit_message_text(
                "❌ Недостаточно средств!",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Назад", callback_data="casino_menu")]])
            )
            return
        
        context.user_data['bet'] = bet
        game_names = {
            'flip': '🎲 Орёл и решка',
            'roulette': '💀 Русская рулетка',
            'wheel': '🎡 Колесо удачи',
            'mines': '💣 Минное поле',
            'dice': '🎲 Кости',
            'slots': '🎰 Слоты'
        }
        
        text = (
            f"{game_names.get(game, '🎮 Игра')}\n\n"
            f"💰 Ставка: {bet} ★\n\n"
            f"Нажмите кнопку для игры:"
        )
        
        keyboard = [
            [InlineKeyboardButton("🎮 Играть", callback_data=f"play_game")],
            [InlineKeyboardButton("◀️ Назад", callback_data="casino_menu")]
        ]
        
        await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(keyboard))
    
    elif data == "play_game":
        bet = context.user_data.get('bet', 10)
        game = context.user_data.get('game', 'flip')
        
        db.update_balance(user_id, -bet)
        
        odds = GAME_ODDS.get(game, GAME_ODDS['flip'])
        win_chance = odds['win_chance']
        multiplier = odds['multiplier']
        
        roll = random.randint(1, 100)
        win = roll <= win_chance
        
        game_names = {
            'flip': '🎲 Орёл и решка',
            'roulette': '💀 Русская рулетка',
            'wheel': '🎡 Колесо удачи',
            'mines': '💣 Минное поле',
            'dice': '🎲 Кости',
            'slots': '🎰 Слоты'
        }
        
        if win:
            win_amount = bet * multiplier
            db.update_balance(user_id, win_amount)
            db.add_game(user_id, game, bet, multiplier, win_amount, 'win')
            
            text = (
                f"🎉 *ВЫ ВЫИГРАЛИ!*\n\n"
                f"📊 *Игра:* {game_names.get(game, '🎮')}\n"
                f"💰 *Ставка:* {bet} ★\n"
                f"📈 *Множитель:* x{multiplier}\n"
                f"💎 *Выигрыш:* {win_amount:.1f} ★"
            )
        else:
            db.add_game(user_id, game, bet, 0, 0, 'lose')
            
            text = (
                f"😢 *ВЫ ПРОИГРАЛИ*\n\n"
                f"📊 *Игра:* {game_names.get(game, '🎮')}\n"
                f"💰 *Ставка:* {bet} ★ проиграна\n\n"
                f"🍀 Повезёт в следующий раз!"
            )
        
        keyboard = [
            [InlineKeyboardButton("🎮 Играть ещё", callback_data=f"{game}_menu")],
            [InlineKeyboardButton("◀️ Главное меню", callback_data="main_menu")]
        ]
        
        await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(keyboard))
        context.user_data.pop('bet', None)
    
    elif data == "case_menu":
        cases = db.get_cases()
        
        text = f"📦 *Кейсы {BOT_NAME}*\n\n"
        keyboard = []
        
        for case in cases:
            text += f"• *{case[1]}* — {case[2]} ★\n"
            keyboard.append([InlineKeyboardButton(f"📦 Открыть {case[1]} ({case[2]} ★)", callback_data=f"open_case_{case[0]}")])
        
        keyboard.append([InlineKeyboardButton("◀️ Главное меню", callback_data="main_menu")])
        
        if CASE_IMAGE and query.message.photo:
            await query.edit_message_caption(
                caption=text,
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        else:
            await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(keyboard))
    
    elif data.startswith("open_case_"):
        case_id = int(data.replace("open_case_", ""))
        case_price = 35
        
        if user[3] < case_price:
            await query.edit_message_text(
                "❌ Недостаточно средств!",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Назад", callback_data="case_menu")]])
            )
            return
        
        db.update_balance(user_id, -case_price)
        result = db.open_case(case_id, user_id)
        
        if result:
            text = (
                f"🎉 *Поздравляем!*\n\n"
                f"Вы выиграли: *{result['name']}*\n"
                f"📊 Редкость: {result['chance']}%\n"
                f"📦 Тип: {'🎁 Подарок' if result['type'] == 'gift' else '💎 NFT'}\n\n"
                f"Предмет добавлен в инвентарь!"
            )
        else:
            text = "❌ Ошибка открытия кейса"
        
        keyboard = [
            [InlineKeyboardButton("📦 Ещё кейс", callback_data="case_menu")],
            [InlineKeyboardButton("◀️ Главное меню", callback_data="main_menu")]
        ]
        
        await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(keyboard))
    
    elif data == "withdraw_menu":
        text = (
            f"💸 *Вывод средств*\n\n"
            f"💰 *Баланс:* {user[3]} ★\n"
            f"💳 *Кошелёк:* {user[12] or 'не указан'}\n\n"
            f"Минимальная сумма: {db.get_setting('min_withdrawal')} ★\n"
            f"Комиссия: {db.get_setting('withdrawal_fee')}%"
        )
        
        keyboard = [
            [InlineKeyboardButton("💳 Указать кошелёк", callback_data="set_wallet")],
            [InlineKeyboardButton("💰 Создать заявку", callback_data="create_withdrawal")],
            [InlineKeyboardButton("📋 История выводов", callback_data="withdrawal_history")],
            [InlineKeyboardButton("◀️ Главное меню", callback_data="main_menu")]
        ]
        
        await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(keyboard))
    
    elif data == "set_wallet":
        context.user_data['awaiting'] = 'wallet'
        await query.edit_message_text(
            "💳 *Укажите кошелёк для вывода*\n\n"
            "Отправьте номер кошелька (например, адрес TON кошелька):",
            parse_mode=ParseMode.MARKDOWN
        )
    
    elif data == "create_withdrawal":
        min_withdrawal = float(db.get_setting('min_withdrawal'))
        
        if user[3] < min_withdrawal:
            await query.edit_message_text(
                f"❌ Минимальная сумма вывода — {min_withdrawal} ★",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Назад", callback_data="withdraw_menu")]])
            )
            return
        
        if not user[12]:
            await query.edit_message_text(
                "❌ Сначала укажите кошелёк для вывода",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("💳 Указать кошелёк", callback_data="set_wallet")]])
            )
            return
        
        context.user_data['awaiting'] = 'withdrawal_amount'
        await query.edit_message_text(
            f"💰 *Создание заявки на вывод*\n\n"
            f"Ваш баланс: {user[3]} ★\n"
            f"Кошелёк: {user[12]}\n\n"
            f"Введите сумму для вывода (мин. {min_withdrawal} ★):",
            parse_mode=ParseMode.MARKDOWN
        )
    
    elif data == "withdrawal_history":
        withdrawals = db.get_user_withdrawals(user_id)
        
        if not withdrawals:
            text = "📋 *История выводов*\n\nУ вас пока нет заявок на вывод"
        else:
            text = "📋 *История выводов*\n\n"
            for w in withdrawals[:10]:
                status_emoji = {
                    'pending': '⏳',
                    'approved': '✅',
                    'rejected': '❌'
                }.get(w[3], '❓')
                
                text += (
                    f"{status_emoji} *{w[2]} ★* — {w[3]}\n"
                    f"🕐 {w[6][:16]}\n\n"
                )
        
        keyboard = [[InlineKeyboardButton("◀️ Назад", callback_data="withdraw_menu")]]
        await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(keyboard))
    
    # ================== АДМИН-ПАНЕЛЬ ==================
    
    elif data == "admin_panel":
        if not await check_admin(update, context):
            await query.edit_message_text("❌ У вас нет прав администратора")
            return
        
        stats = db.get_total_stats()
        active_users = db.get_active_users_count()
        
        text = (
            f"⚙️ *Админ-панель {BOT_NAME}*\n\n"
            f"📊 *Общая статистика:*\n"
            f"• Всего пользователей: {stats['total_users']}\n"
            f"• Активных (7 дней): {active_users}\n"
            f"• Общий баланс: {stats['total_balance']:.2f} ★\n"
            f"• Пополнено всего: {stats['total_deposited']:.2f} ★\n"
            f"• Выведено всего: {stats['total_withdrawn']:.2f} ★\n"
            f"• Всего игр: {stats['total_games']}\n"
            f"• Прибыль бота: {stats['profit']:.2f} ★\n\n"
            f"⏳ *Ожидающих выводов:* {len(db.get_pending_withdrawals())}"
        )
        
        keyboard = [
            [InlineKeyboardButton("👥 Все пользователи", callback_data="admin_users")],
            [InlineKeyboardButton("⏳ Заявки на вывод", callback_data="admin_withdrawals")],
            [InlineKeyboardButton("🔨 Управление банами", callback_data="admin_bans")],
            [InlineKeyboardButton("📢 Рассылка", callback_data="admin_broadcast")],
            [InlineKeyboardButton("⚙️ Настройки", callback_data="admin_settings")],
            [InlineKeyboardButton("🖼️ Загрузить картинки", callback_data="admin_images")],
            [InlineKeyboardButton("◀️ Главное меню", callback_data="main_menu")]
        ]
        
        await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(keyboard))
    
    elif data == "admin_broadcast":
        if not await check_admin(update, context):
            return
        
        context.user_data['awaiting'] = 'broadcast'
        await query.edit_message_text(
            "📢 *Создание рассылки*\n\n"
            "Отправьте сообщение (можно с фото), которое хотите разослать всем пользователям:",
            parse_mode=ParseMode.MARKDOWN
        )
    
    elif data == "admin_images":
        if not await check_admin(update, context):
            return
        
        text = (
            "🖼️ *Загрузка картинок*\n\n"
            "Чтобы установить картинку для главного меню, отправьте фото с подписью:\n"
            "`main_menu`\n\n"
            "Чтобы установить картинку для кейса, отправьте фото с подписью:\n"
            "`case_image`"
        )
        
        keyboard = [[InlineKeyboardButton("◀️ Назад", callback_data="admin_panel")]]
        await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(keyboard))
    
    elif data == "admin_withdrawals":
        if not await check_admin(update, context):
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
            text += (
                f"🆔 *Заявка #{w[0]}*\n"
                f"👤 Пользователь: {w[8]} (@{w[7]})\n"
                f"💰 Сумма: {w[2]} ★\n"
                f"💳 Кошелёк: {w[3]}\n"
                f"🕐 Создана: {w[6][:16]}\n\n"
            )
            keyboard.append([
                InlineKeyboardButton(f"✅ Одобрить #{w[0]}", callback_data=f"approve_withdrawal_{w[0]}"),
                InlineKeyboardButton(f"❌ Отклонить #{w[0]}", callback_data=f"reject_withdrawal_{w[0]}")
            ])
        
        keyboard.append([InlineKeyboardButton("◀️ Назад", callback_data="admin_panel")])
        
        await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(keyboard))
    
    elif data.startswith("approve_withdrawal_"):
        if not await check_admin(update, context):
            return
        
        withdrawal_id = int(data.replace("approve_withdrawal_", ""))
        
        if db.approve_withdrawal(withdrawal_id, user_id):
            await query.edit_message_text("✅ Заявка одобрена")
            
            db.cursor.execute('SELECT user_id, amount FROM withdrawals WHERE id = ?', (withdrawal_id,))
            w_user_id, amount = db.cursor.fetchone()
            try:
                await context.bot.send_message(
                    w_user_id,
                    f"✅ *Заявка на вывод одобрена!*\n\n"
                    f"💰 Сумма: {amount} ★\n"
                    f"Средства отправлены на ваш кошелёк.",
                    parse_mode=ParseMode.MARKDOWN
                )
            except:
                pass
        else:
            await query.edit_message_text("❌ Ошибка: недостаточно средств")
    
    elif data.startswith("reject_withdrawal_"):
        if not await check_admin(update, context):
            return
        
        withdrawal_id = int(data.replace("reject_withdrawal_", ""))
        
        if db.reject_withdrawal(withdrawal_id, user_id):
            await query.edit_message_text("❌ Заявка отклонена")
            
            db.cursor.execute('SELECT user_id, amount FROM withdrawals WHERE id = ?', (withdrawal_id,))
            w_user_id, amount = db.cursor.fetchone()
            try:
                await context.bot.send_message(
                    w_user_id,
                    f"❌ *Заявка на вывод отклонена*\n\n"
                    f"💰 Сумма: {amount} ★\n"
                    f"Причина: проверьте правильность кошелька.",
                    parse_mode=ParseMode.MARKDOWN
                )
            except:
                pass
        else:
            await query.edit_message_text("❌ Ошибка при отклонении заявки")
    
    elif data == "admin_users":
        if not await check_admin(update, context):
            return
        
        users = db.get_all_users()
        text = f"👥 *Всего пользователей: {len(users)}*\n\n"
        
        for u in users[:20]:
            text += f"• {u[2]} (@{u[1]}) — баланс: {u[3]} ★, пополнено: {u[6]} ★\n"
        
        if len(users) > 20:
            text += f"\n...и ещё {len(users)-20} пользователей"
        
        keyboard = [[InlineKeyboardButton("◀️ Назад", callback_data="admin_panel")]]
        await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(keyboard))
    
    elif data == "admin_bans":
        if not await check_admin(update, context):
            return
        
        banned = db.get_banned_users()
        
        text = "🔨 *Забаненные пользователи*\n\n"
        keyboard = []
        
        if banned:
            for b in banned[:5]:
                text += f"• {b[2]} (@{b[1]}) — ID: {b[0]}\n"
                keyboard.append([InlineKeyboardButton(f"✅ Разбанить {b[0]}", callback_data=f"unban_{b[0]}")])
        else:
            text += "Нет забаненных пользователей"
        
        keyboard.append([InlineKeyboardButton("◀️ Назад", callback_data="admin_panel")])
        await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(keyboard))
    
    elif data.startswith("unban_"):
        if not await check_admin(update, context):
            return
        
        ban_user_id = int(data.replace("unban_", ""))
        db.unban_user(ban_user_id)
        await query.edit_message_text(f"✅ Пользователь {ban_user_id} разбанен")
    
    elif data == "admin_settings":
        if not await check_admin(update, context):
            return
        
        min_withdrawal = db.get_setting('min_withdrawal', '10')
        withdrawal_fee = db.get_setting('withdrawal_fee', '5')
        house_edge = db.get_setting('house_edge', '10')
        min_deposit = db.get_setting('min_deposit_stars', str(MIN_STARS))
        star_rate = db.get_setting('star_to_rub', str(STAR_TO_RUB))
        
        text = (
            f"⚙️ *Настройки бота*\n\n"
            f"💰 Мин. сумма вывода: {min_withdrawal} ★\n"
            f"💸 Комиссия на вывод: {withdrawal_fee}%\n"
            f"🎰 Преимущество казино: {house_edge}%\n"
            f"💳 Мин. пополнение: {min_deposit} ★\n"
            f"💱 Курс Stars: 1 ★ = {star_rate} руб\n\n"
            f"Выберите параметр для изменения:"
        )
        
        keyboard = [
            [InlineKeyboardButton("💰 Мин. сумма вывода", callback_data="edit_min_withdrawal")],
            [InlineKeyboardButton("💸 Комиссия на вывод", callback_data="edit_withdrawal_fee")],
            [InlineKeyboardButton("🎰 Преимущество казино", callback_data="edit_house_edge")],
            [InlineKeyboardButton("💳 Мин. пополнение", callback_data="edit_min_deposit")],
            [InlineKeyboardButton("💱 Курс Stars", callback_data="edit_star_rate")],
            [InlineKeyboardButton("◀️ Назад", callback_data="admin_panel")]
        ]
        
        await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(keyboard))
    
    elif data == "edit_min_withdrawal":
        if not await check_admin(update, context):
            return
        
        context.user_data['awaiting'] = 'edit_min_withdrawal'
        await query.edit_message_text(
            "💰 Введите новую минимальную сумму вывода:",
            parse_mode=ParseMode.MARKDOWN
        )
    
    elif data == "edit_withdrawal_fee":
        if not await check_admin(update, context):
            return
        
        context.user_data['awaiting'] = 'edit_withdrawal_fee'
        await query.edit_message_text(
            "💸 Введите новую комиссию на вывод (в %):",
            parse_mode=ParseMode.MARKDOWN
        )
    
    elif data == "edit_house_edge":
        if not await check_admin(update, context):
            return
        
        context.user_data['awaiting'] = 'edit_house_edge'
        await query.edit_message_text(
            "🎰 Введите новое преимущество казино (в %, от 1 до 50):",
            parse_mode=ParseMode.MARKDOWN
        )
    
    elif data == "edit_min_deposit":
        if not await check_admin(update, context):
            return
        
        context.user_data['awaiting'] = 'edit_min_deposit'
        await query.edit_message_text(
            "💳 Введите новую минимальную сумму пополнения в звёздах:",
            parse_mode=ParseMode.MARKDOWN
        )
    
    elif data == "edit_star_rate":
        if not await check_admin(update, context):
            return
        
        context.user_data['awaiting'] = 'edit_star_rate'
        await query.edit_message_text(
            "💱 Введите новый курс (сколько рублей стоит 1 звезда):",
            parse_mode=ParseMode.MARKDOWN
        )
    
    elif data == "rules":
        text = (
            f"📜 *Правила {BOT_NAME}*\n\n"
            f"1. Минимальная сумма вывода — {db.get_setting('min_withdrawal')} ★\n"
            f"2. Комиссия на вывод — {db.get_setting('withdrawal_fee')}%\n"
            f"3. За рефералов начисляется +5 ✨\n"
            f"4. Ежедневный бонус доступен раз в сутки\n"
            f"5. Минимальное пополнение — {db.get_setting('min_deposit_stars')} ★\n"
            f"6. Курс Stars: 1 ★ = {db.get_setting('star_to_rub')} руб\n"
            f"7. Администрация имеет право заблокировать пользователя в случае нарушения правил\n\n"
            f"🍀 Удачи в игре!"
        )
        
        keyboard = [[InlineKeyboardButton("◀️ Главное меню", callback_data="main_menu")]]
        await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(keyboard))
    
    elif data == "main_menu":
        keyboard = [
            [InlineKeyboardButton("🎰 Казино", callback_data="casino_menu")],
            [InlineKeyboardButton("📦 Кейс", callback_data="case_menu")],
            [InlineKeyboardButton("🎁 Ежедневный бонус", callback_data="daily_bonus")],
            [InlineKeyboardButton("👥 Реф ссылка", callback_data="referral")],
            [InlineKeyboardButton("👤 Профиль", callback_data="profile")],
            [InlineKeyboardButton("💸 Вывод", callback_data="withdraw_menu")],
            [InlineKeyboardButton("📜 Правила", callback_data="rules")]
        ]
        
        if user and user[9] == 1:
            keyboard.append([InlineKeyboardButton("⚙️ Админ-панель", callback_data="admin_panel")])
        
        text = (
            f"🌟 *{BOT_NAME}*\n\n"
            f"👤 *ID:* {user_id}\n"
            f"📝 *Имя:* {update.effective_user.first_name}\n"
            f"💰 *Баланс:* {user[3]} ★\n"
            f"❄️ *Снежинки:* {user[5]} ✨\n\n"
            f"Выберите действие:"
        )
        
        await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(keyboard))

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_ban(update, context):
        return
    
    user_id = update.effective_user.id
    text = update.message.text
    
    if 'awaiting' not in context.user_data:
        return
    
    state = context.user_data['awaiting']
    
    if state == 'wallet':
        db.cursor.execute('UPDATE users SET withdrawal_wallet = ? WHERE user_id = ?', (text, user_id))
        db.conn.commit()
        context.user_data.pop('awaiting')
        await update.message.reply_text(
            "✅ Кошелёк сохранён!",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Назад к выводу", callback_data="withdraw_menu")]])
        )
    
    elif state == 'withdrawal_amount':
        try:
            amount = float(text)
            user = db.get_user(user_id)
            min_withdrawal = float(db.get_setting('min_withdrawal'))
            
            if amount < min_withdrawal:
                await update.message.reply_text(f"❌ Минимальная сумма вывода — {min_withdrawal} ★")
                return
            
            if amount > user[3]:
                await update.message.reply_text("❌ Недостаточно средств")
                return
            
            withdrawal_id = db.create_withdrawal(user_id, amount, user[12])
            
            context.user_data.pop('awaiting')
            await update.message.reply_text(
                f"✅ *Заявка на вывод создана!*\n\n"
                f"💰 Сумма: {amount} ★\n"
                f"🆔 Номер заявки: #{withdrawal_id}\n\n"
                f"Ожидайте подтверждения администратора.",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Назад к выводу", callback_data="withdraw_menu")]])
            )
            
            for admin_id in ADMIN_IDS:
                try:
                    await context.bot.send_message(
                        admin_id,
                        f"⏳ *Новая заявка на вывод*\n\n"
                        f"👤 Пользователь: @{update.effective_user.username or user_id}\n"
                        f"💰 Сумма: {amount} ★\n"
                        f"🆔 Заявка #{withdrawal_id}",
                        parse_mode=ParseMode.MARKDOWN
                    )
                except:
                    pass
            
        except ValueError:
            await update.message.reply_text("❌ Введите число")
    
    elif state == 'crypto_amount':
        try:
            amount_rub = float(text)
            min_rub = float(db.get_setting('min_deposit_stars')) * float(db.get_setting('star_to_rub'))
            
            if amount_rub < min_rub:
                await update.message.reply_text(f"❌ Минимальная сумма — {min_rub:.0f} руб")
                return
            
            # Конвертируем в звёзды
            star_rate = float(db.get_setting('star_to_rub'))
            amount_stars = amount_rub / star_rate
            
            # Создаём счёт в CryptoBot
            invoice = crypto_bot.create_invoice(
                amount_rub=amount_rub,
                description=f"Пополнение баланса {BOT_NAME} на {amount_stars:.0f} ★",
                payload=f"crypto_{amount_stars}_{user_id}"
            )
            
            if invoice and invoice.get('pay_url'):
                context.user_data.pop('awaiting')
                
                await update.message.reply_text(
                    f"💎 *Счёт создан!*\n\n"
                    f"Сумма: {amount_rub} руб\n"
                    f"Вы получите: {amount_stars:.0f} ★\n\n"
                    f"Нажмите кнопку ниже для оплаты:",
                    parse_mode=ParseMode.MARKDOWN,
                    reply_markup=InlineKeyboardMarkup([[
                        InlineKeyboardButton("💳 Оплатить", url=invoice['pay_url'])
                    ]])
                )
                
                # Сохраняем информацию о платеже (в реальном проекте нужно обрабатывать вебхуки)
                db.add_deposit(user_id, amount_stars, amount_rub, 'cryptobot', str(invoice['invoice_id']))
                
            else:
                await update.message.reply_text("❌ Ошибка создания счёта. Попробуйте позже.")
                
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
    
    elif state == 'edit_min_withdrawal':
        if user_id not in ADMIN_IDS:
            return
        
        try:
            value = float(text)
            db.set_setting('min_withdrawal', str(value))
            context.user_data.pop('awaiting')
            await update.message.reply_text(f"✅ Минимальная сумма вывода изменена на {value} ★")
        except ValueError:
            await update.message.reply_text("❌ Введите число")
    
    elif state == 'edit_withdrawal_fee':
        if user_id not in ADMIN_IDS:
            return
        
        try:
            value = float(text)
            if value < 0 or value > 100:
                await update.message.reply_text("❌ Комиссия должна быть от 0 до 100")
                return
            db.set_setting('withdrawal_fee', str(value))
            context.user_data.pop('awaiting')
            await update.message.reply_text(f"✅ Комиссия на вывод изменена на {value}%")
        except ValueError:
            await update.message.reply_text("❌ Введите число")
    
    elif state == 'edit_house_edge':
        if user_id not in ADMIN_IDS:
            return
        
        try:
            value = float(text)
            if value < 1 or value > 50:
                await update.message.reply_text("❌ Преимущество должно быть от 1 до 50%")
                return
            
            db.set_setting('house_edge', str(value))
            
            # Обновляем шансы игр
            global GAME_ODDS
            base_chances = {
                'flip': 50,
                'roulette': 25,
                'wheel': 10,
                'mines': 15,
                'dice': 35,
                'slots': 30
            }
            
            for game, base_chance in base_chances.items():
                new_chance = base_chance * (100 - value) / 100
                GAME_ODDS[game]['win_chance'] = round(new_chance, 1)
            
            context.user_data.pop('awaiting')
            await update.message.reply_text(f"✅ Преимущество казино изменено на {value}%")
        except ValueError:
            await update.message.reply_text("❌ Введите число")
    
    elif state == 'edit_min_deposit':
        if user_id not in ADMIN_IDS:
            return
        
        try:
            value = int(text)
            if value < 1:
                await update.message.reply_text("❌ Минимальная сумма должна быть больше 0")
                return
            db.set_setting('min_deposit_stars', str(value))
            context.user_data.pop('awaiting')
            await update.message.reply_text(f"✅ Минимальное пополнение изменено на {value} ★")
        except ValueError:
            await update.message.reply_text("❌ Введите число")
    
    elif state == 'edit_star_rate':
        if user_id not in ADMIN_IDS:
            return
        
        try:
            value = float(text)
            if value < 0.1:
                await update.message.reply_text("❌ Курс должен быть больше 0.1")
                return
            db.set_setting('star_to_rub', str(value))
            context.user_data.pop('awaiting')
            await update.message.reply_text(f"✅ Курс изменён: 1 ★ = {value} руб")
        except ValueError:
            await update.message.reply_text("❌ Введите число")

# ================== ОБРАБОТЧИКИ ПЛАТЕЖЕЙ ==================

async def pre_checkout(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Проверка перед оплатой"""
    query = update.pre_checkout_query
    
    # Проверяем, что платёж валидный
    if query.invoice_payload.startswith("deposit_stars_"):
        await query.answer(ok=True)
    else:
        await query.answer(ok=False, error_message="Неверный платёж")

async def successful_payment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Успешная оплата"""
    user_id = update.effective_user.id
    payload = update.message.successful_payment.invoice_payload
    
    if payload.startswith("deposit_stars_"):
        # Парсим payload
        parts = payload.split("_")
        stars = int(parts[2])
        
        # Зачисляем баланс
        db.update_balance(user_id, stars)
        db.add_deposit(user_id, stars, stars, 'stars', f"stars_{stars}")
        
        await update.message.reply_text(
            f"✅ *Оплата прошла успешно!*\n\n"
            f"💰 На ваш баланс зачислено: {stars} ★",
            parse_mode=ParseMode.MARKDOWN
        )

def main():
    print("=" * 60)
    print(f"🚀 ЗАПУСК БОТА {BOT_NAME}")
    print("=" * 60)
    print("✅ Казино с 6 играми")
    print("✅ Кейсы с предметами")
    print("✅ Вывод средств")
    print("✅ Пополнение через Stars и CryptoBot")
    print(f"✅ Администраторы: {len(ADMIN_IDS)}")
    print("=" * 60)
    
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    
    # Добавляем хендлеры
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT | filters.PHOTO, handle_message))
    app.add_handler(PreCheckoutQueryHandler(pre_checkout))
    app.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment))
    
    # Обновляем username бота при старте
    async def post_init(application):
        await update_bot_username(application)
    
    app.post_init = post_init
    
    print("🤖 Бот запущен и готов к работе!")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
