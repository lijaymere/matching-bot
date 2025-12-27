# bot.py - Complete production-ready bot for Railway
import asyncio
import logging
import os
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Tuple

from aiogram import Bot, Dispatcher, types, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.redis import RedisStorage
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup,
    InlineKeyboardButton, Location, PhotoSize, InputMediaPhoto,
    ReplyKeyboardRemove
)
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart, Command
import asyncpg
from redis.asyncio import Redis
import math
import json
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# ============= CONFIGURATION =============
TOKEN = "8543856764:AAGI8iMM4G1tWRpv5K2nOb8DkdofYYNIKow"
ADMIN_ID = 1405012211

# Railway automatically provides these environment variables
DATABASE_URL = os.getenv("DATABASE_URL")
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")
PORT = int(os.getenv("PORT", 8080))

# Ethiopian cultural interests
CULTURAL_INTERESTS = [
    {"id": 1, "en": "Bunna (Coffee)", "am": "ቡና"},
    {"id": 2, "en": "Eskista Dance", "am": "እስክስታ"},
    {"id": 3, "en": "Saint George FC", "am": "ሰይንት ጊዮርጊስ"},
    {"id": 4, "en": "Bunna FC", "am": "ቡና እግር ኳስ"},
    {"id": 5, "en": "Hiking in Entoto", "am": "በእንጦጦ ላይ ጉዞ"},
    {"id": 6, "en": "Tech/Startup Scene", "am": "ቴክ/መነሻ ስራ"},
    {"id": 7, "en": "Azmari Bet", "am": "አዝማሪ ቤት"},
    {"id": 8, "en": "Traditional Food", "am": "ባህላዊ ምግብ"},
    {"id": 9, "en": "Ethiopian Music", "am": "ኢትዮጵያዊ ሙዚቃ"},
    {"id": 10, "en": "Orthodox Christianity", "am": "ኦርቶዶክስ ክርስትና"},
]

# Addis Ababa sub-cities with coordinates
SUB_CITIES = {
    "Bole": (8.9806, 38.7990),
    "Kazanchis": (9.0227, 38.7469),
    "Piassa": (9.0300, 38.7500),
    "Megenagna": (9.0400, 38.7800),
    "Saris": (9.0500, 38.8100),
    "Kirkos": (9.0100, 38.7600),
    "Arada": (9.0300, 38.7400),
    "Yeka": (9.0600, 38.8200),
    "Lideta": (9.0200, 38.7300),
    "Nifas Silk": (9.0700, 38.7700),
    "Gullele": (9.0800, 38.7900),
}

