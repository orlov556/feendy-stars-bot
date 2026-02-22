import logging
import random
import sqlite3
import asyncio
import json
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes, MessageHandler, filters
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
    
    def create_invoice(self, amount, currency="TON", description="Пополнение баланса FEENDY STARS"):
        try:
            url = f"{CRYPTOBOT_API_URL}/createInvoice"
            payload = {
                "asset": currency,
                "amount": str(amount),
                "description": description,
                "paid_btn_name": "return",
                "paid_btn_url": "https://t.me/YOUR_BOT_USERNAME",
                "payload": f"deposit_{amount}_{int(time.time())}"
            }
            
            response = requests.post(url, headers=self.headers, json=payload)
            if response.status_code == 200:
                data = response.json()
                if data.get('ok'):
                    return data['result']
            return None
        except Exception as e:
            logger.error(f"CryptoBot API error: {e}")
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
            response = requests.post(url, headers=self.headers, json=payload)
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
                is_admin INTEGER DEFAULT 0,
                is_banned INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                total_withdrawn INTEGER DEFAULT 0,
                total_lost INTEGER DEFAULT 0
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
                crypto_id TEXT,
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
                    {'name': 'Сердце', 'chance': 60, 'value': 15},
                    {'name': 'Роза', 'chance': 17, 'value': 25},
                    {'name': 'Ракета', 'chance': 7, 'value': 50},
                    {'name': 'Цветы', 'chance': 7, 'value': 50},
                    {'name': 'Кольцо', 'chance': 3, 'value': 100},
                    {'name': 'Алмаз', 'chance': 1.5, 'value': 100},
                    {'name': 'Люлом', 'chance': 1, 'value': 325},
                    {'name': 'Chyn Dogg', 'chance': 1, 'value': 425}
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
            self.cursor.execute('''
                INSERT OR IGNORE INTO users (user_id, username, first_name, is_admin)
                VALUES (?, 'admin', 'Admin', 1)
            ''', (admin_id,))
        self.conn.commit()
    
    def _init_settings(self):
        settings = {
            'min_withdrawal': '50',
            'withdrawal_fee': '0',
            'case_price': '35',
            'house_edge': '10'
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
    
    def add_lost_stars(self, user_id, amount):
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
    
    def get_all_users(self):
        self.cursor.execute('SELECT user_id, username, first_name, balance, snowflakes, created_at FROM users ORDER BY created_at DESC')
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
        {'name': 'Носок', 'price': 1250},
        {'name': 'Змея в коробке', 'price': 1250},
        {'name': 'Змея 2025', 'price': 1250},
        {'name': 'Колокольчики', 'price': 1600},
        {'name': 'Бенгальские огни', 'price': 1300},
        {'name': 'Пряничный человечек', 'price': 1550}
    ]
    
    def buy_winter_nft(self, user_id, item_name):
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
    
    # ================== ВЫВОД ==================
    
    def create_withdrawal(self, user_id, amount, crypto_id):
        self.cursor.execute('''
            INSERT INTO withdrawals (user_id, amount, crypto_id)
            VALUES (?, ?, ?)
        ''', (user_id, amount, crypto_id))
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
            SELECT user_id, amount, crypto_id FROM withdrawals WHERE id = ? AND status = 'pending'
        ''', (withdrawal_id,))
        withdrawal = self.cursor.fetchone()
        
        if not withdrawal:
            return False
        
        user_id, amount, crypto_id = withdrawal
        
        user = self.get_user(user_id)
        if user[3] < amount:
            return False
        
        if crypto.transfer(crypto_id, amount):
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
    
    # ================== ПЛАТЕЖИ ==================
    
    def add_payment(self, user_id, amount, invoice_id):
        self.cursor.execute('''
            INSERT INTO payments (user_id, amount, invoice_id, status)
            VALUES (?, ?, ?, 'pending')
        ''', (user_id, amount, invoice_id))
        self.conn.commit()
    
    def confirm_payment(self, invoice_id):
        self.cursor.execute('''
            UPDATE payments SET status = 'completed' WHERE invoice_id = ? AND status = 'pending'
        ''', (invoice_id,))
        self.conn.commit()
        
        self.cursor.execute('SELECT user_id, amount FROM payments WHERE invoice_id = ?', (invoice_id,))
        result = self.cursor.fetchone()
        if result:
            user_id, amount = result
            self.update_balance(user_id, amount)
            return True
        return False
    
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

GAME_ODDS = {
    'flip': {'win_chance': 45, 'multiplier': 1.7, 'name': '🎲 Орёл и решка'},
    'roulette': {'win_chance': 20, 'multiplier': 4.5, 'name': '💀 Русская рулетка'},
    'wheel': {'win_chance': 8, 'multiplier': 10, 'name': '🎡 Колесо удачи'},
    'mines': {'win_chance': 12, 'multiplier': 7.5, 'name': '💣 Минное поле'},
    'dice': {'win_chance': 30, 'multiplier': 2.5, 'name': '🎲 Кости'},
    'slots': {'win_chance': 25, 'multiplier': 3.0, 'name': '🎰 Слоты'}
}

async def check_ban(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user = db.get_user(user_id)
    if user and user[9] == 1:
        await update.message.reply_text("❌ Вы заблокированы в этом боте.")
        return False
    return True

async def check_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user = db.get_user(user_id)
    return user and user[8] == 1

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
    
    if user_data and user_data[8] == 1:
        keyboard.append([InlineKeyboardButton("⚙️ Админ-панель", callback_data="admin_panel")])
    
    text = (
        f"🌟 *{BOT_NAME}*\n\n"
        f"ID: {user.id}\n"
        f"Имя: {user.first_name}\n"
        f"Баланс: {user_data[3]} ★\n"
        f"Снежинки: {user_data[4]} ✨\n\n"
        f"Выберите действие:"
    )
    
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(keyboard))

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    user_id = update.effective_user.id
    user = db.get_user(user_id)
    
    if not user:
        await query.edit_message_text("❌ Ошибка: пользователь не найден")
        return
    
    if user[9] == 1:
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
            f"**ID**: {user_id}\n"
            f"**Имя**: {user[2]}\n"
            f"**Username**: @{user[1] or 'нет'}\n"
            f"**Баланс**: {user[3]} ★\n"
            f"**Снежинки**: {user[4]} ✨\n"
            f"**Рефералов**: {user[5]}\n\n"
            f"**Статистика игр:**\n"
            f"• Всего игр: {stats[0] if stats else 0}\n"
            f"• Выиграно: {stats[1] if stats else 0}\n"
            f"• Проиграно: {stats[2] if stats else 0}\n"
            f"• Сумма ставок: {stats[3] if stats else 0} ★\n\n"
            f"💰 [Пополнить баланс](button:deposit_menu)\n"
            f"🎫 [Ввести промокод](button:promo_code)"
        )
        
        keyboard = [[InlineKeyboardButton("◀️ Главное меню", callback_data="main_menu")]]
        await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(keyboard))
    
    # ================== ПРАВИЛА ==================
    
    elif data == "rules":
        text = (
            f"📜 *Правила использования бота {BOT_NAME}*\n\n"
            f"**Запрещено:**\n"
            f"• Использование ботов для накрутки\n"
            f"• Создание мультикакаунтов\n"
            f"• Обман системы реферальной программы\n"
            f"• Любые попытки обмана администрации\n\n"
            f"**Разрешено:**\n"
            f"• Приглашать реальных друзей\n"
            f"• Активно участвовать в проекте\n"
            f"• Соблюдать правила каналов\n\n"
            f"**Нарушение правил ведет к:**\n"
            f"• Блокировке аккаунта\n"
            f"• Обнулению баланса\n"
            f"• Запрету на участие в проекте\n\n"
            f"Администрация оставляет за собой право блокировать пользователей "
            f"без объяснения причин при подозрении в мошенничестве."
        )
        
        keyboard = [[InlineKeyboardButton("◀️ Главное меню", callback_data="main_menu")]]
        await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(keyboard))
    
    # ================== ЗИМНИЙ МАГАЗИН ==================
    
    elif data == "winter_shop":
        text = (
            f"❄️ *Зимний магазин NFT*\n\n"
            f"**Ваши снежинки:** {user[4]} ✨\n\n"
            f"**Доступные NFT:**\n\n"
        )
        
        for item in db.WINTER_NFTS:
            text += f"• **{item['name']} - {item['price']}**\n"
        
        text += (
            f"\n**Как получить снежинки?**\n"
            f"• За каждую проигранную звезду: +0.5 ✨\n"
            f"• За каждого приглашенного друга: +5 ✨"
        )
        
        keyboard = []
        for item in db.WINTER_NFTS:
            keyboard.append([InlineKeyboardButton(
                f"🎁 Купить {item['name']} - {item['price']} ✨",
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
                        text = (
                            f"✅ *Покупка успешна!*\n\n"
                            f"Вы приобрели: **{item_name}**\n"
                            f"Цена: {item['price']} ✨\n\n"
                            f"NFT добавлен в вашу коллекцию!"
                        )
                    else:
                        text = "❌ Ошибка при покупке"
                else:
                    missing = item['price'] - user[4]
                    text = (
                        f"❌ *Недостаточно снежинок!*\n\n"
                        f"Требуется: {item['price']} ✨\n"
                        f"У вас: {user[4]} ✨\n"
                        f"Не хватает: {missing} ✨\n\n"
                        f"Играйте в казино и приглашайте друзей, "
                        f"чтобы заработать снежинки!"
                    )
                
                keyboard = [[InlineKeyboardButton("◀️ В магазин", callback_data="winter_shop")]]
                await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(keyboard))
                break
    
    # ================== ВЫВОД ==================
    
    elif data == "withdraw_menu":
        text = (
            f"💸 *Вывод средств*\n\n"
            f"**Баланс:** {user[3]} ★\n"
            f"**CryptoBot ID:** {user[7] or 'не указан'}\n\n"
            f"Минимальная сумма: 50 ★\n"
            f"Комиссия: 0%\n\n"
            f"Вывод происходит автоматически на ваш CryptoBot кошелек."
        )
        
        keyboard = [
            [InlineKeyboardButton("💳 Указать CryptoBot ID", callback_data="set_crypto_id")],
            [InlineKeyboardButton("💰 Создать заявку на вывод", callback_data="create_withdrawal")],
            [InlineKeyboardButton("📋 История выводов", callback_data="withdrawal_history")],
            [InlineKeyboardButton("◀️ Главное меню", callback_data="main_menu")]
        ]
        
        await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(keyboard))
    
    elif data == "set_crypto_id":
        context.user_data['awaiting'] = 'crypto_id'
        await query.edit_message_text(
            "💳 *Укажите ваш CryptoBot ID*\n\n"
            "Отправьте ID вашего кошелька в CryptoBot (число):\n"
            "Найти можно в боте @CryptoBot → Wallet → Profile → ID",
            parse_mode=ParseMode.MARKDOWN
        )
    
    elif data == "create_withdrawal":
        min_withdrawal = 50
        
        if user[3] < min_withdrawal:
            missing = min_withdrawal - user[3]
            text = (
                f"❌ *Недостаточно средств!*\n\n"
                f"Минимальная сумма вывода: {min_withdrawal} ★\n"
                f"У вас: {user[3]} ★\n"
                f"Не хватает: {missing} ★\n\n"
                f"Хотите пополнить баланс?"
            )
            keyboard = [
                [InlineKeyboardButton(f"💰 Пополнить {missing} ★", callback_data=f"deposit_{missing}")],
                [InlineKeyboardButton("◀️ Назад", callback_data="withdraw_menu")]
            ]
            await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(keyboard))
            return
        
        if not user[7]:
            await query.edit_message_text(
                "❌ Сначала укажите CryptoBot ID",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("💳 Указать ID", callback_data="set_crypto_id")]])
            )
            return
        
        context.user_data['awaiting'] = 'withdrawal_amount'
        await query.edit_message_text(
            f"💰 *Создание заявки на вывод*\n\n"
            f"Ваш баланс: {user[3]} ★\n"
            f"CryptoBot ID: {user[7]}\n\n"
            f"Введите сумму для вывода (мин. 50 ★):",
            parse_mode=ParseMode.MARKDOWN
        )
    
    # ================== ПОПОЛНЕНИЕ ==================
    
    elif data == "deposit_menu":
        text = "💰 *Пополнение баланса*\n\nВыберите сумму:"
        keyboard = [
            [
                InlineKeyboardButton("10 ★", callback_data="deposit_10"),
                InlineKeyboardButton("25 ★", callback_data="deposit_25"),
                InlineKeyboardButton("50 ★", callback_data="deposit_50")
            ],
            [
                InlineKeyboardButton("100 ★", callback_data="deposit_100"),
                InlineKeyboardButton("250 ★", callback_data="deposit_250"),
                InlineKeyboardButton("500 ★", callback_data="deposit_500")
            ],
            [InlineKeyboardButton("◀️ Главное меню", callback_data="main_menu")]
        ]
        await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(keyboard))
    
    elif data.startswith("deposit_"):
        amount = int(data.replace("deposit_", ""))
        
        invoice = crypto.create_invoice(amount, "TON", f"Пополнение баланса {BOT_NAME} на {amount} ★")
        
        if invoice:
            pay_url = invoice['pay_url']
            invoice_id = invoice['invoice_id']
            
            db.add_payment(user_id, amount, invoice_id)
            
            text = (
                f"💰 *Пополнение на {amount} ★*\n\n"
                f"Для оплаты перейдите по ссылке ниже:\n"
                f"[Оплатить {amount} ★]({pay_url})\n\n"
                f"После оплаты баланс будет зачислен автоматически."
            )
            keyboard = [[InlineKeyboardButton("◀️ Главное меню", callback_data="main_menu")]]
            await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(keyboard))
        else:
            await query.edit_message_text("❌ Ошибка создания счета. Попробуйте позже.")
    
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
    
    elif data.startswith("game_"):
        game_type = data.replace("game_", "")
        context.user_data['current_game'] = game_type
        
        text = (
            f"{GAME_ODDS[game_type]['name']}\n\n"
            f"💰 Ваш баланс: {user[3]} ★\n\n"
            f"Выберите сумму ставки:"
        )
        
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
        game_type = context.user_data.get('current_game', 'flip')
        game = GAME_ODDS[game_type]
        
        if user[3] < bet:
            missing = bet - user[3]
            text = (
                f"❌ *Недостаточно средств!*\n\n"
                f"Ставка: {bet} ★\n"
                f"У вас: {user[3]} ★\n"
                f"Не хватает: {missing} ★\n\n"
                f"Хотите пополнить баланс?"
            )
            keyboard = [
                [InlineKeyboardButton(f"💰 Пополнить {missing} ★", callback_data=f"deposit_{missing}")],
                [InlineKeyboardButton("◀️ Назад", callback_data=f"game_{game_type}")]
            ]
            await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(keyboard))
            return
        
        # Списываем ставку
        db.update_balance(user_id, -bet)
        
        # Определяем результат
        roll = random.randint(1, 100)
        win = roll <= game['win_chance']
        
        if win:
            win_amount = int(bet * game['multiplier'])
            db.update_balance(user_id, win_amount)
            db.add_game(user_id, game_type, bet, game['multiplier'], win_amount, 'win')
            
            text = (
                f"🎉 *ВЫ ВЫИГРАЛИ!*\n\n"
                f"📊 *Игра:* {game['name']}\n"
                f"💰 *Ставка:* {bet} ★\n"
                f"📈 *Множитель:* x{game['multiplier']}\n"
                f"💎 *Выигрыш:* {win_amount} ★"
            )
        else:
            # Начисляем снежинки за проигрыш
            db.add_lost_stars(user_id, bet)
            db.add_game(user_id, game_type, bet, 0, 0, 'lose')
            
            text = (
                f"😢 *ВЫ ПРОИГРАЛИ*\n\n"
                f"📊 *Игра:* {game['name']}\n"
                f"💰 *Ставка:* {bet} ★ проиграна\n"
                f"✨ *Получено снежинок:* {int(bet * 0.5)}\n\n"
                f"🍀 Повезёт в следующий раз!"
            )
        
        keyboard = [
            [InlineKeyboardButton("🎮 Играть ещё", callback_data=f"game_{game_type}")],
            [InlineKeyboardButton("◀️ Главное меню", callback_data="main_menu")]
        ]
        
        await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(keyboard))
    
    # ================== КЕЙС ==================
    
    elif data == "case_menu":
        cases = db.get_cases()
        case = cases[0] if cases else None
        
        if case:
            text = (
                f"📦 *Кейс {BOT_NAME}*\n\n"
                f"Цена открытия: {case[2]} ★\n\n"
                f"**Возможные предметы:**\n"
            )
            
            items = json.loads(case[3])
            for item in items:
                text += f"• {item['name']} — шанс {item['chance']}%\n"
            
            keyboard = [
                [InlineKeyboardButton(f"📦 Открыть кейс ({case[2]} ★)", callback_data=f"open_case_{case[0]}")],
                [InlineKeyboardButton("◀️ Главное меню", callback_data="main_menu")]
            ]
            await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(keyboard))
    
    elif data.startswith("open_case_"):
        case_id = int(data.replace("open_case_", ""))
        case_price = 35
        
        if user[3] < case_price:
            missing = case_price - user[3]
            text = (
                f"❌ *Недостаточно средств!*\n\n"
                f"Цена открытия: {case_price} ★\n"
                f"У вас: {user[3]} ★\n"
                f"Не хватает: {missing} ★\n\n"
                f"Хотите пополнить баланс?"
            )
            keyboard = [
                [InlineKeyboardButton(f"💰 Пополнить {missing} ★", callback_data=f"deposit_{missing}")],
                [InlineKeyboardButton("◀️ Назад", callback_data="case_menu")]
            ]
            await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(keyboard))
            return
        
        db.update_balance(user_id, -case_price)
        result = db.open_case(case_id, user_id)
        
        if result:
            text = (
                f"🎉 *Поздравляем!*\n\n"
                f"Вы выиграли: **{result['name']}**\n"
                f"Редкость: {result['chance']}%\n"
                f"Стоимость: {result['value']} ★\n\n"
                f"Предмет добавлен в инвентарь!"
            )
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
            f"**Ваша ссылка:**\n`{ref_link}`\n\n"
            f"**Приглашено друзей:** {user[5]}\n"
            f"**Заработано снежинок:** {user[5] * 5} ✨\n\n"
            f"**Как это работает:**\n"
            f"• За каждого приглашенного друга: +5 ✨\n"
            f"• Друг должен начать пользоваться ботом\n"
            f"• Снежинки можно тратить в зимнем магазине"
        )
        
        keyboard = [[InlineKeyboardButton("◀️ Главное меню", callback_data="main_menu")]]
        await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(keyboard))
    
    # ================== ЕЖЕДНЕВНЫЙ БОНУС ==================
    
    elif data == "daily_bonus":
        if db.check_daily_bonus(user_id):
            text = "🎁 *Ежедневный бонус получен!*\n\n+5 ★ звёзд"
        else:
            text = "❌ Вы уже получали бонус сегодня. Приходите завтра!"
        
        keyboard = [[InlineKeyboardButton("◀️ Главное меню", callback_data="main_menu")]]
        await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(keyboard))
    
    # ================== АДМИН-ПАНЕЛЬ ==================
    
    elif data == "admin_panel":
        if not await check_admin(update, context):
            await query.edit_message_text("❌ У вас нет прав администратора")
            return
        
        stats = db.get_total_stats()
        pending = db.get_pending_withdrawals()
        
        text = (
            f"⚙️ *Админ-панель {BOT_NAME}*\n\n"
            f"📊 **Статистика:**\n"
            f"• Пользователей: {stats['total_users']}\n"
            f"• Общий баланс: {stats['total_balance']} ★\n"
            f"• Всего снежинок: {stats['total_snowflakes']} ✨\n"
            f"• Выведено: {stats['total_withdrawn']} ★\n"
            f"• Всего игр: {stats['total_games']}\n\n"
            f"⏳ **Заявок на вывод:** {len(pending)}"
        )
        
        keyboard = [
            [InlineKeyboardButton("👥 Все пользователи", callback_data="admin_users")],
            [InlineKeyboardButton("⏳ Заявки на вывод", callback_data="admin_withdrawals")],
            [InlineKeyboardButton("🔨 Управление банами", callback_data="admin_bans")],
            [InlineKeyboardButton("📢 Рассылка", callback_data="admin_broadcast")],
            [InlineKeyboardButton("⚙️ Настройки", callback_data="admin_settings")],
            [InlineKeyboardButton("◀️ Главное меню", callback_data="main_menu")]
        ]
        
        await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(keyboard))
    
    elif data == "admin_users":
        if not await check_admin(update, context):
            return
        
        users = db.get_all_users()
        text = f"👥 *Всего пользователей: {len(users)}*\n\n"
        
        for u in users[:20]:
            text += f"• {u[2]} (@{u[1]}) — {u[3]} ★, {u[4]} ✨\n"
        
        if len(users) > 20:
            text += f"\n...и ещё {len(users)-20} пользователей"
        
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
                f"💳 CryptoBot ID: {w[4]}\n"
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
            await query.edit_message_text("✅ Заявка одобрена и средства отправлены")
            
            db.cursor.execute('SELECT user_id, amount FROM withdrawals WHERE id = ?', (withdrawal_id,))
            w_user_id, amount = db.cursor.fetchone()
            try:
                await context.bot.send_message(
                    w_user_id,
                    f"✅ *Заявка на вывод одобрена!*\n\n"
                    f"💰 Сумма: {amount} ★\n"
                    f"Средства отправлены на ваш CryptoBot кошелек.",
                    parse_mode=ParseMode.MARKDOWN
                )
            except:
                pass
        else:
            await query.edit_message_text("❌ Ошибка: недостаточно средств или заявка уже обработана")
    
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
                    f"Причина: проверьте правильность CryptoBot ID.",
                    parse_mode=ParseMode.MARKDOWN
                )
            except:
                pass
        else:
            await query.edit_message_text("❌ Ошибка при отклонении заявки")
    
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
    
    elif data == "admin_broadcast":
        if not await check_admin(update, context):
            return
        
        context.user_data['awaiting'] = 'broadcast'
        await query.edit_message_text(
            "📢 *Создание рассылки*\n\n"
            "Отправьте сообщение (можно с фото), которое хотите разослать всем пользователям:",
            parse_mode=ParseMode.MARKDOWN
        )
    
    elif data == "admin_settings":
        if not await check_admin(update, context):
            return
        
        min_withdrawal = db.get_setting('min_withdrawal', '50')
        withdrawal_fee = db.get_setting('withdrawal_fee', '0')
        case_price = db.get_setting('case_price', '35')
        house_edge = db.get_setting('house_edge', '10')
        
        text = (
            f"⚙️ *Настройки бота*\n\n"
            f"💰 Мин. вывод: {min_withdrawal} ★\n"
            f"💸 Комиссия вывода: {withdrawal_fee}%\n"
            f"📦 Цена кейса: {case_price} ★\n"
            f"🎰 Преимущество казино: {house_edge}%\n\n"
            f"Выберите параметр для изменения:"
        )
        
        keyboard = [
            [InlineKeyboardButton("💰 Мин. вывод", callback_data="edit_min_withdrawal")],
            [InlineKeyboardButton("💸 Комиссия вывода", callback_data="edit_withdrawal_fee")],
            [InlineKeyboardButton("📦 Цена кейса", callback_data="edit_case_price")],
            [InlineKeyboardButton("🎰 Преимущество казино", callback_data="edit_house_edge")],
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
    
    elif data == "edit_case_price":
        if not await check_admin(update, context):
            return
        
        context.user_data['awaiting'] = 'edit_case_price'
        await query.edit_message_text(
            "📦 Введите новую цену кейса (в ★):",
            parse_mode=ParseMode.MARKDOWN
        )
    
    elif data == "edit_house_edge":
        if not await check_admin(update, context):
            return
        
        context.user_data['awaiting'] = 'edit_house_edge'
        await query.edit_message_text(
            "🎰 Введите новое преимущество казино (в %, от 1 до 50):\n\n"
            "Чем выше %, тем меньше выигрывают игроки.",
            parse_mode=ParseMode.MARKDOWN
        )
    
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
        
        if user and user[8] == 1:
            keyboard.append([InlineKeyboardButton("⚙️ Админ-панель", callback_data="admin_panel")])
        
        text = (
            f"🌟 *{BOT_NAME}*\n\n"
            f"ID: {user_id}\n"
            f"Имя: {update.effective_user.first_name}\n"
            f"Баланс: {user[3]} ★\n"
            f"Снежинки: {user[4]} ✨\n\n"
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
    
    if state == 'crypto_id':
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
    
    elif state == 'withdrawal_amount':
        try:
            amount = int(text)
            
            if amount < 50:
                await update.message.reply_text("❌ Минимальная сумма вывода — 50 ★")
                return
            
            user = db.get_user(user_id)
            if amount > user[3]:
                await update.message.reply_text("❌ Недостаточно средств")
                return
            
            withdrawal_id = db.create_withdrawal(user_id, amount, user[7])
            
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
            value = int(text)
            if value < 1:
                await update.message.reply_text("❌ Минимальная сумма должна быть больше 0")
                return
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
    
    elif state == 'edit_case_price':
        if user_id not in ADMIN_IDS:
            return
        
        try:
            value = int(text)
            if value < 1:
                await update.message.reply_text("❌ Цена должна быть больше 0")
                return
            db.set_setting('case_price', str(value))
            context.user_data.pop('awaiting')
            await update.message.reply_text(f"✅ Цена кейса изменена на {value} ★")
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

def main():
    print("=" * 60)
    print(f"🚀 ЗАПУСК БОТА {BOT_NAME}")
    print("=" * 60)
    print("✅ Зимний магазин NFT (6 предметов)")
    print("✅ Система снежинок (0.5 за проигрыш)")
    print("✅ Вывод от 50 ★ через CryptoBot")
    print("✅ Казино с 6 играми")
    print("✅ Кейс FEENDY STARS")
    print("✅ Реферальная система")
    print("✅ Полная админ-панель")
    print(f"✅ Администраторы: {len(ADMIN_IDS)}")
    print("=" * 60)
    
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT | filters.PHOTO, handle_message))
    
    print("🤖 Бот запущен и готов к работе!")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()