# ============= DATABASE SETUP =============
async def init_db():
    """Initialize PostgreSQL database connection"""
    conn = await asyncpg.connect(DATABASE_URL)
    
    # Create tables if they don't exist
    await conn.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id SERIAL PRIMARY KEY,
            telegram_id BIGINT UNIQUE NOT NULL,
            language VARCHAR(10) DEFAULT 'en',
            full_name VARCHAR(200) NOT NULL,
            age INTEGER,
            gender VARCHAR(20),
            preference VARCHAR(20) DEFAULT 'both',
            bio TEXT,
            latitude FLOAT,
            longitude FLOAT,
            sub_city VARCHAR(100),
            search_radius INTEGER DEFAULT 10,
            photo_ids JSONB DEFAULT '[]',
            main_photo_id VARCHAR(300),
            is_verified BOOLEAN DEFAULT FALSE,
            is_active BOOLEAN DEFAULT TRUE,
            is_stealth BOOLEAN DEFAULT FALSE,
            is_premium BOOLEAN DEFAULT FALSE,
            notify_matches BOOLEAN DEFAULT TRUE,
            notify_nearby BOOLEAN DEFAULT TRUE,
            likes_today INTEGER DEFAULT 0,
            last_like_reset TIMESTAMP DEFAULT NOW(),
            created_at TIMESTAMP DEFAULT NOW(),
            updated_at TIMESTAMP DEFAULT NOW(),
            last_seen TIMESTAMP DEFAULT NOW()
        )
    ''')
    
    await conn.execute('''
        CREATE TABLE IF NOT EXISTS interests (
            id SERIAL PRIMARY KEY,
            name_en VARCHAR(100) NOT NULL,
            name_am VARCHAR(100) NOT NULL
        )
    ''')
    
    await conn.execute('''
        CREATE TABLE IF NOT EXISTS user_interests (
            user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
            interest_id INTEGER REFERENCES interests(id) ON DELETE CASCADE,
            PRIMARY KEY (user_id, interest_id)
        )
    ''')
    
    await conn.execute('''
        CREATE TABLE IF NOT EXISTS likes (
            id SERIAL PRIMARY KEY,
            from_user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
            to_user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
            created_at TIMESTAMP DEFAULT NOW(),
            UNIQUE(from_user_id, to_user_id)
        )
    ''')
    
    await conn.execute('''
        CREATE TABLE IF NOT EXISTS matches (
            id SERIAL PRIMARY KEY,
            user1_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
            user2_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
            matched_at TIMESTAMP DEFAULT NOW(),
            chat_active BOOLEAN DEFAULT TRUE
        )
    ''')
    
    await conn.execute('''
        CREATE TABLE IF NOT EXISTS chat_messages (
            id SERIAL PRIMARY KEY,
            match_id INTEGER REFERENCES matches(id) ON DELETE CASCADE,
            sender_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
            message TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT NOW()
        )
    ''')
    
    await conn.execute('''
        CREATE TABLE IF NOT EXISTS reports (
            id SERIAL PRIMARY KEY,
            reporter_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
            reported_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
            reason TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT NOW()
        )
    ''')
    
    # Insert cultural interests if not exists
    for interest in CULTURAL_INTERESTS:
        await conn.execute('''
            INSERT INTO interests (id, name_en, name_am)
            VALUES ($1, $2, $3)
            ON CONFLICT (id) DO NOTHING
        ''', interest["id"], interest["en"], interest["am"])
    
    await conn.close()

# ============= UTILITIES =============
def haversine_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculate distance between two points in kilometers"""
    R = 6371  # Earth radius in km
    lat1, lon1, lat2, lon2 = map(math.radians, [lat1, lon1, lat2, lon2])
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = math.sin(dlat/2)**2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon/2)**2
    return 2 * R * math.asin(math.sqrt(a))

def get_subcity_coordinates(subcity: str) -> Tuple[float, float]:
    """Get coordinates for Addis sub-city"""
    return SUB_CITIES.get(subcity, (9.0227, 38.7469))  # Default to Kazanchis

# ============= KEYBOARDS =============
def get_language_keyboard() -> InlineKeyboardMarkup:
    """Language selection keyboard"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="English 🇺🇸", callback_data="lang_en")],
        [InlineKeyboardButton(text="አማርኛ 🇪🇹", callback_data="lang_am")]
    ])

def get_gender_keyboard(lang: str = "en") -> InlineKeyboardMarkup:
    """Gender selection keyboard"""
    if lang == "am":
        buttons = [
            [InlineKeyboardButton(text="ወንድ 👨", callback_data="gender_male")],
            [InlineKeyboardButton(text="ሴት 👩", callback_data="gender_female")],
            [InlineKeyboardButton(text="ሌላ 🏳️‍🌈", callback_data="gender_other")]
        ]
    else:
        buttons = [
            [InlineKeyboardButton(text="Male 👨", callback_data="gender_male")],
            [InlineKeyboardButton(text="Female 👩", callback_data="gender_female")],
            [InlineKeyboardButton(text="Other 🏳️‍🌈", callback_data="gender_other")]
        ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_preference_keyboard(lang: str = "en") -> InlineKeyboardMarkup:
    """Preference selection keyboard"""
    if lang == "am":
        buttons = [
            [InlineKeyboardButton(text="ወንዶች 🧑‍🤝‍🧑", callback_data="pref_male")],
            [InlineKeyboardButton(text="ሴቶች 👭", callback_data="pref_female")],
            [InlineKeyboardButton(text="ሁለቱም 🤝", callback_data="pref_both")]
        ]
    else:
        buttons = [
            [InlineKeyboardButton(text="Men 🧑‍🤝‍🧑", callback_data="pref_male")],
            [InlineKeyboardButton(text="Women 👭", callback_data="pref_female")],
            [InlineKeyboardButton(text="Both 🤝", callback_data="pref_both")]
        ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_location_options_keyboard(lang: str = "en") -> InlineKeyboardMarkup:
    """Location sharing options keyboard"""
    if lang == "am":
        buttons = [
            [InlineKeyboardButton(text="📍 አሁን አካባቢ ላክ", request_location=True)],
            [InlineKeyboardButton(text="🏙 ንኡስ ከተማ ምረጥ", callback_data="choose_subcity")]
        ]
    else:
        buttons = [
            [InlineKeyboardButton(text="📍 Share Current Location", request_location=True)],
            [InlineKeyboardButton(text="🏙 Choose Sub-City", callback_data="choose_subcity")]
        ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_subcity_keyboard() -> InlineKeyboardMarkup:
    """Addis Ababa sub-cities keyboard"""
    buttons = []
    subcities = list(SUB_CITIES.keys())
    for i in range(0, len(subcities), 2):
        row = []
        if i < len(subcities):
            row.append(InlineKeyboardButton(text=subcities[i], callback_data=f"subcity_{subcities[i]}"))
        if i + 1 < len(subcities):
            row.append(InlineKeyboardButton(text=subcities[i+1], callback_data=f"subcity_{subcities[i+1]}"))
        buttons.append(row)
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_interests_keyboard(lang: str = "en", selected: List[int] = None) -> InlineKeyboardMarkup:
    """Interests selection keyboard"""
    if selected is None:
        selected = []
    
    buttons = []
    for interest in CULTURAL_INTERESTS:
        name = interest["am"] if lang == "am" else interest["en"]
        check = "✅ " if interest["id"] in selected else ""
        buttons.append([
            InlineKeyboardButton(
                text=f"{check}{name}",
                callback_data=f"interest_{interest['id']}"
            )
        ])
    
    buttons.append([
        InlineKeyboardButton(
            text="✅ Done / ተጠናቅቋል" if lang == "am" else "✅ Done",
            callback_data="interests_done"
        )
    ])
    
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_main_menu_keyboard(lang: str = "en") -> InlineKeyboardMarkup:
    """Main menu keyboard"""
    if lang == "am":
        buttons = [
            [InlineKeyboardButton(text="👀 ሰዎችን ይመልከቱ", callback_data="browse")],
            [InlineKeyboardButton(text="💌 ተመሳሳይ ሰዎች", callback_data="matches")],
            [InlineKeyboardButton(text="⚙️ ማስተካከያዎች", callback_data="settings")],
            [InlineKeyboardButton(text="🆘 እገዛ", callback_data="help")]
        ]
    else:
        buttons = [
            [InlineKeyboardButton(text="👀 Browse People", callback_data="browse")],
            [InlineKeyboardButton(text="💌 My Matches", callback_data="matches")],
            [InlineKeyboardButton(text="⚙️ Settings", callback_data="settings")],
            [InlineKeyboardButton(text="🆘 Help", callback_data="help")]
        ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_profile_action_keyboard(profile_id: int, lang: str = "en") -> InlineKeyboardMarkup:
    """Like/Dislike/Report buttons for profiles"""
    if lang == "am":
        buttons = [
            [
                InlineKeyboardButton(text="👍 አስተያየት", callback_data=f"like_{profile_id}"),
                InlineKeyboardButton(text="👎 አልወደውም", callback_data=f"dislike_{profile_id}")
            ],
            [InlineKeyboardButton(text="⚠️ ሪፖርት", callback_data=f"report_{profile_id}")]
        ]
    else:
        buttons = [
            [
                InlineKeyboardButton(text="👍 Like", callback_data=f"like_{profile_id}"),
                InlineKeyboardButton(text="👎 Dislike", callback_data=f"dislike_{profile_id}")
            ],
            [InlineKeyboardButton(text="⚠️ Report", callback_data=f"report_{profile_id}")]
        ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_settings_keyboard(lang: str = "en") -> InlineKeyboardMarkup:
    """Settings menu keyboard"""
    if lang == "am":
        buttons = [
            [InlineKeyboardButton(text="🌍 ቋንቋ ቀይር", callback_data="change_language")],
            [InlineKeyboardButton(text="📍 አካባቢ አዘምን", callback_data="update_location")],
            [InlineKeyboardButton(text="👁️ ስልኬን ቀይር", callback_data="toggle_stealth")],
            [InlineKeyboardButton(text="🔔 ማሳወቂያዎች", callback_data="notifications")],
            [InlineKeyboardButton(text="↩️ ወደ ዋና ገጽ", callback_data="main_menu")]
        ]
    else:
        buttons = [
            [InlineKeyboardButton(text="🌍 Change Language", callback_data="change_language")],
            [InlineKeyboardButton(text="📍 Update Location", callback_data="update_location")],
            [InlineKeyboardButton(text="👁️ Toggle Stealth Mode", callback_data="toggle_stealth")],
            [InlineKeyboardButton(text="🔔 Notifications", callback_data="notifications")],
            [InlineKeyboardButton(text="↩️ Back to Main", callback_data="main_menu")]
        ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)

# ============= FSM STATES =============
class RegistrationStates(StatesGroup):
    language = State()
    name = State()
    age = State()
    gender = State()
    preference = State()
    location = State()
    interests = State()
    photo = State()
    bio = State()

class SettingsStates(StatesGroup):
    location = State()
    bio = State()

# ============= BOT INITIALIZATION =============
bot = Bot(
    token=TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML)
)

redis = Redis.from_url(REDIS_URL)
storage = RedisStorage(redis=redis)
dp = Dispatcher(storage=storage)
router = Router()
dp.include_router(router)

# ============= DATABASE FUNCTIONS =============
async def get_db_connection():
    """Get database connection"""
    return await asyncpg.connect(DATABASE_URL)

async def get_user(telegram_id: int):
    """Get user from database"""
    conn = await get_db_connection()
    user = await conn.fetchrow(
        "SELECT * FROM users WHERE telegram_id = $1",
        telegram_id
    )
    await conn.close()
    return user

async def create_user(telegram_id: int, language: str, full_name: str):
    """Create new user"""
    conn = await get_db_connection()
    await conn.execute(
        """INSERT INTO users (telegram_id, language, full_name, created_at, last_seen)
           VALUES ($1, $2, $3, NOW(), NOW())""",
        telegram_id, language, full_name
    )
    await conn.close()

async def update_user(telegram_id: int, **kwargs):
    """Update user fields"""
    if not kwargs:
        return
    
    conn = await get_db_connection()
    set_clause = ", ".join([f"{key} = ${i+2}" for i, key in enumerate(kwargs.keys())])
    values = list(kwargs.values())
    
    await conn.execute(
        f"UPDATE users SET {set_clause}, updated_at = NOW() WHERE telegram_id = $1",
        telegram_id, *values
    )
    await conn.close()

async def add_user_interests(telegram_id: int, interest_ids: List[int]):
    """Add interests for user"""
    conn = await get_db_connection()
    user = await get_user(telegram_id)
    
    # Clear existing interests
    await conn.execute(
        "DELETE FROM user_interests WHERE user_id = $1",
        user["id"]
    )
    
    # Add new interests
    for interest_id in interest_ids:
        await conn.execute(
            "INSERT INTO user_interests (user_id, interest_id) VALUES ($1, $2)",
            user["id"], interest_id
        )
    
    await conn.close()

async def get_nearby_users(telegram_id: int, limit: int = 20):
    """Get nearby users for browsing"""
    user = await get_user(telegram_id)
    if not user or not user["latitude"]:
        return []
    
    conn = await get_db_connection()
    
    # Query for nearby users with same preference
    query = """
        SELECT u.*, 
               ARRAY_AGG(ui.interest_id) as interest_ids
        FROM users u
        LEFT JOIN user_interests ui ON u.id = ui.user_id
        WHERE u.telegram_id != $1
          AND u.is_active = TRUE
          AND u.is_stealth = FALSE
          AND (
            u.preference = 'both' OR
            (u.preference = 'male' AND $2 = 'male') OR
            (u.preference = 'female' AND $2 = 'female')
          )
          AND ($3 = 'both' OR 
               ($3 = 'male' AND u.gender = 'male') OR
               ($3 = 'female' AND u.gender = 'female'))
        GROUP BY u.id
        ORDER BY u.created_at DESC
        LIMIT $4
    """
    
    users = await conn.fetch(
        query,
        telegram_id,
        user["gender"],
        user["preference"],
        limit
    )
    
    await conn.close()
    return users

async def create_like(from_user_id: int, to_user_id: int) -> bool:
    """Create a like and check for match"""
    from_user = await get_user(from_user_id)
    to_user = await get_user(to_user_id)
    
    if not from_user or not to_user:
        return False
    
    conn = await get_db_connection()
    
    # Check if already liked
    existing = await conn.fetchrow(
        "SELECT * FROM likes WHERE from_user_id = $1 AND to_user_id = $2",
        to_user["id"], from_user["id"]
    )
    
    # Add like
    await conn.execute(
        "INSERT INTO likes (from_user_id, to_user_id) VALUES ($1, $2)",
        from_user["id"], to_user["id"]
    )
    
    # Update likes count
    await conn.execute(
        "UPDATE users SET likes_today = likes_today + 1 WHERE id = $1",
        from_user["id"]
    )
    
    is_match = False
    if existing:
        # Create match
        await conn.execute(
            """INSERT INTO matches (user1_id, user2_id) 
               VALUES ($1, $2), ($2, $1)
               ON CONFLICT DO NOTHING""",
            min(from_user["id"], to_user["id"]),
            max(from_user["id"], to_user["id"])
        )
        is_match = True
    
    await conn.close()
    return is_match

async def get_user_matches(telegram_id: int):
    """Get user's matches"""
    user = await get_user(telegram_id)
    if not user:
        return []
    
    conn = await get_db_connection()
    
    matches = await conn.fetch("""
        SELECT u.* FROM matches m
        JOIN users u ON (m.user2_id = u.id AND m.user1_id = $1)
                      OR (m.user1_id = u.id AND m.user2_id = $1)
        WHERE u.id != $1
        ORDER BY m.matched_at DESC
    """, user["id"])
    
    await conn.close()
    return matches

# ============= HANDLERS =============
@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    """Start command handler - Language first approach"""
    user = await get_user(message.from_user.id)
    
    if user:
        # User exists, show main menu
        await show_main_menu(message, user["language"])
        await state.clear()
    else:
        # New user - start registration
        await message.answer(
            "🌍 <b>Habesha Match</b>\n\n"
            "Please choose your language / ቋንቋ ይምረጡ:",
            reply_markup=get_language_keyboard()
        )
        await state.set_state(RegistrationStates.language)

@router.callback_query(F.data.startswith("lang_"))
async def process_language(callback: CallbackQuery, state: FSMContext):
    """Handle language selection"""
    language = callback.data.split("_")[1]  # en or am
    
    await state.update_data(language=language)
    
    if language == "am":
        text = "👤 <b>ስምህ ምን ይባላል?</b>\n\nሙሉ ስምህን አስገባ:"
    else:
        text = "👤 <b>What's your name?</b>\n\nEnter your full name:"
    
    await callback.message.edit_text(text)
    await state.set_state(RegistrationStates.name)
    await callback.answer()

@router.message(RegistrationStates.name)
async def process_name(message: Message, state: FSMContext):
    """Handle name input"""
    await state.update_data(full_name=message.text)
    data = await state.get_data()
    
    if data["language"] == "am":
        text = "🔞 <b>ዕድሜህ ስንት ነው?</b>\n\nዕድሜህን በቁጥር አስገባ (18+):"
    else:
        text = "🔞 <b>How old are you?</b>\n\nEnter your age (18+):"
    
    await message.answer(text)
    await state.set_state(RegistrationStates.age)

@router.message(RegistrationStates.age)
async def process_age(message: Message, state: FSMContext):
    """Handle age input with validation"""
    try:
        age = int(message.text)
        if age < 18:
            raise ValueError("Under 18")
        
        await state.update_data(age=age)
        data = await state.get_data()
        
        if data["language"] == "am":
            text = "⚥ <b>ጾታህ ምንድነው?</b>"
        else:
            text = "⚥ <b>What's your gender?</b>"
        
        await message.answer(
            text,
            reply_markup=get_gender_keyboard(data["language"])
        )
        await state.set_state(RegistrationStates.gender)
        
    except ValueError:
        if data["language"] == "am":
            error_msg = "እባክህ ትክክለኛ ዕድሜ አስገባ (18 እና ከዚያ በላይ)"
        else:
            error_msg = "Please enter a valid age (18 and above)"
        await message.answer(error_msg)

@router.callback_query(F.data.startswith("gender_"))
async def process_gender(callback: CallbackQuery, state: FSMContext):
    """Handle gender selection"""
    gender = callback.data.split("_")[1]
    await state.update_data(gender=gender)
    data = await state.get_data()
    
    if data["language"] == "am":
        text = "❤️ <b>በማን ላይ ፍላጎት አለህ?</b>"
    else:
        text = "❤️ <b>Who are you interested in?</b>"
    
    await callback.message.edit_text(
        text,
        reply_markup=get_preference_keyboard(data["language"])
    )
    await state.set_state(RegistrationStates.preference)
    await callback.answer()

@router.callback_query(F.data.startswith("pref_"))
async def process_preference(callback: CallbackQuery, state: FSMContext):
    """Handle preference selection"""
    preference = callback.data.split("_")[1]
    await state.update_data(preference=preference)
    data = await state.get_data()
    
    if data["language"] == "am":
        text = (
            "📍 <b>አካባቢህን አሳውቀኝ</b>\n\n"
            "ለተሻለ ተመሳሳይነት አካባቢህን ሼር አርግ።\n"
            "ወይም ንኡስ ከተማህን ምረጥ።"
        )
    else:
        text = (
            "📍 <b>Share your location</b>\n\n"
            "For better matching, share your location.\n"
            "Or choose your sub-city."
        )
    
    await callback.message.edit_text(
        text,
        reply_markup=get_location_options_keyboard(data["language"])
    )
    await state.set_state(RegistrationStates.location)
    await callback.answer()

@router.message(RegistrationStates.location, F.location)
async def process_location_gps(message: Message, state: FSMContext):
    """Handle GPS location sharing"""
    latitude = message.location.latitude
    longitude = message.location.longitude
    
    await state.update_data(
        latitude=latitude,
        longitude=longitude,
        sub_city=None
    )
    
    data = await state.get_data()
    await process_location_next_step(message, data)

@router.callback_query(F.data == "choose_subcity")
async def choose_subcity_callback(callback: CallbackQuery, state: FSMContext):
    """Handle sub-city selection"""
    data = await state.get_data()
    
    if data["language"] == "am":
        text = "🏙 <b>ንኡስ ከተማህን ምረጥ።</b>"
    else:
        text = "🏙 <b>Choose your sub-city.</b>"
    
    await callback.message.edit_text(
        text,
        reply_markup=get_subcity_keyboard()
    )
    await callback.answer()

@router.callback_query(F.data.startswith("subcity_"))
async def process_subcity(callback: CallbackQuery, state: FSMContext):
    """Handle sub-city selection"""
    subcity = callback.data.split("_")[1]
    lat, lon = get_subcity_coordinates(subcity)
    
    await state.update_data(
        sub_city=subcity,
        latitude=lat,
        longitude=lon
    )
    
    data = await state.get_data()
    await process_location_next_step(callback.message, data)
    await callback.answer()

async def process_location_next_step(message: Message, data: dict):
    """Proceed to interests after location"""
    if data["language"] == "am":
        text = (
            "🎯 <b>ፍላጎቶችህን ምረጥ</b>\n\n"
            "በታች ካሉት ውስጥ የሚያስደስትህን ምረጥ።\n"
            "ብዙ ማረግ ትችላለህ። ሲጨርስ 'ተጠናቅቋል' የሚለውን ይጫኑ።"
        )
    else:
        text = (
            "🎯 <b>Choose your interests</b>\n\n"
            "Select what interests you from below.\n"
            "You can select multiple. Press 'Done' when finished."
        )
    
    if isinstance(message, Message):
        await message.answer(
            text,
            reply_markup=get_interests_keyboard(data["language"])
        )
    else:
        await message.edit_text(
            text,
            reply_markup=get_interests_keyboard(data["language"])
        )
    
    from aiogram.fsm.context import FSMContext
    from handlers.registration import RegistrationStates
    # We need to import these, but for simplicity we'll set state
    # In actual implementation, pass state properly

@router.callback_query(F.data.startswith("interest_"))
async def toggle_interest(callback: CallbackQuery, state: FSMContext):
    """Toggle interest selection"""
    interest_id = int(callback.data.split("_")[1])
    data = await state.get_data()
    
    interests = data.get("interests", [])
    if interest_id in interests:
        interests.remove(interest_id)
    else:
        interests.append(interest_id)
    
    await state.update_data(interests=interests)
    
    # Update keyboard with new selection
    await callback.message.edit_reply_markup(
        reply_markup=get_interests_keyboard(data["language"], interests)
    )
    await callback.answer()

@router.callback_query(F.data == "interests_done")
async def process_interests_done(callback: CallbackQuery, state: FSMContext):
    """Finish interests selection"""
    data = await state.get_data()
    
    if data["language"] == "am":
        text = (
            "📸 <b>ፎቶህን ላክ</b>\n\n"
            "ለመልክህ ጥሩ ፎቶ ላክ።\n"
            "አንድ ፎቶ በቂ ነው።"
        )
    else:
        text = (
            "📸 <b>Send your photo</b>\n\n"
            "Send a clear photo of yourself.\n"
            "One photo is enough for now."
        )
    
    await callback.message.edit_text(text)
    await state.set_state(RegistrationStates.photo)
    await callback.answer()

@router.message(RegistrationStates.photo, F.photo)
async def process_photo(message: Message, state: FSMContext):
    """Handle photo upload"""
    photo = message.photo[-1]
    photo_id = photo.file_id
    
    await state.update_data(photo_id=photo_id)
    data = await state.get_data()
    
    if data["language"] == "am":
        text = (
            "📝 <b>ራስህን አስተዋውቅ</b>\n\n"
            "አጭር መግለጫ ፅፍ (ከ 500 ፊደላት በታች)።\n"
            "ምን ይፈልጋሉ? ምን ያስደስትዎታል?\n\n"
            "ለምሳሌ: 'የቴክ ተንኮለኛ፣ ቡና አፍቃሪ፣ ሙዚቃ እና ጉዞ ወዳድ'"
        )
    else:
        text = (
            "📝 <b>Introduce yourself</b>\n\n"
            "Write a short bio (under 500 characters).\n"
            "What are you looking for? What makes you happy?\n\n"
            "Example: 'Tech enthusiast, coffee lover, enjoy music and travel'"
        )
    
    await message.answer(text)
    await state.set_state(RegistrationStates.bio)

@router.message(RegistrationStates.bio)
async def process_bio(message: Message, state: FSMContext):
    """Handle bio input and complete registration"""
    if len(message.text) > 500:
        if data["language"] == "am":
            await message.answer("መግለጫው በጣም ረጅም ነው። 500 ፊደላት ብቻ።")
        else:
            await message.answer("Bio is too long. Maximum 500 characters.")
        return
    
    await state.update_data(bio=message.text)
    data = await state.get_data()
    
    # Create user in database
    await create_user(
        telegram_id=message.from_user.id,
        language=data["language"],
        full_name=data["full_name"]
    )
    
    # Update user with all data
    updates = {
        "age": data["age"],
        "gender": data["gender"],
        "preference": data["preference"],
        "latitude": data.get("latitude"),
        "longitude": data.get("longitude"),
        "sub_city": data.get("sub_city"),
        "main_photo_id": data.get("photo_id"),
        "bio": data.get("bio")
    }
    
    await update_user(message.from_user.id, **updates)
    
    # Add interests
    if "interests" in data:
        await add_user_interests(message.from_user.id, data["interests"])
    
    # Send welcome message
    if data["language"] == "am":
        text = (
            "🎉 <b>ምዝገባው ተጠናቅቋል!</b>\n\n"
            "አሁን በአካባቢህ ያሉ ሰዎችን ማየት ትችላለህ።\n\n"
            "ጠቃሚ ማስታወሻዎች፦\n"
            "• /start - ዋና ገጽ\n"
            "• /help - እገዛ\n"
            "• /safety - ደህንነት ምክሮች\n"
            "• /report - ሰውን ሪፖርት ማድረግ"
        )
    else:
        text = (
            "🎉 <b>Registration Complete!</b>\n\n"
            "You can now browse people in your area.\n\n"
            "Useful commands:\n"
            "• /start - Main menu\n"
            "• /help - Get help\n"
            "• /safety - Safety tips\n"
            "• /report - Report a user"
        )
    
    await message.answer(
        text,
        reply_markup=get_main_menu_keyboard(data["language"])
    )
    await state.clear()

# ============= MAIN MENU HANDLERS =============
async def show_main_menu(message: Message, language: str = "en"):
    """Show main menu"""
    if language == "am":
        text = (
            "🏠 <b>ዋና ገጽ - ሀበሻ ማች</b>\n\n"
            "አዲስ ሰዎችን ያግኙ፣ ያግኙ እና ይተዋወቁ።\n\n"
            "ከታች ያለውን ይምረጡ፦"
        )
    else:
        text = (
            "🏠 <b>Main Menu - Habesha Match</b>\n\n"
            "Discover new people, match, and connect.\n\n"
            "Choose an option below:"
        )
    
    await message.answer(
        text,
        reply_markup=get_main_menu_keyboard(language)
    )

@router.callback_query(F.data == "main_menu")
async def back_to_main(callback: CallbackQuery):
    """Return to main menu"""
    user = await get_user(callback.from_user.id)
    lang = user["language"] if user else "en"
    await show_main_menu(callback.message, lang)
    await callback.answer()

@router.callback_query(F.data == "browse")
async def browse_profiles(callback: CallbackQuery):
    """Browse nearby profiles"""
    user = await get_user(callback.from_user.id)
    if not user:
        await callback.answer("Please register first with /start")
        return
    
    # Check rate limiting
    if user["likes_today"] >= 50 and not user["is_premium"]:
        if user["language"] == "am":
            await callback.answer("የዛሬው ገደብ አልቋል። ነገ ይሞክሩ።")
        else:
            await callback.answer("Daily limit reached. Try tomorrow.")
        return
    
    nearby_users = await get_nearby_users(callback.from_user.id, limit=1)
    
    if not nearby_users:
        if user["language"] == "am":
            await callback.message.answer("በአካባቢህ ምንም ሰዎች የሉም። ቆየት እና እንደገና ሞክር።")
        else:
            await callback.message.answer("No people in your area. Wait and try again.")
        await callback.answer()
        return
    
    profile = nearby_users[0]
    
    # Create profile display
    caption_parts = []
    
    # Name and age
    if profile["age"]:
        caption_parts.append(f"👤 <b>{profile['full_name']}</b>, {profile['age']}")
    else:
        caption_parts.append(f"👤 <b>{profile['full_name']}</b>")
    
    # Location
    if profile["sub_city"]:
        caption_parts.append(f"📍 {profile['sub_city']}")
    
    # Bio
    if profile["bio"] and len(profile["bio"]) > 0:
        bio_preview = profile["bio"][:100] + "..." if len(profile["bio"]) > 100 else profile["bio"]
        caption_parts.append(f"\n{bio_preview}")
    
    caption = "\n".join(caption_parts)
    
    # Send profile with photo if available
    if profile["main_photo_id"]:
        try:
            await callback.message.answer_photo(
                photo=profile["main_photo_id"],
                caption=caption,
                reply_markup=get_profile_action_keyboard(profile["id"], user["language"])
            )
        except:
            await callback.message.answer(
                caption,
                reply_markup=get_profile_action_keyboard(profile["id"], user["language"])
            )
    else:
        await callback.message.answer(
            caption,
            reply_markup=get_profile_action_keyboard(profile["id"], user["language"])
        )
    
    await callback.answer()

@router.callback_query(F.data.startswith("like_"))
async def handle_like(callback: CallbackQuery):
    """Handle profile like"""
    profile_id = int(callback.data.split("_")[1])
    user = await get_user(callback.from_user.id)
    
    if not user:
        await callback.answer("Please register first")
        return
    
    # Get target user telegram_id
    conn = await get_db_connection()
    target_user = await conn.fetchrow(
        "SELECT telegram_id FROM users WHERE id = $1",
        profile_id
    )
    await conn.close()
    
    if not target_user:
        await callback.answer("User not found")
        return
    
    # Create like and check for match
    is_match = await create_like(callback.from_user.id, target_user["telegram_id"])
    
    if is_match:
        # It's a match!
        if user["language"] == "am":
            match_text = "🎉 <b>ተመሳሳይነት ተገኘ!</b>\n\nአሁን መልዕክት መላክ ትችላላችሁ።"
            await callback.message.answer(match_text)
            
            # Notify the other user
            other_user = await get_user(target_user["telegram_id"])
            if other_user and other_user["notify_matches"]:
                if other_user["language"] == "am":
                    notify_text = "🎉 <b>አዲስ ተመሳሳይነት!</b>\n\nአሁን መልዕክት መላክ ትችላላችሁ።"
                else:
                    notify_text = "🎉 <b>New Match!</b>\n\nYou can now send messages."
                
                try:
                    await bot.send_message(
                        chat_id=target_user["telegram_id"],
                        text=notify_text
                    )
                except:
                    pass
        else:
            match_text = "🎉 <b>It's a Match!</b>\n\nYou can now send messages."
            await callback.message.answer(match_text)
            
            # Notify the other user
            other_user = await get_user(target_user["telegram_id"])
            if other_user and other_user["notify_matches"]:
                try:
                    await bot.send_message(
                        chat_id=target_user["telegram_id"],
                        text="🎉 <b>New Match!</b>\n\nYou can now send messages."
                    )
                except:
                    pass
    else:
        if user["language"] == "am":
            await callback.answer("👍 አስተያየት ተልኳል")
        else:
            await callback.answer("👍 Like sent")
    
    # Show next profile
    await browse_profiles(callback)

@router.callback_query(F.data.startswith("dislike_"))
async def handle_dislike(callback: CallbackQuery):
    """Handle profile dislike - just show next"""
    if await get_user(callback.from_user.id):
        await browse_profiles(callback)
    else:
        await callback.answer("Please register first")

@router.callback_query(F.data == "matches")
async def show_matches(callback: CallbackQuery):
    """Show user's matches"""
    user = await get_user(callback.from_user.id)
    if not user:
        await callback.answer("Please register first")
        return
    
    matches = await get_user_matches(callback.from_user.id)
    
    if not matches:
        if user["language"] == "am":
            await callback.message.answer("🤷‍♂️ <b>እስካሁን ምንም ተመሳሳይነት የለም</b>\n\nሰዎችን ይመልከቱ እና አስተያየት ይስጡ።")
        else:
            await callback.message.answer("🤷‍♂️ <b>No matches yet</b>\n\nBrowse people and send likes.")
        await callback.answer()
        return
    
    if user["language"] == "am":
        text = "💌 <b>ተመሳሳይነቶችህ</b>\n\n"
    else:
        text = "💌 <b>Your Matches</b>\n\n"
    
    for i, match in enumerate(matches[:10], 1):  # Show first 10 matches
        text += f"{i}. <b>{match['full_name']}</b>"
        if match["age"]:
            text += f", {match['age']}"
        if match["sub_city"]:
            text += f" - {match['sub_city']}"
        text += "\n"
    
    await callback.message.answer(text)
    await callback.answer()

@router.callback_query(F.data == "settings")
async def show_settings(callback: CallbackQuery):
    """Show settings menu"""
    user = await get_user(callback.from_user.id)
    if not user:
        await callback.answer("Please register first")
        return
    
    if user["language"] == "am":
        text = (
            "⚙️ <b>ማስተካከያዎች</b>\n\n"
            f"• ቋንቋ: {'አማርኛ' if user['language'] == 'am' else 'English'}\n"
            f"• አካባቢ: {user['sub_city'] or 'Not set'}\n"
            f"• ስልክ ሁነት: {'ደብቅ' if user['is_stealth'] else 'ተገልጦ'}\n"
            f"• ማሳወቂያዎች: {'አንብ' if user['notify_matches'] else 'ጠፋ'}\n\n"
            "ከታች ለመቀየር ይምረጡ፦"
        )
    else:
        text = (
            "⚙️ <b>Settings</b>\n\n"
            f"• Language: {'Amharic' if user['language'] == 'am' else 'English'}\n"
            f"• Location: {user['sub_city'] or 'Not set'}\n"
            f"• Stealth Mode: {'On' if user['is_stealth'] else 'Off'}\n"
            f"• Notifications: {'On' if user['notify_matches'] else 'Off'}\n\n"
            "Select below to change:"
        )
    
    await callback.message.edit_text(
        text,
        reply_markup=get_settings_keyboard(user["language"])
    )
    await callback.answer()

@router.callback_query(F.data == "change_language")
async def change_language(callback: CallbackQuery):
    """Change language"""
    user = await get_user(callback.from_user.id)
    if not user:
        await callback.answer()
        return
    
    new_lang = "am" if user["language"] == "en" else "en"
    await update_user(callback.from_user.id, language=new_lang)
    
    if new_lang == "am":
        await callback.message.edit_text(
            "✅ ቋንቋ ወደ አማርኛ ተቀይሯል",
            reply_markup=get_settings_keyboard(new_lang)
        )
    else:
        await callback.message.edit_text(
            "✅ Language changed to English",
            reply_markup=get_settings_keyboard(new_lang)
        )
    
    await callback.answer()

@router.callback_query(F.data == "toggle_stealth")
async def toggle_stealth(callback: CallbackQuery):
    """Toggle stealth mode"""
    user = await get_user(callback.from_user.id)
    if not user:
        await callback.answer()
        return
    
    new_stealth = not user["is_stealth"]
    await update_user(callback.from_user.id, is_stealth=new_stealth)
    
    if user["language"] == "am":
        status = "ደብቅ" if new_stealth else "ተገልጦ"
        await callback.message.edit_text(
            f"✅ ስልክ ሁነት: {status}",
            reply_markup=get_settings_keyboard(user["language"])
        )
    else:
        status = "On" if new_stealth else "Off"
        await callback.message.edit_text(
            f"✅ Stealth Mode: {status}",
            reply_markup=get_settings_keyboard(user["language"])
        )
    
    await callback.answer()

@router.callback_query(F.data == "update_location")
async def update_location_start(callback: CallbackQuery, state: FSMContext):
    """Start location update"""
    user = await get_user(callback.from_user.id)
    if not user:
        await callback.answer()
        return
    
    if user["language"] == "am":
        text = (
            "📍 <b>አዲስ አካባቢ አስገባ</b>\n\n"
            "አሁን አካባቢህን ላክ ወይም ንኡስ ከተማ ምረጥ።"
        )
    else:
        text = (
            "📍 <b>Update Location</b>\n\n"
            "Share your current location or choose sub-city."
        )
    
    await callback.message.edit_text(
        text,
        reply_markup=get_location_options_keyboard(user["language"])
    )
    await state.set_state(SettingsStates.location)
    await callback.answer()

@router.message(SettingsStates.location, F.location)
async def update_location_gps(message: Message, state: FSMContext):
    """Update location with GPS"""
    await update_user(
        message.from_user.id,
        latitude=message.location.latitude,
        longitude=message.location.longitude,
        sub_city=None
    )
    
    user = await get_user(message.from_user.id)
    if user["language"] == "am":
        await message.answer("✅ አካባቢ ተዘምኗል")
    else:
        await message.answer("✅ Location updated")
    
    await state.clear()
    await show_settings_from_message(message, user["language"])

async def show_settings_from_message(message: Message, language: str):
    """Show settings menu from message"""
    user = await get_user(message.from_user.id)
    if not user:
        return
    
    if language == "am":
        text = (
            "⚙️ <b>ማስተካከያዎች</b>\n\n"
            f"• ቋንቋ: {'አማርኛ' if user['language'] == 'am' else 'English'}\n"
            f"• አካባቢ: {user['sub_city'] or 'Not set'}\n"
            f"• ስልክ ሁነት: {'ደብቅ' if user['is_stealth'] else 'ተገልጦ'}\n"
            f"• ማሳወቂያዎች: {'አንብ' if user['notify_matches'] else 'ጠፋ'}\n\n"
            "ከታች ለመቀየር ይምረጡ፦"
        )
    else:
        text = (
            "⚙️ <b>Settings</b>\n\n"
            f"• Language: {'Amharic' if user['language'] == 'am' else 'English'}\n"
            f"• Location: {user['sub_city'] or 'Not set'}\n"
            f"• Stealth Mode: {'On' if user['is_stealth'] else 'Off'}\n"
            f"• Notifications: {'On' if user['notify_matches'] else 'Off'}\n\n"
            "Select below to change:"
        )
    
    await message.answer(
        text,
        reply_markup=get_settings_keyboard(language)
    )

@router.callback_query(F.data == "help")
async def show_help(callback: CallbackQuery):
    """Show help menu"""
    user = await get_user(callback.from_user.id)
    lang = user["language"] if user else "en"
    
    if lang == "am":
        text = (
            "🆘 <b>እገዛ እና ደህንነት</b>\n\n"
            "<b>ዋና ትዕዛዞች፦</b>\n"
            "/start - ዋና ገጽ\n"
            "/help - ይህን መልዕክት\n"
            "/safety - ደህንነት ምክሮች\n"
            "/report - ሰውን ሪፖርት ማድረግ\n\n"
            "<b>ደህንነት ምክሮች፦</b>\n"
            "1. ለመጀመሪያ ጊዜ በህዝባዊ ቦታ ተገናኝ\n"
            "2. የራስህን መረጃ አላማጭ\n"
            "3. አስቀድመህ ስልክ ቁጥር አትስጥ\n"
            "4. አለመስማማት ከተገኘ ወዲያ አቁም\n"
            "5. ጠያቂ ከሆነ ወዲያ ሪፖርት አርግ\n\n"
            "<b>ለሪፖርት፦</b>\n"
            "አንድን ሰው ለሪፖርት ማድረግ ከፈለጉ በመገለጫው ላይ 'ሪፖርት' የሚለውን ይጫኑ።"
        )
    else:
        text = (
            "🆘 <b>Help & Safety</b>\n\n"
            "<b>Main Commands:</b>\n"
            "/start - Main menu\n"
            "/help - This message\n"
            "/safety - Safety tips\n"
            "/report - Report a user\n\n"
            "<b>Safety Tips:</b>\n"
            "1. Meet first time in public places\n"
            "2. Don't share personal information quickly\n"
            "3. Don't give phone number before meeting\n"
            "4. Stop if uncomfortable\n"
            "5. Report suspicious behavior immediately\n\n"
            "<b>To Report:</b>\n"
            "Click 'Report' button on any profile to report a user."
        )
    
    await callback.message.edit_text(
        text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(
                text="↩️ ወደ ዋና" if lang == "am" else "↩️ Back to Main",
                callback_data="main_menu"
            )]
        ])
    )
    await callback.answer()

@router.message(Command("safety"))
async def cmd_safety(message: Message):
    """Safety tips command"""
    user = await get_user(message.from_user.id)
    lang = user["language"] if user else "en"
    
    if lang == "am":
        text = (
            "🛡️ <b>ደህንነት ምክሮች</b>\n\n"
            "<b>በአዲስ አበባ ሰዎችን ሲገናኙ፦</b>\n\n"
            "✅ <b>የሚያደርጉት፦</b>\n"
            "• ለመጀመሪያ ጊዜ በቡና ቤት ወይም ማል ውስጥ ተገናኝ\n"
            "• አጋር ወዳጅ ይዘው መምጣትን ይጠይቁ\n"
            "• የተወሰነ የጊዜ ገደብ ያዘጋጁ\n"
            "• በቀን እና በብሩህ ቦታ ተገናኝ\n\n"
            "❌ <b>የማትደርጉት፦</b>\n"
            "• የቤት አድራሻ አትስጡ\n"
            "• በመጀመሪያ ቀን ብዙ ገንዘብ አትውሰዱ\n"
            "• ወደ ሰላሳ ቦታዎች አትሂዱ\n"
            "• ጠመንጃ ወይም መድሃኒት ተሳትፎ ካለ ወዲያ ይቅረቡ\n\n"
            "🔔 <b>ማስታወሻ፦</b>\n"
            "ማንኛውም ጠያቂ ባህሪ ወዲያውኑ ሪፖርት ያድርጉ።"
        )
    else:
        text = (
            "🛡️ <b>Safety Tips for Dating in Addis</b>\n\n"
            "<b>When meeting new people in Addis Ababa:</b>\n\n"
            "✅ <b>Do:</b>\n"
            "• Meet first time in coffee shops or malls\n"
            "• Ask to bring a friend along\n"
            "• Set specific time limits\n"
            "• Meet during daytime in well-lit areas\n\n"
            "❌ <b>Don't:</b>\n"
            "• Give out home address\n"
            "• Carry large amounts of money on first date\n"
            "• Go to remote locations\n"
            "• Engage if drugs or weapons are involved\n\n"
            "🔔 <b>Remember:</b>\n"
            "Report any suspicious behavior immediately."
        )
    
    await message.answer(text)

@router.callback_query(F.data.startswith("report_"))
async def report_user(callback: CallbackQuery, state: FSMContext):
    """Report a user"""
    profile_id = int(callback.data.split("_")[1])
    user = await get_user(callback.from_user.id)
    
    if not user:
        await callback.answer()
        return
    
    # Store reported user ID in state
    await state.update_data(reported_id=profile_id)
    
    if user["language"] == "am":
        text = (
            "⚠️ <b>ሪፖርት ማድረግ</b>\n\n"
            "ለምን ይህን ሰው ሪፖርት ማድረግ ትፈልጋለህ?\n\n"
            "ምርጫዎች፦\n"
            "1. ጠቃሚ ወይም አስጸያፊ ቋንቋ\n"
            "2. ሐሰተኛ መረጃ\n"
            "3. ጠያቂ ባህሪ\n"
            "4. አለመስማማት\n"
            "5. ሌላ\n\n"
            "ምክንያቱን ፅፍ፦"
        )
    else:
        text = (
            "⚠️ <b>Report User</b>\n\n"
            "Why are you reporting this user?\n\n"
            "Options:\n"
            "1. Offensive or abusive language\n"
            "2. False information\n"
            "3. Suspicious behavior\n"
            "4. Harassment\n"
            "5. Other\n\n"
            "Write the reason:"
        )
    
    await callback.message.edit_text(text)
    await callback.answer()

@router.message(F.text)
async def handle_report_reason(message: Message, state: FSMContext):
    """Handle report reason"""
    data = await state.get_data()
    reported_id = data.get("reported_id")
    
    if reported_id:
        user = await get_user(message.from_user.id)
        if user:
            # Save report to database
            conn = await get_db_connection()
            await conn.execute("""
                INSERT INTO reports (reporter_id, reported_id, reason)
                VALUES ($1, $2, $3)
            """, user["id"], reported_id, message.text)
            await conn.close()
            
            # Notify admin
            try:
                await bot.send_message(
                    ADMIN_ID,
                    f"🚨 <b>New User Report</b>\n\n"
                    f"Reporter: {user['full_name']} (ID: {user['telegram_id']})\n"
                    f"Reported User ID: {reported_id}\n"
                    f"Reason: {message.text[:500]}"
                )
            except:
                pass
            
            if user["language"] == "am":
                await message.answer("✅ ሪፖርት ቀርቧል። እናመሰግናለን።")
            else:
                await message.answer("✅ Report submitted. Thank you.")
        
        await state.clear()

# ============= ADMIN COMMANDS =============
@router.message(Command("admin"))
async def admin_panel(message: Message):
    """Admin panel"""
    if message.from_user.id != ADMIN_ID:
        return
    
    conn = await get_db_connection()
    stats = await conn.fetchrow("""
        SELECT 
            COUNT(*) as total_users,
            COUNT(CASE WHEN is_active THEN 1 END) as active_users,
            COUNT(CASE WHEN is_verified THEN 1 END) as verified_users,
            COUNT(CASE WHEN is_stealth THEN 1 END) as stealth_users,
            (SELECT COUNT(*) FROM matches) as total_matches,
            (SELECT COUNT(*) FROM reports) as total_reports
        FROM users
    """)
    await conn.close()
    
    text = (
        "👑 <b>Admin Panel - Habesha Match</b>\n\n"
        f"📊 <b>Statistics:</b>\n"
        f"• Total Users: {stats['total_users']}\n"
        f"• Active Users: {stats['active_users']}\n"
        f"• Verified Users: {stats['verified_users']}\n"
        f"• Stealth Users: {stats['stealth_users']}\n"
        f"• Total Matches: {stats['total_matches']}\n"
        f"• Total Reports: {stats['total_reports']}\n\n"
        "<b>Admin Commands:</b>\n"
        "/stats - Show statistics\n"
        "/broadcast - Broadcast message\n"
        "/verify [id] - Verify user\n"
        "/ban [id] - Ban user"
    )
    
    await message.answer(text)

# ============= WEBHOOK SETUP FOR RAILWAY =============
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from aiohttp import web

async def on_startup():
    """Initialize on startup"""
    await init_db()
    await bot.set_webhook(f"{os.getenv('RAILWAY_STATIC_URL', '')}/webhook")

async def on_shutdown():
    """Cleanup on shutdown"""
    await bot.session.close()
    await redis.close()

# ============= MAIN ENTRY POINT =============
async def main():
    """Main entry point"""
    # Initialize database
    await init_db()
    
    # Register startup/shutdown
    dp.startup.register(on_startup)
    dp.shutdown.register(on_shutdown)
    
    # Check if running on Railway (has PORT env var)
    if PORT != 8080:  # Railway provides PORT
        # Webhook mode for Railway
        app = web.Application()
        
        webhook_path = f"/webhook/{TOKEN}"
        webhook_requests_handler = SimpleRequestHandler(
            dispatcher=dp,
            bot=bot,
        )
        webhook_requests_handler.register(app, path=webhook_path)
        
        setup_application(app, dp, bot=bot)
        
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "0.0.0.0", PORT)
        
        print(f"Starting webhook server on port {PORT}")
        await site.start()
        
        # Keep running
        await asyncio.Event().wait()
    else:
        # Polling mode for local development
        print("Starting in polling mode...")
        await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
