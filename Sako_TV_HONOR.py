import logging
import asyncio
import sqlite3
import os

from aiogram import Bot, Dispatcher, F, BaseMiddleware
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton
)

logging.basicConfig(level=logging.INFO)

# =========================================================
# TOKEN
# =========================================================


TOKEN = os.environ["TOKEN"]

bot = Bot(token=TOKEN)
dp = Dispatcher()

# =========================================================
# SUBSCRIPTION GUARD
# =========================================================
# Проверяем подписку не только при /start, но и при каждом
# повторном использовании кнопок/вводе текста. Поэтому если
# пользователь отписался после первого входа, доступ снова
# закрывается до повторной подписки на все 3 канала.

class SubscriptionGuardMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        user_id = getattr(getattr(event, "from_user", None), "id", None)

        if not user_id:
            return await handler(event, data)

        # /start и кнопка проверки подписки должны проходить
        # без блокировки, потому что они сами запускают проверку.
        if isinstance(event, Message):
            text = event.text or ""
            if text.startswith("/start"):
                return await handler(event, data)

        if isinstance(event, CallbackQuery):
            if event.data == "check_sub":
                return await handler(event, data)

        if not await check_subscriptions(user_id):
            try:
                if isinstance(event, CallbackQuery):
                    await event.answer(
                        "❌ Сначала подпишитесь на все 3 канала!",
                        show_alert=True
                    )
                    if event.message:
                        await event.message.edit_text(
                            "❌ Доступ закрыт!\n\n"
                            "Подпишитесь на все 3 канала и нажмите "
                            "«✅ Я подписался!».",
                            reply_markup=get_sub_keyboard()
                        )
                elif isinstance(event, Message):
                    await event.answer(
                        "❌ Для использования бота вы должны "
                        "быть подписаны на все 3 канала!",
                        reply_markup=get_sub_keyboard()
                    )
            except Exception as e:
                logging.warning(f"Subscription guard response error: {e}")
            return

        return await handler(event, data)

# =========================================================
# CHANNELS
# =========================================================

CHANNELS = [
    "-1003320377098",
    "-1003847355908",
    "-1003761985839"
]

# =========================================================
# SPECIAL OPPO / VIVO SETTINGS
# =========================================================
# Support/admin username. Can be changed in Railway Variables.
SPECIAL_ADMIN_USERNAME = os.getenv(
    "SPECIAL_ADMIN_USERNAME",
    "isakovffx"
)

# New channel for special-order notifications.
# Example Railway Variable: SPECIAL_ORDER_CHANNEL=@your_channel
SPECIAL_ORDER_CHANNEL = os.getenv(
    "SPECIAL_ORDER_CHANNEL",
    ""
)

# =========================================================
# DATABASE
# =========================================================

conn = sqlite3.connect("bot_users.db")
cursor = conn.cursor()

cursor.execute("""
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    referrer_id INTEGER,
    invites_count INTEGER DEFAULT 0,
    referral_confirmed INTEGER DEFAULT 0,
    special_active INTEGER DEFAULT 0,
    special_brand TEXT,
    special_model TEXT,
    special_start_invites INTEGER DEFAULT 0,
    special_completed INTEGER DEFAULT 0
)
""")

# Для старой базы добавляем новый столбец
try:
    cursor.execute(
        "ALTER TABLE users ADD COLUMN referral_confirmed INTEGER DEFAULT 0"
    )
except sqlite3.OperationalError:
    pass

# Новые поля для OPPO/Vivo специальной настройки.
for column_sql in [
    "ALTER TABLE users ADD COLUMN special_active INTEGER DEFAULT 0",
    "ALTER TABLE users ADD COLUMN special_brand TEXT",
    "ALTER TABLE users ADD COLUMN special_model TEXT",
    "ALTER TABLE users ADD COLUMN special_start_invites INTEGER DEFAULT 0",
    "ALTER TABLE users ADD COLUMN special_completed INTEGER DEFAULT 0"
]:
    try:
        cursor.execute(column_sql)
    except sqlite3.OperationalError:
        pass

conn.commit()

# =========================================================
# SUBSCRIPTION CHECK
# =========================================================

async def check_subscriptions(user_id: int) -> bool:

    for channel in CHANNELS:

        try:
            member = await bot.get_chat_member(
                chat_id=channel,
                user_id=user_id
            )

            if member.status in ["left", "kicked"]:
                return False

        except Exception as e:
            logging.warning(
                f"Subscription check error {channel}: {e}"
            )
            return False

    return True

# Вешаем глобальную проверку после определения check_subscriptions.
# Она действует на все сообщения/колбэки, кроме /start и check_sub.
dp.message.outer_middleware(SubscriptionGuardMiddleware())
dp.callback_query.outer_middleware(SubscriptionGuardMiddleware())

# =========================================================
# OPPO / VIVO SPECIAL REQUEST
# =========================================================

def get_support_keyboard():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🆘 Поддержка",
                    url=f"https://t.me/{SPECIAL_ADMIN_USERNAME.lstrip('@')}"
                )
            ]
        ]
    )


def get_special_main_keyboard():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="⚙️ Особенная настройка",
                    callback_data="special_start"
                ),
                InlineKeyboardButton(
                    text="🆘 Поддержка",
                    url=f"https://t.me/{SPECIAL_ADMIN_USERNAME.lstrip('@')}"
                )
            ]
        ]
    )


def get_special_progress_keyboard():
    # В режиме «Особенная настройка» кнопки поддержки нет.
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="⬅️ Назад к брендам",
                    callback_data="back_to_brands"
                )
            ]
        ]
    )


async def complete_special_request(user_id: int):
    """
    Завершает специальную заявку после 5 НОВЫХ подтверждённых invite.
    Invite для обычных тарифов не списываются.
    """
    cursor.execute(
        """
        SELECT special_active,
               special_brand,
               special_model,
               special_start_invites,
               special_completed
        FROM users
        WHERE user_id = ?
        """,
        (user_id,)
    )
    row = cursor.fetchone()

    if not row:
        return False

    (
        special_active,
        special_brand,
        special_model,
        special_start_invites,
        special_completed
    ) = row

    if (
        not special_active
        or special_completed
        or not special_model
    ):
        return False

    cursor.execute(
        """
        SELECT invites_count
        FROM users
        WHERE user_id = ?
        """,
        (user_id,)
    )
    invite_row = cursor.fetchone()
    current_invites = invite_row[0] if invite_row else 0

    start_invites = special_start_invites or 0

    if current_invites - start_invites < 5:
        return False

    # Сначала фиксируем выполнение, чтобы повторные проверки
    # не отправляли одну и ту же заявку несколько раз.
    cursor.execute(
        """
        UPDATE users
        SET special_active = 0,
            special_completed = 1
        WHERE user_id = ?
        AND special_active = 1
        AND special_completed = 0
        """,
        (user_id,)
    )

    if cursor.rowcount == 0:
        return False

    conn.commit()

    # Берём username именно того пользователя, который сделал заявку.
    try:
        chat = await bot.get_chat(user_id)
        username = (
            f"@{chat.username}"
            if chat.username
            else "без username"
        )
        first_name = chat.first_name or "Без имени"
    except Exception:
        username = (
            f"@{SPECIAL_ADMIN_USERNAME}"
            if False else "без username"
        )
        first_name = "Без имени"

    model = special_model.strip()
    brand = (special_brand or "").upper()
    if model.lower().startswith("oppo"):
        brand = "OPPO"
    elif model.lower().startswith("vivo"):
        brand = "VIVO"

    order_text = (
        "🔥 НОВАЯ ЗАЯВКА — ОСОБЕННАЯ НАСТРОЙКА\n\n"
        f"👤 Пользователь: {first_name}\n"
        f"🔗 Telegram: {username}\n"
        f"🆔 ID: {user_id}\n"
        f"📱 Бренд: {brand}\n"
        f"📱 Модель: {model}\n"
        "👥 Выполнено: 5 подтверждённых invite"
    )

    # Заявка администратору.
    try:
        await bot.send_message(
            f"@{SPECIAL_ADMIN_USERNAME.lstrip('@')}",
            order_text
        )
    except Exception as e:
        logging.warning(
            f"Could not send special order to admin: {e}"
        )

    # Отдельное уведомление в новый канал.
    if SPECIAL_ORDER_CHANNEL.strip():
        channel_text = (
            "📥 НОВАЯ ЗАЯВКА — ОСОБЕННАЯ НАСТРОЙКА\n\n"
            f"👤 Имя: {first_name}\n"
            f"🔗 Username: {username}\n"
            f"🆔 Telegram ID: {user_id}\n"
            f"📱 Бренд: {brand}\n"
            f"📱 Модель: {model}\n"
            "👥 Invite: 5/5"
        )

        try:
            await bot.send_message(
                SPECIAL_ORDER_CHANNEL.strip(),
                channel_text
            )
        except Exception as e:
            logging.warning(
                f"Could not send special order to channel: {e}"
            )

    # Пользователю.
    try:
        await bot.send_message(
            user_id,
            "✅ 5 invite выполнено!\n\n"
            "📱 Ваша заявка на особенную настройку принята.\n"
            "📨 Скоро наш администратор свяжется с вами."
        )
    except Exception as e:
        logging.warning(
            f"Could not notify special-order user: {e}"
        )

    return True


# =========================================================
# CONFIRM REFERRAL
# =========================================================

async def confirm_referral(user_id: int):

    cursor.execute(
        """
        SELECT referrer_id, referral_confirmed
        FROM users
        WHERE user_id = ?
        """,
        (user_id,)
    )

    referral_data = cursor.fetchone()

    if not referral_data:
        return

    referrer_id, referral_confirmed = referral_data

    # Уже был засчитан
    if not referrer_id or referral_confirmed:
        return

    # Проверяем, существует ли реферер
    cursor.execute(
        """
        SELECT user_id
        FROM users
        WHERE user_id = ?
        """,
        (referrer_id,)
    )

    referrer_exists = cursor.fetchone()

    if not referrer_exists:
        return

    # Засчитываем +1 invite
    cursor.execute(
        """
        UPDATE users
        SET invites_count = invites_count + 1
        WHERE user_id = ?
        """,
        (referrer_id,)
    )

    # Помечаем referral как подтверждённый
    cursor.execute(
        """
        UPDATE users
        SET referral_confirmed = 1
        WHERE user_id = ?
        """,
        (user_id,)
    )

    conn.commit()

    try:
        await bot.send_message(
            referrer_id,
            "🎉 Новый invite подтверждён!\n\n"
            "👤 Пользователь подписался на все 3 канала.\n"
            "📊 +1 invite"
        )
    except Exception:
        pass

    # Если у реферера активна специальная заявка OPPO/Vivo,
    # проверяем, набрал ли он 5 новых подтверждённых invite.
    await complete_special_request(referrer_id)

# =========================================================
# SUBSCRIPTION BUTTONS
# =========================================================

def get_sub_keyboard():

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📢 Подписаться на 1-й Канал",
                    url="https://t.me/ISAKOV_FF"
                )
            ],
            [
                InlineKeyboardButton(
                    text="📢 Подписаться на 2-й Канал",
                    url="https://t.me/isakovv_sale"
                )
            ],
            [
                InlineKeyboardButton(
                    text="📢 Подписаться на 3-й Канал",
                    url="https://t.me/farhodjontv"
                )
            ],
            [
                InlineKeyboardButton(
                    text="✅ Я подписался!",
                    callback_data="check_sub"
                )
            ]
        ]
    )

# =========================================================
# BRANDS
# =========================================================

def get_brands_keyboard():

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📱 iPhone",
                    callback_data="brand_iphone"
                ),
                InlineKeyboardButton(
                    text="📱 Samsung",
                    callback_data="brand_samsung"
                )
            ],
            [
                InlineKeyboardButton(
                    text="📱 Xiaomi",
                    callback_data="brand_xiaomi"
                ),
                InlineKeyboardButton(
                    text="📱 POCO",
                    callback_data="brand_poco"
                )
            ],
            [
                InlineKeyboardButton(
                    text="📱 Realme",
                    callback_data="brand_realme"
                ),
                InlineKeyboardButton(
                    text="📱 Honor",
                    callback_data="brand_honor"
                )
            ],
            [
                InlineKeyboardButton(
                    text="📱 Infinix",
                    callback_data="brand_infinix"
                ),
                InlineKeyboardButton(
                    text="📱 Tecno",
                    callback_data="brand_tecno"
                )
            ],
            [
                InlineKeyboardButton(
                    text="📱 OPPO",
                    callback_data="brand_oppo"
                ),
                InlineKeyboardButton(
                    text="📱 Vivo",
                    callback_data="brand_vivo"
                )
            ],
            [
                InlineKeyboardButton(
                    text="⚙️ Особенная настройка",
                    callback_data="special_start"
                ),
                InlineKeyboardButton(
                    text="🆘 Поддержка",
                    url=f"https://t.me/{SPECIAL_ADMIN_USERNAME.lstrip('@')}"
                )
            ]
        ]
    )

# =========================================================
# IPHONE GROUPS
# =========================================================

IPHONE_GROUPS = {

    "8": [
        ("iPhone 8", "iphone8"),
        ("iPhone 8+", "iphone8plus")
    ],

    "X": [
        ("iPhone X", "iphonex"),
        ("iPhone XR", "iphonexr"),
        ("iPhone Xs", "iphonexs"),
        ("iPhone Xs Max", "iphonexsmax")
    ],

    "11": [
        ("iPhone 11", "iphone11"),
        ("iPhone 11 Pro", "iphone11pro"),
        ("iPhone 11 Pro Max", "iphone11promax")
    ],

    "12": [
        ("iPhone 12", "iphone12"),
        ("iPhone 12 mini", "iphone12mini"),
        ("iPhone 12 Pro", "iphone12pro"),
        ("iPhone 12 Pro Max", "iphone12promax")
    ],

    "13": [
        ("iPhone 13", "iphone13"),
        ("iPhone 13 mini", "iphone13mini"),
        ("iPhone 13 Pro", "iphone13pro"),
        ("iPhone 13 Pro Max", "iphone13promax")
    ],

    "14": [
        ("iPhone 14", "iphone14"),
        ("iPhone 14 Pro", "iphone14pro"),
        ("iPhone 14 Pro Max", "iphone14promax")
    ],

    "15": [
        ("iPhone 15", "iphone15"),
        ("iPhone 15 Plus", "iphone15plus"),
        ("iPhone 15 Pro", "iphone15pro"),
        ("iPhone 15 Pro Max", "iphone15promax"),
        ("iPhone SE 2nd Gen", "iphonese2"),
        ("iPhone SE 3rd Gen", "iphonese3")
    ],

    "16": [
        ("iPhone 16", "iphone16"),
        ("iPhone 16 Plus", "iphone16plus"),
        ("iPhone 16 Pro", "iphone16pro"),
        ("iPhone 16 Pro Max", "iphone16promax")
    ],

    "17": [
        ("iPhone 17", "iphone17"),
        ("iPhone 17 Pro", "iphone17pro"),
        ("iPhone 17 Pro Max", "iphone17promax"),
        ("iPhone Air", "iphone17air")
    ]
}


def get_iphone_groups():

    buttons = []

    for group in IPHONE_GROUPS:

        buttons.append([
            InlineKeyboardButton(
                text=f"iPhone {group}",
                callback_data=f"iphone_group_{group.lower()}"
            )
        ])

    buttons.append([
        InlineKeyboardButton(
            text="⬅️ Назад к брендам",
            callback_data="back_to_brands"
        )
    ])

    return InlineKeyboardMarkup(
        inline_keyboard=buttons
    )


def get_iphone_group_models(group):

    models = IPHONE_GROUPS.get(group, [])

    buttons = []

    for i in range(0, len(models), 2):

        row = [
            InlineKeyboardButton(
                text=models[i][0],
                callback_data=f"model_{models[i][1]}"
            )
        ]

        if i + 1 < len(models):

            row.append(
                InlineKeyboardButton(
                    text=models[i + 1][0],
                    callback_data=f"model_{models[i + 1][1]}"
                )
            )

        buttons.append(row)

    buttons.append([
        InlineKeyboardButton(
            text="⬅️ Назад к iPhone",
            callback_data="brand_iphone"
        )
    ])

    return InlineKeyboardMarkup(
        inline_keyboard=buttons
    )

# =========================================================
# SAMSUNG GROUPS
# =========================================================

SAMSUNG_GROUPS = {

    "S": [
        ("Samsung Galaxy S24 Ultra", "s24ultra"),
        ("Samsung Galaxy S24+", "s24plus"),
        ("Samsung Galaxy S24", "s24"),
        ("Samsung Galaxy S23 Ultra", "s23ultra"),
        ("Samsung Galaxy S23", "s23"),
        ("Samsung Galaxy S22 Ultra", "s22ultra"),
        ("Samsung Galaxy S22", "s22"),
        ("Samsung Galaxy S21", "s21"),
        ("Samsung Galaxy S20 Ultra", "s20ultra"),
        ("Samsung Galaxy S20", "s20"),
        ("Samsung Galaxy S10+", "s10plus"),
        ("Samsung Galaxy S10", "s10")
    ],

    "A": [
        ("Samsung Galaxy A55 5G", "a55"),
        ("Samsung Galaxy A54", "a54"),
        ("Samsung Galaxy A53 5G", "a53"),
        ("Samsung Galaxy A52s 5G", "a52s"),
        ("Samsung Galaxy A52", "a52"),
        ("Samsung Galaxy A51", "a51"),
        ("Samsung Galaxy A50", "a50"),
        ("Samsung Galaxy A35", "a35"),
        ("Samsung Galaxy A34", "a34"),
        ("Samsung Galaxy A33 5G", "a33"),
        ("Samsung Galaxy A32 5G", "a32_5g"),
        ("Samsung Galaxy A32", "a32"),
        ("Samsung Galaxy A31", "a31"),
        ("Samsung Galaxy A25 5G", "a25"),
        ("Samsung Galaxy A24", "a24"),
        ("Samsung Galaxy A23 5G", "a23"),
        ("Samsung Galaxy A22 5G", "a22"),
        ("Samsung Galaxy A15 5G", "a15"),
        ("Samsung Galaxy A10s", "a10s"),
        ("Samsung Galaxy A10", "a10")
    ]
}


def get_samsung_groups():

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📱 Galaxy S",
                    callback_data="samsung_group_s"
                )
            ],
            [
                InlineKeyboardButton(
                    text="📱 Galaxy A",
                    callback_data="samsung_group_a"
                )
            ],
            [
                InlineKeyboardButton(
                    text="⬅️ Назад к брендам",
                    callback_data="back_to_brands"
                )
            ]
        ]
    )


def get_samsung_group_models(group):

    models = SAMSUNG_GROUPS.get(group, [])

    buttons = []

    for i in range(0, len(models), 2):

        row = [
            InlineKeyboardButton(
                text=models[i][0],
                callback_data=f"model_{models[i][1]}"
            )
        ]

        if i + 1 < len(models):

            row.append(
                InlineKeyboardButton(
                    text=models[i + 1][0],
                    callback_data=f"model_{models[i + 1][1]}"
                )
            )

        buttons.append(row)

    buttons.append([
        InlineKeyboardButton(
            text="⬅️ Назад к Samsung",
            callback_data="brand_samsung"
        )
    ])

    return InlineKeyboardMarkup(
        inline_keyboard=buttons
    )

# =========================================================
# POCO GROUPS
# =========================================================

POCO_GROUPS = {

    "X": [
        ("POCO X6 Pro 5G", "poco_x6pro"),
        ("POCO X6 5G", "poco_x6"),
        ("POCO X5 Pro 5G", "poco_x5pro"),
        ("POCO X5 5G", "poco_x5"),
        ("POCO X4 Pro 5G", "poco_x4pro5g"),
        ("POCO X4 Pro", "poco_x4pro"),
        ("POCO X4 GT", "poco_x4gt")
    ],

    "M": [
        ("POCO M6 Pro 5G", "poco_m6pro5g"),
        ("POCO M6 Plus 5G", "poco_m6plus5g"),
        ("POCO M6 Pro", "poco_m6pro")
    ],

    "F": [
        ("POCO F5 Pro", "poco_f5pro"),
        ("POCO F5", "poco_f5")
    ],

    "C": [
        ("POCO C75 5G", "poco_c75"),
        ("POCO C61", "poco_c61"),
        ("POCO C55", "poco_c55"),
        ("POCO C31", "poco_c31")
    ]
}


def get_poco_groups():

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📱 POCO X",
                    callback_data="poco_group_x"
                ),
                InlineKeyboardButton(
                    text="📱 POCO M",
                    callback_data="poco_group_m"
                )
            ],
            [
                InlineKeyboardButton(
                    text="📱 POCO F",
                    callback_data="poco_group_f"
                ),
                InlineKeyboardButton(
                    text="📱 POCO C",
                    callback_data="poco_group_c"
                )
            ],
            [
                InlineKeyboardButton(
                    text="⬅️ Назад к брендам",
                    callback_data="back_to_brands"
                )
            ]
        ]
    )


def get_poco_group_models(group):

    models = POCO_GROUPS.get(group, [])

    buttons = []

    for i in range(0, len(models), 2):

        row = [
            InlineKeyboardButton(
                text=models[i][0],
                callback_data=f"model_{models[i][1]}"
            )
        ]

        if i + 1 < len(models):

            row.append(
                InlineKeyboardButton(
                    text=models[i + 1][0],
                    callback_data=f"model_{models[i + 1][1]}"
                )
            )

        buttons.append(row)

    buttons.append([
        InlineKeyboardButton(
            text="⬅️ Назад к POCO",
            callback_data="brand_poco"
        )
    ])

    return InlineKeyboardMarkup(
        inline_keyboard=buttons
    )

# =========================================================
# TECNO GROUPS
# =========================================================

TECNO_GROUPS = {

    "Spark": [
        ("Tecno Spark 2022", "tecno_spark2022"),
        ("Tecno Spark 20 Pro+", "tecno_spark20proplus"),
        ("Tecno Spark 20 Pro", "tecno_spark20pro"),
        ("Tecno Spark 20", "tecno_spark20"),
        ("Tecno Spark 10 Pro", "tecno_spark10pro"),
        ("Tecno Spark 8 Pro", "tecno_spark8pro")
    ],

    "Phantom": [
        ("Tecno Phantom X2 Pro", "tecno_phantomx2pro"),
        ("Tecno Phantom X2", "tecno_phantomx2")
    ],

    "Camon": [
        ("Tecno Camon 20 Pro 5G", "tecno_camon20pro5g"),
        ("Tecno Camon 20 Premier", "tecno_camon20premier"),
        ("Tecno Camon 20", "tecno_camon20")
    ],

    "Pova": [
        ("Tecno Pova 5 Pro 5G", "tecno_pova5pro")
    ],

    "Pop": [
        ("Tecno Pop 7 Pro", "tecno_pop7pro"),
        ("Tecno Pop 6 Pro", "tecno_pop6pro"),
        ("Tecno Pop 5 Pro", "tecno_pop5pro")
    ]
}


def get_tecno_groups():

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📱 Spark",
                    callback_data="tecno_group_spark"
                ),
                InlineKeyboardButton(
                    text="📱 Phantom",
                    callback_data="tecno_group_phantom"
                )
            ],
            [
                InlineKeyboardButton(
                    text="📱 Camon",
                    callback_data="tecno_group_camon"
                ),
                InlineKeyboardButton(
                    text="📱 Pova",
                    callback_data="tecno_group_pova"
                )
            ],
            [
                InlineKeyboardButton(
                    text="📱 Pop",
                    callback_data="tecno_group_pop"
                )
            ],
            [
                InlineKeyboardButton(
                    text="⬅️ Назад к брендам",
                    callback_data="back_to_brands"
                )
            ]
        ]
    )


def get_tecno_group_models(group):

    models = TECNO_GROUPS.get(group, [])

    buttons = []

    for i in range(0, len(models), 2):

        row = [
            InlineKeyboardButton(
                text=models[i][0],
                callback_data=f"model_{models[i][1]}"
            )
        ]

        if i + 1 < len(models):

            row.append(
                InlineKeyboardButton(
                    text=models[i + 1][0],
                    callback_data=f"model_{models[i + 1][1]}"
                )
            )

        buttons.append(row)

    buttons.append([
        InlineKeyboardButton(
            text="⬅️ Назад к TECNO",
            callback_data="brand_tecno"
        )
    ])

    return InlineKeyboardMarkup(
        inline_keyboard=buttons
    )

# =========================================================
# INFINIX GROUPS
# =========================================================

INFINIX_GROUPS = {

    "Note": [
        ("Infinix Note 40 Pro+", "infinix_note40proplus"),
        ("Infinix Note 40 Pro", "infinix_note40pro"),
        ("Infinix Note 40", "infinix_note40"),
        ("Infinix Note 30 Pro", "infinix_note30pro"),
        ("Infinix Note 30", "infinix_note30"),
        ("Infinix Note 12 Pro", "infinix_note12pro")
    ],

    "Hot": [
        ("Infinix Hot 50 Pro+", "infinix_hot50proplus"),
        ("Infinix Hot 50 Pro", "infinix_hot50pro"),
        ("Infinix Hot 50", "infinix_hot50"),
        ("Infinix Hot 40 Pro", "infinix_hot40pro"),
        ("Infinix Hot 40", "infinix_hot40"),
        ("Infinix Hot 30", "infinix_hot30")
    ],

    "GT": [
        ("Infinix GT 30 Pro", "infinix_gt30pro"),
        ("Infinix GT 20 Pro", "infinix_gt20pro"),
        ("Infinix GT 10 Pro", "infinix_gt10pro")
    ],

    "Zero": [
        ("Infinix Zero 40", "infinix_zero40"),
        ("Infinix Zero 30 5G", "infinix_zero30_5g"),
        ("Infinix Zero 30", "infinix_zero30")
    ]
}


def get_infinix_groups():

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📱 Note",
                    callback_data="infinix_group_note"
                ),
                InlineKeyboardButton(
                    text="📱 Hot",
                    callback_data="infinix_group_hot"
                )
            ],
            [
                InlineKeyboardButton(
                    text="📱 GT",
                    callback_data="infinix_group_gt"
                ),
                InlineKeyboardButton(
                    text="📱 Zero",
                    callback_data="infinix_group_zero"
                )
            ],
            [
                InlineKeyboardButton(
                    text="⬅️ Назад к брендам",
                    callback_data="back_to_brands"
                )
            ]
        ]
    )


def get_infinix_group_models(group):

    models = INFINIX_GROUPS.get(group, [])

    buttons = []

    for i in range(0, len(models), 2):

        row = [
            InlineKeyboardButton(
                text=models[i][0],
                callback_data=f"model_{models[i][1]}"
            )
        ]

        if i + 1 < len(models):

            row.append(
                InlineKeyboardButton(
                    text=models[i + 1][0],
                    callback_data=f"model_{models[i + 1][1]}"
                )
            )

        buttons.append(row)

    buttons.append([
        InlineKeyboardButton(
            text="⬅️ Назад к Infinix",
            callback_data="brand_infinix"
        )
    ])

    return InlineKeyboardMarkup(
        inline_keyboard=buttons
    )

# =========================================================
# REALME GROUPS
# =========================================================

REALME_GROUPS = {

    "GT": [
        ("Realme GT 7 Pro", "realme_gt7pro"),
        ("Realme GT 6", "realme_gt6"),
        ("Realme GT 5", "realme_gt5"),
        ("Realme GT Neo 6", "realme_gtneo6"),
        ("Realme GT Neo 5", "realme_gtneo5")
    ],

    "Number": [
        ("Realme 14 Pro+", "realme_14proplus"),
        ("Realme 14 Pro", "realme_14pro"),
        ("Realme 13 Pro+", "realme_13proplus"),
        ("Realme 13 Pro", "realme_13pro"),
        ("Realme 12 Pro+", "realme_12proplus"),
        ("Realme 12 Pro", "realme_12pro"),
        ("Realme 11 Pro+", "realme_11proplus"),
        ("Realme 11 Pro", "realme_11pro")
    ],

    "C": [
        ("Realme C75", "realme_c75"),
        ("Realme C65", "realme_c65"),
        ("Realme C55", "realme_c55"),
        ("Realme C53", "realme_c53"),
        ("Realme C35", "realme_c35")
    ],

    "Narzo": [
        ("Realme Narzo 70 Pro", "realme_narzo70pro"),
        ("Realme Narzo 60 Pro", "realme_narzo60pro"),
        ("Realme Narzo 50 Pro", "realme_narzo50pro")
    ]
}


def get_realme_groups():

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📱 GT",
                    callback_data="realme_group_gt"
                ),
                InlineKeyboardButton(
                    text="📱 Number",
                    callback_data="realme_group_number"
                )
            ],
            [
                InlineKeyboardButton(
                    text="📱 C",
                    callback_data="realme_group_c"
                ),
                InlineKeyboardButton(
                    text="📱 Narzo",
                    callback_data="realme_group_narzo"
                )
            ],
            [
                InlineKeyboardButton(
                    text="⬅️ Назад к брендам",
                    callback_data="back_to_brands"
                )
            ]
        ]
    )


def get_realme_group_models(group):

    models = REALME_GROUPS.get(group, [])

    buttons = []

    for i in range(0, len(models), 2):

        row = [
            InlineKeyboardButton(
                text=models[i][0],
                callback_data=f"model_{models[i][1]}"
            )
        ]

        if i + 1 < len(models):

            row.append(
                InlineKeyboardButton(
                    text=models[i + 1][0],
                    callback_data=f"model_{models[i + 1][1]}"
                )
            )

        buttons.append(row)

    buttons.append([
        InlineKeyboardButton(
            text="⬅️ Назад к Realme",
            callback_data="brand_realme"
        )
    ])

    return InlineKeyboardMarkup(
        inline_keyboard=buttons
    )


# =========================================================
# HONOR GROUPS
# =========================================================

HONOR_GROUPS = {

    "Magic": [
        ("HONOR Robot Phone", "honor_robot_phone"),
        ("HONOR Magic V6", "honor_magicv6"),
        ("HONOR Magic8 Pro", "honor_magic8pro"),
        ("HONOR Magic V5", "honor_magicv5"),
        ("HONOR Magic7 RSR", "honor_magic7rsr"),
        ("HONOR Magic7 Pro", "honor_magic7pro"),
        ("HONOR Magic V3", "honor_magicv3"),
        ("HONOR Magic7", "honor_magic7"),
        ("HONOR Magic6 RSR", "honor_magic6rsr"),
        ("HONOR Magic6 Pro", "honor_magic6pro"),
        ("HONOR Magic V2 RSR", "honor_magicv2rsr"),
        ("HONOR Magic V2", "honor_magicv2"),
        ("HONOR Magic5 Pro", "honor_magic5pro"),
        ("HONOR Magic5 Ultimate", "honor_magic5ultimate"),
        ("HONOR Magic5", "honor_magic5"),
        ("HONOR Magic Vs", "honor_magicvs"),
        ("HONOR Magic4 Pro", "honor_magic4pro"),
        ("HONOR Magic4 Ultimate", "honor_magic4ultimate"),
        ("HONOR Magic V", "honor_magicv")
    ],

    "N": [
        ("HONOR 600 Pro", "honor_600pro"),
        ("HONOR 600", "honor_600"),
        ("HONOR 600 Pro MOLLY Limited Edition", "honor_600pro_molly"),
        ("HONOR 600 Lite", "honor_600lite"),
        ("HONOR 600 Smart 5G", "honor_600smart5g"),
        ("HONOR 400 Pro", "honor_400pro"),
        ("HONOR 400 Smart 5G", "honor_400smart5g"),
        ("HONOR 400", "honor_400"),
        ("HONOR 400 Lite", "honor_400lite"),
        ("HONOR 200 Smart", "honor_200smart"),
        ("HONOR 200 Pro", "honor_200pro"),
        ("HONOR 200", "honor_200"),
        ("HONOR 200 Lite", "honor_200lite"),
        ("HONOR 90", "honor_90"),
        ("HONOR 90 Lite", "honor_90lite"),
        ("HONOR 70 Pro", "honor_70pro"),
        ("HONOR 70 Pro+", "honor_70proplus"),
        ("HONOR 70", "honor_70"),
        ("HONOR 50", "honor_50"),
        ("HONOR 50 Lite", "honor_50lite")
    ],

    "X": [
        ("HONOR X9d", "honor_x9d"),
        ("HONOR X7e Plus", "honor_x7eplus"),
        ("HONOR X7e", "honor_x7e"),
        ("HONOR X8d", "honor_x8d"),
        ("HONOR X7d", "honor_x7d"),
        ("HONOR X6e", "honor_x6e"),
        ("HONOR X6d 5G", "honor_x6d5g"),
        ("HONOR X5d Plus", "honor_x5dplus"),
        ("HONOR X5d", "honor_x5d"),
        ("HONOR X5c Plus", "honor_x5cplus"),
        ("HONOR X5c", "honor_x5c"),
        ("HONOR X9c", "honor_x9c"),
        ("HONOR X8c", "honor_x8c"),
        ("HONOR X6c", "honor_x6c"),
        ("HONOR X9c Smart", "honor_x9csmart"),
        ("HONOR X5b", "honor_x5b"),
        ("HONOR X7c", "honor_x7c"),
        ("HONOR X9b", "honor_x9b"),
        ("HONOR X6b", "honor_x6b"),
        ("HONOR X7b 5G", "honor_x7b5g"),
        ("HONOR X8b", "honor_x8b"),
        ("HONOR X7b", "honor_x7b"),
        ("HONOR X5 Plus", "honor_x5plus"),
        ("HONOR X9a", "honor_x9a"),
        ("HONOR X8a 5G", "honor_x8a5g"),
        ("HONOR X6a", "honor_x6a"),
        ("HONOR X8a", "honor_x8a"),
        ("HONOR X7a", "honor_x7a"),
        ("HONOR X8 5G", "honor_x8_5g"),
        ("HONOR X9 5G", "honor_x9_5g"),
        ("HONOR X9", "honor_x9"),
        ("HONOR X8", "honor_x8"),
        ("HONOR X7", "honor_x7"),
        ("HONOR X6", "honor_x6"),
        ("HONOR X5", "honor_x5")
    ],

    "Play": [
        ("HONOR Play20A", "honor_play20a"),
        ("HONOR Play20C", "honor_play20c")
    ]
}


def get_honor_groups():

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📱 Magic",
                    callback_data="honor_group_magic"
                ),
                InlineKeyboardButton(
                    text="📱 N Series",
                    callback_data="honor_group_n"
                )
            ],
            [
                InlineKeyboardButton(
                    text="📱 X Series",
                    callback_data="honor_group_x"
                ),
                InlineKeyboardButton(
                    text="📱 Play",
                    callback_data="honor_group_play"
                )
            ],
            [
                InlineKeyboardButton(
                    text="⬅️ Назад к брендам",
                    callback_data="back_to_brands"
                )
            ]
        ]
    )


def get_honor_group_models(group):

    models = HONOR_GROUPS.get(group, [])

    buttons = []

    for i in range(0, len(models), 2):

        row = [
            InlineKeyboardButton(
                text=models[i][0],
                callback_data=f"model_{models[i][1]}"
            )
        ]

        if i + 1 < len(models):

            row.append(
                InlineKeyboardButton(
                    text=models[i + 1][0],
                    callback_data=f"model_{models[i + 1][1]}"
                )
            )

        buttons.append(row)

    buttons.append([
        InlineKeyboardButton(
            text="⬅️ Назад к HONOR",
            callback_data="brand_honor"
        )
    ])

    return InlineKeyboardMarkup(
        inline_keyboard=buttons
    )

# =========================================================
# XIAOMI GROUPS
# =========================================================

XIAOMI_GROUPS = {

    "Mi": [
        ("Xiaomi Mi 8", "mi8"),
        ("Xiaomi Mi 8 SE", "mi8se"),
        ("Xiaomi Mi 8 Pro", "mi8pro"),
        ("Xiaomi Mi 8 Lite", "mi8lite"),
        ("Xiaomi Mi 9", "mi9"),
        ("Xiaomi Mi 9 SE", "mi9se"),
        ("Xiaomi Mi 9 Lite", "mi9lite"),
        ("Xiaomi Mi 9T", "mi9t"),
        ("Xiaomi Mi 9T Pro", "mi9tpro"),
        ("Xiaomi Mi 9T Pro 5G", "mi9tpro5g"),
        ("Xiaomi Mi 10", "mi10"),
        ("Xiaomi Mi 10 Pro", "mi10pro"),
        ("Xiaomi Mi 10 Lite", "mi10lite"),
        ("Xiaomi Mi 10 Lite 5G", "mi10lite5g"),
        ("Xiaomi Mi 10T", "mi10t"),
        ("Xiaomi Mi 10T Pro", "mi10tpro"),
        ("Xiaomi Mi 10T Lite", "mi10tlite"),
        ("Xiaomi Mi 10T Ultra", "mi10tultra"),
        ("Xiaomi Mi 11", "mi11"),
        ("Xiaomi Mi 11 Lite", "mi11lite"),
        ("Xiaomi Mi 11 Lite 5G", "mi11lite5g"),
        ("Xiaomi Mi 11 Lite 5G NE", "mi11lite5gne"),
        ("Xiaomi Mi 11 Pro", "mi11pro"),
        ("Xiaomi Mi 11 Ultra", "mi11ultra"),
        ("Xiaomi Mi 11X", "mi11x"),
        ("Xiaomi Mi 11X Pro", "mi11xpro"),
        ("Xiaomi Mi 11i", "mi11i"),
        ("Xiaomi Mi 12", "mi12"),
        ("Xiaomi Mi 12X", "mi12x"),
        ("Xiaomi Mi 12 Pro", "mi12pro"),
        ("Xiaomi Mi 12 Lite", "mi12lite"),
        ("Xiaomi Mi 12S", "mi12s"),
        ("Xiaomi Mi 12S Pro", "mi12spro"),
        ("Xiaomi Mi 12S Ultra", "mi12sultra"),
        ("Xiaomi Mi 13", "mi13"),
        ("Xiaomi Mi 13 Pro", "mi13pro"),
        ("Xiaomi Mi 13 Lite", "mi13lite"),
        ("Xiaomi Mi 13 Ultra", "mi13ultra"),
        ("Xiaomi Mi 14", "mi14"),
        ("Xiaomi Mi 14 Pro", "mi14pro"),
        ("Xiaomi Mi 14 Ultra", "mi14ultra"),
        ("Xiaomi Mi 15", "mi15"),
        ("Xiaomi Mi 15 Pro", "mi15pro"),
        ("Xiaomi Mi 15 Ultra", "mi15ultra"),
        ("Xiaomi Mi 15S Pro", "mi15spro"),
        ("Xiaomi Mi 17", "mi17"),
        ("Xiaomi Mi 17 Ultra", "mi17ultra")
    ],

    "T": [
        ("Xiaomi Mi 10T", "mi10t"),
        ("Xiaomi Mi 10T Pro", "mi10tpro"),
        ("Xiaomi Mi 10T Lite", "mi10tlite"),
        ("Xiaomi Mi 10T Ultra", "mi10tultra"),
        ("Xiaomi Mi 11T", "mi11t"),
        ("Xiaomi Mi 11T Pro", "mi11tpro"),
        ("Xiaomi Mi 12T", "mi12t"),
        ("Xiaomi Mi 12T Pro", "mi12tpro"),
        ("Xiaomi Mi 13T", "mi13t"),
        ("Xiaomi Mi 13T Pro", "mi13tpro"),
        ("Xiaomi Mi 14T", "mi14t"),
        ("Xiaomi Mi 14T Pro", "mi14tpro"),
        ("Xiaomi Mi 15T", "mi15t"),
        ("Xiaomi Mi 15T Pro", "mi15tpro"),
        ("Xiaomi Mi 17T", "mi17t"),
        ("Xiaomi Mi 17T Pro", "mi17tpro")
    ],

    "Redmi Note": [
        ("Redmi Note 7", "redmi_note7"),
        ("Redmi Note 7 Pro", "redmi_note7pro"),
        ("Redmi Note 7S", "redmi_note7s"),
        ("Redmi Note 8", "redmi_note8"),
        ("Redmi Note 8 Pro", "redmi_note8pro"),
        ("Redmi Note 8T", "redmi_note8t"),
        ("Redmi Note 9", "redmi_note9"),
        ("Redmi Note 9S", "redmi_note9s"),
        ("Redmi Note 9 Pro", "redmi_note9pro"),
        ("Redmi Note 9 Pro Max", "redmi_note9promax"),
        ("Redmi Note 9T", "redmi_note9t"),
        ("Redmi Note 9 5G", "redmi_note9_5g"),
        ("Redmi Note 9 Pro 5G", "redmi_note9pro_5g"),
        ("Redmi Note 9 NFC", "redmi_note9_nfc"),
        ("Redmi Note 10", "redmi_note10"),
        ("Redmi Note 10S", "redmi_note10s"),
        ("Redmi Note 10S 5G", "redmi_note10s_5g"),
        ("Redmi Note 10 Pro", "redmi_note10pro"),
        ("Redmi Note 10 Pro 5G", "redmi_note10pro_5g"),
        ("Redmi Note 10 Lite", "redmi_note10lite"),
        ("Redmi Note 10T", "redmi_note10t"),
        ("Redmi Note 11", "redmi_note11"),
        ("Redmi Note 11S", "redmi_note11s"),
        ("Redmi Note 11S 5G", "redmi_note11s_5g"),
        ("Redmi Note 11 Pro", "redmi_note11pro"),
        ("Redmi Note 11 Pro 5G", "redmi_note11pro_5g"),
        ("Redmi Note 11 Pro+ 5G", "redmi_note11proplus_5g"),
        ("Redmi Note 11T 5G", "redmi_note11t_5g"),
        ("Redmi Note 11T Pro", "redmi_note11tpro"),
        ("Redmi Note 11T Pro+", "redmi_note11tproplus"),
        ("Redmi Note 12", "redmi_note12"),
        ("Redmi Note 12 Pro 5G", "redmi_note12pro_5g"),
        ("Redmi Note 12 Pro+ 5G", "redmi_note12proplus_5g"),
        ("Redmi Note 12S", "redmi_note12s"),
        ("Redmi Note 12 Turbo", "redmi_note12turbo"),
        ("Redmi Note 12T Pro", "redmi_note12tpro"),
        ("Redmi Note 13", "redmi_note13"),
        ("Redmi Note 13 5G", "redmi_note13_5g"),
        ("Redmi Note 13 Pro", "redmi_note13pro"),
        ("Redmi Note 13 Pro+ 5G", "redmi_note13proplus_5g"),
        ("Redmi Note 13R", "redmi_note13r"),
        ("Redmi Note 13R Pro", "redmi_note13rpro"),
        ("Redmi Note 14", "redmi_note14"),
        ("Redmi Note 14 5G", "redmi_note14_5g"),
        ("Redmi Note 14 Pro", "redmi_note14pro"),
        ("Redmi Note 14 Pro 5G", "redmi_note14pro_5g"),
        ("Redmi Note 14 Pro+ 5G", "redmi_note14proplus_5g"),
        ("Redmi Note 14S", "redmi_note14s"),
        ("Redmi Note 15", "redmi_note15"),
        ("Redmi Note 15 5G", "redmi_note15_5g"),
        ("Redmi Note 15 Pro", "redmi_note15pro"),
        ("Redmi Note 15 Pro 5G", "redmi_note15pro_5g"),
        ("Redmi Note 15 Pro+ 5G", "redmi_note15proplus_5g"),
        ("Xiaomi Redmi Note 17", "redmi_note17")
    ]
}


def get_xiaomi_groups():

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📱 Xiaomi Mi",
                    callback_data="xiaomi_group_mi"
                )
            ],
            [
                InlineKeyboardButton(
                    text="📱 Xiaomi T",
                    callback_data="xiaomi_group_t"
                )
            ],
            [
                InlineKeyboardButton(
                    text="📱 Redmi Note",
                    callback_data="xiaomi_group_redminote"
                )
            ],
            [
                InlineKeyboardButton(
                    text="⬅️ Назад к брендам",
                    callback_data="back_to_brands"
                )
            ]
        ]
    )


def get_xiaomi_group_models(group):

    models = XIAOMI_GROUPS.get(group, [])

    buttons = []

    for i in range(0, len(models), 2):

        row = [
            InlineKeyboardButton(
                text=models[i][0],
                callback_data=f"model_{models[i][1]}"
            )
        ]

        if i + 1 < len(models):

            row.append(
                InlineKeyboardButton(
                    text=models[i + 1][0],
                    callback_data=f"model_{models[i + 1][1]}"
                )
            )

        buttons.append(row)

    buttons.append([
        InlineKeyboardButton(
            text="⬅️ Назад к Xiaomi",
            callback_data="brand_xiaomi"
        )
    ])

    return InlineKeyboardMarkup(
        inline_keyboard=buttons
    )

# =========================================================
# CONFIGS
# =========================================================

IPHONE_CONFIGS = {

    "iphone8": ("iPhone 8", 190, 175, 160, 145, 130, 20),
    "iphone8plus": ("iPhone 8+", 180, 166, 151, 127, 122, 16),
    "iphonexr": ("iPhone XR", 195, 180, 165, 150, 135, 20),
    "iphonex": ("iPhone X", 145, 130, 115, 100, 85, 15),
    "iphonexs": ("iPhone Xs", 173, 162, 143, 127, 118, 11),
    "iphonexsmax": ("iPhone Xs Max", 181, 164, 148, 127, 118, 10),
    "iphone11": ("iPhone 11", 200, 185, 170, 155, 140, 20),
    "iphone11pro": ("iPhone 11 Pro", 148, 133, 118, 103, 88, 15),
    "iphone11promax": ("iPhone 11 Pro Max", 148, 133, 118, 103, 88, 15),
    "iphone12": ("iPhone 12", 152, 137, 122, 107, 92, 16),
    "iphone12pro": ("iPhone 12 Pro", 153, 138, 123, 108, 93, 16),
    "iphone12promax": ("iPhone 12 Pro Max", 155, 140, 125, 110, 95, 16),
    "iphone12mini": ("iPhone 12 mini", 150, 135, 120, 105, 90, 15),
    "iphone13": ("iPhone 13", 160, 145, 130, 115, 100, 17),
    "iphone13pro": ("iPhone 13 Pro", 161, 146, 131, 116, 101, 17),
    "iphone13promax": ("iPhone 13 Pro Max", 162, 147, 132, 117, 102, 17),
    "iphone13mini": ("iPhone 13 mini", 155, 140, 125, 110, 95, 16),
    "iphone14": ("iPhone 14", 163, 148, 133, 115, 100, 17),
    "iphone14pro": ("iPhone 14 Pro", 168, 153, 138, 123, 108, 17),
    "iphone14promax": ("iPhone 14 Pro Max", 170, 155, 140, 125, 110, 18),
    "iphone15": ("iPhone 15", 172, 157, 142, 127, 112, 18),
    "iphone15pro": ("iPhone 15 Pro", 175, 160, 145, 130, 115, 18),
    "iphone15promax": ("iPhone 15 Pro Max", 180, 165, 150, 135, 120, 19),
    "iphone15plus": ("iPhone 15 Plus", 174, 159, 144, 129, 114, 18),
    "iphonese2": ("iPhone SE 2nd Gen", 198, 183, 168, 153, 138, 20),
    "iphonese3": ("iPhone SE 3rd Gen", 200, 185, 170, 155, 140, 20),
    "iphone16": ("iPhone 16", 176, 161, 146, 131, 116, 18),
    "iphone16pro": ("iPhone 16 Pro", 180, 165, 150, 135, 120, 19),
    "iphone16promax": ("iPhone 16 Pro Max", 182, 167, 152, 137, 122, 19),
    "iphone16plus": ("iPhone 16 Plus", 178, 163, 148, 133, 118, 18),
    "iphone17": ("iPhone 17", 163, 150, 138, 123, 99, 17),
    "iphone17pro": ("iPhone 17 Pro", 166, 151, 138, 115, 105, 12),
    "iphone17promax": ("iPhone 17 Pro Max", 164, 153, 130, 114, 101, 13),
    "iphone17air": ("iPhone Air", 161, 146, 133, 118, 102, 10)
}

SAMSUNG_CONFIGS = {

    "s24ultra": ("Samsung Galaxy S24 Ultra", 140, 125, 110, 95, 80, 15),
    "s24plus": ("Samsung Galaxy S24+", 145, 130, 115, 100, 85, 15),
    "s23ultra": ("Samsung Galaxy S23 Ultra", 150, 135, 120, 105, 90, 16),
    "s23": ("Samsung Galaxy S23", 165, 150, 135, 120, 105, 17),
    "s22ultra": ("Samsung Galaxy S22 Ultra", 148, 133, 118, 103, 88, 15),
    "s21": ("Samsung Galaxy S21", 170, 155, 140, 125, 110, 18),
    "s20ultra": ("Samsung Galaxy S20 Ultra", 138, 123, 108, 93, 78, 14),
    "s20": ("Samsung Galaxy S20", 130, 115, 100, 85, 70, 14),
    "s10plus": ("Samsung Galaxy S10+", 137, 122, 107, 92, 77, 14),
    "s10": ("Samsung Galaxy S10", 132, 117, 102, 87, 72, 14),
    "s24": ("Samsung Galaxy S24", 168, 153, 138, 123, 108, 17),
    "s22": ("Samsung Galaxy S22", 165, 150, 135, 120, 105, 17),

    "a54": ("Samsung Galaxy A54", 175, 160, 145, 130, 115, 18),
    "a35": ("Samsung Galaxy A35", 180, 165, 150, 135, 120, 19),
    "a34": ("Samsung Galaxy A34", 178, 163, 148, 133, 118, 18),
    "a23": ("Samsung Galaxy A23 5G", 195, 180, 165, 150, 135, 20),
    "a22": ("Samsung Galaxy A22 5G", 190, 175, 160, 145, 130, 20),
    "a15": ("Samsung Galaxy A15 5G", 185, 170, 155, 140, 125, 19),
    "a52": ("Samsung Galaxy A52", 172, 157, 142, 127, 112, 18),
    "a32": ("Samsung Galaxy A32", 200, 185, 170, 155, 140, 20),
    "a55": ("Samsung Galaxy A55 5G", 172, 157, 142, 127, 112, 18),
    "a53": ("Samsung Galaxy A53 5G", 172, 157, 142, 127, 112, 18),
    "a52s": ("Samsung Galaxy A52s 5G", 170, 155, 140, 125, 110, 17),
    "a51": ("Samsung Galaxy A51", 171, 156, 141, 126, 111, 18),
    "a50": ("Samsung Galaxy A50", 172, 157, 142, 127, 112, 18),
    "a33": ("Samsung Galaxy A33 5G", 175, 160, 145, 130, 115, 18),
    "a32_5g": ("Samsung Galaxy A32 5G", 195, 180, 165, 150, 135, 20),
    "a31": ("Samsung Galaxy A31", 172, 157, 142, 127, 112, 18),
    "a25": ("Samsung Galaxy A25 5G", 175, 160, 145, 130, 115, 18),
    "a24": ("Samsung Galaxy A24", 192, 177, 162, 147, 132, 19),
    "a10s": ("Samsung Galaxy A10s", 188, 173, 158, 143, 128, 19),
    "a10": ("Samsung Galaxy A10", 193, 178, 163, 148, 133, 19)
}

POCO_CONFIGS = {

    "poco_x5": ("POCO X5 5G", 180, 165, 150, 135, 120, 19),
    "poco_x4pro": ("POCO X4 Pro", 175, 160, 145, 130, 115, 18),
    "poco_x6pro": ("POCO X6 Pro 5G", 162, 147, 132, 117, 102, 17),
    "poco_x6": ("POCO X6 5G", 175, 160, 145, 130, 115, 18),
    "poco_x5pro": ("POCO X5 Pro 5G", 175, 160, 145, 130, 115, 18),
    "poco_x4pro5g": ("POCO X4 Pro 5G", 175, 160, 145, 130, 115, 18),
    "poco_x4gt": ("POCO X4 GT", 175, 160, 145, 130, 115, 18),
    "poco_m6pro5g": ("POCO M6 Pro 5G", 185, 170, 155, 140, 125, 19),
    "poco_m6plus5g": ("POCO M6 Plus 5G", 180, 165, 150, 135, 120, 19),
    "poco_m6pro": ("POCO M6 Pro", 175, 160, 145, 130, 115, 18),
    "poco_f5pro": ("POCO F5 Pro", 172, 157, 142, 127, 112, 18),
    "poco_f5": ("POCO F5", 175, 160, 145, 130, 115, 18),
    "poco_c75": ("POCO C75 5G", 190, 175, 160, 145, 130, 20),
    "poco_c61": ("POCO C61", 185, 170, 155, 140, 125, 19),
    "poco_c55": ("POCO C55", 180, 165, 150, 135, 120, 19),
    "poco_c31": ("POCO C31", 175, 160, 145, 130, 115, 18)
}

TECNO_CONFIGS = {

    "tecno_spark2022": ("Tecno Spark 2022", 172, 157, 142, 127, 112, 18),
    "tecno_phantomx2pro": ("Tecno Phantom X2 Pro", 175, 160, 145, 130, 115, 18),
    "tecno_phantomx2": ("Tecno Phantom X2", 175, 160, 145, 130, 115, 18),
    "tecno_camon20pro5g": ("Tecno Camon 20 Pro 5G", 175, 160, 145, 130, 115, 18),
    "tecno_camon20premier": ("Tecno Camon 20 Premier", 175, 160, 145, 130, 115, 18),
    "tecno_camon20": ("Tecno Camon 20", 195, 180, 165, 150, 135, 20),
    "tecno_spark20proplus": ("Tecno Spark 20 Pro+", 195, 180, 165, 150, 135, 20),
    "tecno_spark20pro": ("Tecno Spark 20 Pro", 195, 180, 165, 150, 135, 20),
    "tecno_spark20": ("Tecno Spark 20", 195, 180, 165, 150, 135, 20),
    "tecno_spark10pro": ("Tecno Spark 10 Pro", 195, 180, 165, 150, 135, 20),
    "tecno_pova5pro": ("Tecno Pova 5 Pro 5G", 175, 160, 145, 130, 115, 18),
    "tecno_spark8pro": ("Tecno Spark 8 Pro", 195, 180, 165, 150, 135, 20),
    "tecno_pop7pro": ("Tecno Pop 7 Pro", 195, 180, 165, 150, 135, 20),
    "tecno_pop6pro": ("Tecno Pop 6 Pro", 195, 180, 165, 150, 135, 20),
    "tecno_pop5pro": ("Tecno Pop 5 Pro", 195, 180, 165, 150, 135, 20)
}

# =========================================================
# INFINIX CONFIGS
# =========================================================

INFINIX_CONFIGS = {

    "infinix_note40proplus": (
        "Infinix Note 40 Pro+",
        185, 170, 155, 140, 125, 19
    ),
    "infinix_note40pro": (
        "Infinix Note 40 Pro",
        183, 168, 153, 138, 123, 18
    ),
    "infinix_note40": (
        "Infinix Note 40",
        180, 165, 150, 135, 120, 18
    ),
    "infinix_note30pro": (
        "Infinix Note 30 Pro",
        185, 170, 155, 140, 125, 19
    ),
    "infinix_note30": (
        "Infinix Note 30",
        180, 165, 150, 135, 120, 18
    ),
    "infinix_note12pro": (
        "Infinix Note 12 Pro",
        178, 163, 148, 133, 118, 18
    ),

    "infinix_hot50proplus": (
        "Infinix Hot 50 Pro+",
        190, 175, 160, 145, 130, 20
    ),
    "infinix_hot50pro": (
        "Infinix Hot 50 Pro",
        188, 173, 158, 143, 128, 19
    ),
    "infinix_hot50": (
        "Infinix Hot 50",
        185, 170, 155, 140, 125, 19
    ),
    "infinix_hot40pro": (
        "Infinix Hot 40 Pro",
        190, 175, 160, 145, 130, 20
    ),
    "infinix_hot40": (
        "Infinix Hot 40",
        185, 170, 155, 140, 125, 19
    ),
    "infinix_hot30": (
        "Infinix Hot 30",
        180, 165, 150, 135, 120, 19
    ),

    "infinix_gt30pro": (
        "Infinix GT 30 Pro",
        190, 175, 160, 145, 130, 20
    ),
    "infinix_gt20pro": (
        "Infinix GT 20 Pro",
        188, 173, 158, 143, 128, 20
    ),
    "infinix_gt10pro": (
        "Infinix GT 10 Pro",
        185, 170, 155, 140, 125, 19
    ),

    "infinix_zero40": (
        "Infinix Zero 40",
        185, 170, 155, 140, 125, 19
    ),
    "infinix_zero30_5g": (
        "Infinix Zero 30 5G",
        190, 175, 160, 145, 130, 20
    ),
    "infinix_zero30": (
        "Infinix Zero 30",
        185, 170, 155, 140, 125, 19
    )
}

# =========================================================
# REALME CONFIGS
# =========================================================

REALME_CONFIGS = {

    "realme_gt7pro": (
        "Realme GT 7 Pro",
        190, 175, 160, 145, 130, 20
    ),
    "realme_gt6": (
        "Realme GT 6",
        188, 173, 158, 143, 128, 20
    ),
    "realme_gt5": (
        "Realme GT 5",
        185, 170, 155, 140, 125, 19
    ),
    "realme_gtneo6": (
        "Realme GT Neo 6",
        188, 173, 158, 143, 128, 20
    ),
    "realme_gtneo5": (
        "Realme GT Neo 5",
        185, 170, 155, 140, 125, 19
    ),

    "realme_14proplus": (
        "Realme 14 Pro+",
        188, 173, 158, 143, 128, 20
    ),
    "realme_14pro": (
        "Realme 14 Pro",
        185, 170, 155, 140, 125, 19
    ),
    "realme_13proplus": (
        "Realme 13 Pro+",
        188, 173, 158, 143, 128, 20
    ),
    "realme_13pro": (
        "Realme 13 Pro",
        185, 170, 155, 140, 125, 19
    ),
    "realme_12proplus": (
        "Realme 12 Pro+",
        188, 173, 158, 143, 128, 20
    ),
    "realme_12pro": (
        "Realme 12 Pro",
        185, 170, 155, 140, 125, 19
    ),
    "realme_11proplus": (
        "Realme 11 Pro+",
        185, 170, 155, 140, 125, 19
    ),
    "realme_11pro": (
        "Realme 11 Pro",
        183, 168, 153, 138, 123, 18
    ),

    "realme_c75": (
        "Realme C75",
        195, 180, 165, 150, 135, 20
    ),
    "realme_c65": (
        "Realme C65",
        190, 175, 160, 145, 130, 20
    ),
    "realme_c55": (
        "Realme C55",
        185, 170, 155, 140, 125, 19
    ),
    "realme_c53": (
        "Realme C53",
        185, 170, 155, 140, 125, 19
    ),
    "realme_c35": (
        "Realme C35",
        180, 165, 150, 135, 120, 18
    ),

    "realme_narzo70pro": (
        "Realme Narzo 70 Pro",
        190, 175, 160, 145, 130, 20
    ),
    "realme_narzo60pro": (
        "Realme Narzo 60 Pro",
        188, 173, 158, 143, 128, 20
    ),
    "realme_narzo50pro": (
        "Realme Narzo 50 Pro",
        185, 170, 155, 140, 125, 19
    )
}

# =========================================================
# XIAOMI CONFIGS
# =========================================================

XIAOMI_CONFIGS = {

    "mi8": ("Xiaomi Mi 8", 172, 157, 142, 127, 112, 18),
    "mi8se": ("Xiaomi Mi 8 SE", 181, 167, 149, 135, 116, 11),
    "mi8pro": ("Xiaomi Mi 8 Pro", 182, 163, 146, 129, 122, 17),
    "mi8lite": ("Xiaomi Mi 8 Lite", 180, 164, 144, 132, 114, 14),

    "mi9": ("Xiaomi Mi 9", 172, 157, 142, 127, 112, 18),
    "mi9se": ("Xiaomi Mi 9 SE", 182, 161, 152, 136, 119, 17),
    "mi9lite": ("Xiaomi Mi 9 Lite", 179, 160, 146, 127, 115, 11),
    "mi9t": ("Xiaomi Mi 9T", 172, 157, 142, 127, 112, 18),
    "mi9tpro": ("Xiaomi Mi 9T Pro", 172, 157, 142, 127, 112, 18),
    "mi9tpro5g": ("Xiaomi Mi 9T Pro 5G", 182, 166, 149, 128, 116, 17),

    "mi10": ("Xiaomi Mi 10", 133, 118, 103, 88, 73, 14),
    "mi10pro": ("Xiaomi Mi 10 Pro", 133, 118, 103, 88, 73, 14),
    "mi10lite": ("Xiaomi Mi 10 Lite", 179, 163, 142, 132, 113, 18),
    "mi10lite5g": ("Xiaomi Mi 10 Lite 5G", 180, 160, 144, 134, 117, 12),

    "mi11": ("Xiaomi Mi 11", 150, 135, 120, 105, 90, 16),
    "mi11lite": ("Xiaomi Mi 11 Lite", 172, 157, 142, 127, 112, 18),
    "mi11lite5g": ("Xiaomi Mi 11 Lite 5G", 172, 157, 142, 127, 112, 18),
    "mi11lite5gne": ("Xiaomi Mi 11 Lite 5G NE", 172, 165, 151, 130, 117, 14),
    "mi11pro": ("Xiaomi Mi 11 Pro", 174, 163, 151, 134, 114, 11),
    "mi11ultra": ("Xiaomi Mi 11 Ultra", 138, 123, 108, 93, 78, 14),
    "mi11x": ("Xiaomi Mi 11X", 175, 160, 145, 130, 115, 18),
    "mi11xpro": ("Xiaomi Mi 11X Pro", 175, 160, 145, 130, 115, 18),
    "mi11i": ("Xiaomi Mi 11i", 180, 164, 149, 130, 112, 19),

    "mi12": ("Xiaomi Mi 12", 182, 165, 143, 130, 117, 12),
    "mi12x": ("Xiaomi Mi 12X", 176, 167, 145, 137, 119, 11),
    "mi12pro": ("Xiaomi Mi 12 Pro", 181, 162, 143, 127, 115, 13),
    "mi12lite": ("Xiaomi Mi 12 Lite", 173, 163, 143, 135, 112, 18),
    "mi12s": ("Xiaomi Mi 12S", 177, 158, 151, 132, 121, 12),
    "mi12spro": ("Xiaomi Mi 12S Pro", 176, 165, 152, 136, 117, 18),
    "mi12sultra": ("Xiaomi Mi 12S Ultra", 178, 158, 147, 136, 117, 18),

    "mi13": ("Xiaomi Mi 13", 175, 158, 152, 134, 113, 20),
    "mi13pro": ("Xiaomi Mi 13 Pro", 182, 158, 148, 132, 122, 12),
    "mi13lite": ("Xiaomi Mi 13 Lite", 172, 160, 148, 135, 112, 16),
    "mi13ultra": ("Xiaomi Mi 13 Ultra", 172, 160, 152, 127, 117, 20),

    "mi14": ("Xiaomi Mi 14", 182, 160, 146, 132, 113, 18),
    "mi14pro": ("Xiaomi Mi 14 Pro", 172, 160, 149, 136, 120, 10),
    "mi14ultra": ("Xiaomi Mi 14 Ultra", 180, 162, 150, 129, 113, 18),

    "mi15": ("Xiaomi Mi 15", 181, 166, 149, 133, 118, 16),
    "mi15pro": ("Xiaomi Mi 15 Pro", 178, 165, 150, 134, 120, 10),
    "mi15ultra": ("Xiaomi Mi 15 Ultra", 177, 165, 142, 133, 121, 11),
    "mi15spro": ("Xiaomi Mi 15S Pro", 182, 167, 142, 130, 119, 11),

    "mi17": ("Xiaomi Mi 17", 182, 167, 147, 136, 112, 11),
    "mi17ultra": ("Xiaomi Mi 17 Ultra", 172, 157, 144, 136, 116, 14),

    "mi10t": ("Xiaomi Mi 10T", 175, 160, 145, 130, 115, 18),
    "mi10tpro": ("Xiaomi Mi 10T Pro", 175, 160, 145, 130, 115, 18),
    "mi10tlite": ("Xiaomi Mi 10T Lite", 175, 160, 145, 130, 115, 18),
    "mi10tultra": ("Xiaomi Mi 10T Ultra", 176, 163, 151, 128, 120, 17),

    "mi11t": ("Xiaomi Mi 11T", 177, 165, 152, 129, 117, 12),
    "mi11tpro": ("Xiaomi Mi 11T Pro", 179, 164, 149, 135, 117, 12),

    "mi12t": ("Xiaomi Mi 12T", 180, 167, 143, 135, 115, 19),
    "mi12tpro": ("Xiaomi Mi 12T Pro", 180, 165, 149, 137, 122, 12),

    "mi13t": ("Xiaomi Mi 13T", 173, 165, 142, 133, 116, 13),
    "mi13tpro": ("Xiaomi Mi 13T Pro", 177, 165, 143, 130, 122, 12),

    "mi14t": ("Xiaomi Mi 14T", 177, 167, 144, 129, 121, 15),
    "mi14tpro": ("Xiaomi Mi 14T Pro", 173, 161, 150, 137, 118, 14),

    "mi15t": ("Xiaomi Mi 15T", 180, 164, 142, 130, 117, 20),
    "mi15tpro": ("Xiaomi Mi 15T Pro", 172, 166, 150, 137, 112, 20),

    "mi17t": ("Xiaomi Mi 17T", 174, 163, 152, 131, 115, 18),
    "mi17tpro": ("Xiaomi Mi 17T Pro", 175, 165, 143, 136, 116, 19),

    "redmi_note7": ("Redmi Note 7", 175, 160, 145, 130, 115, 18),
    "redmi_note7pro": ("Redmi Note 7 Pro", 162, 147, 132, 117, 102, 16),
    "redmi_note7s": ("Redmi Note 7S", 176, 159, 151, 129, 121, 20),

    "redmi_note8": ("Redmi Note 8", 165, 150, 135, 120, 105, 17),
    "redmi_note8pro": ("Redmi Note 8 Pro", 170, 155, 140, 125, 110, 17),
    "redmi_note8t": ("Redmi Note 8T", 176, 162, 144, 135, 112, 11),

    "redmi_note9": ("Redmi Note 9", 175, 160, 145, 130, 115, 18),
    "redmi_note9s": ("Redmi Note 9S", 175, 160, 145, 130, 115, 18),
    "redmi_note9pro": ("Redmi Note 9 Pro", 175, 160, 145, 130, 115, 18),
    "redmi_note9promax": ("Redmi Note 9 Pro Max", 175, 160, 145, 130, 115, 18),

    "redmi_note9t": ("Redmi Note 9T", 175, 167, 148, 133, 122, 17),
    "redmi_note9_5g": ("Redmi Note 9 5G", 173, 166, 144, 137, 114, 17),
    "redmi_note9pro_5g": ("Redmi Note 9 Pro 5G", 173, 159, 142, 136, 122, 13),
    "redmi_note9_nfc": ("Redmi Note 9 NFC", 178, 167, 142, 137, 119, 18),

    "redmi_note10": ("Redmi Note 10", 175, 160, 145, 130, 115, 18),
    "redmi_note10s": ("Redmi Note 10S", 175, 160, 145, 130, 115, 18),
    "redmi_note10s_5g": ("Redmi Note 10S 5G", 188, 171, 157, 141, 128, 12),
    "redmi_note10pro": ("Redmi Note 10 Pro", 170, 155, 140, 125, 110, 18),
    "redmi_note10pro_5g": ("Redmi Note 10 Pro 5G", 180, 163, 151, 128, 114, 16),
    "redmi_note10lite": ("Redmi Note 10 Lite", 173, 161, 149, 127, 115, 15),
    "redmi_note10t": ("Redmi Note 10T", 181, 165, 149, 136, 122, 15),

    "redmi_note11": ("Redmi Note 11", 172, 157, 142, 127, 112, 18),
    "redmi_note11s": ("Redmi Note 11S", 170, 155, 140, 125, 110, 17),
    "redmi_note11s_5g": ("Redmi Note 11S 5G", 193, 178, 163, 148, 133, 19),
    "redmi_note11pro_5g": ("Redmi Note 11 Pro 5G", 175, 160, 145, 130, 115, 18),
    "redmi_note11pro": ("Redmi Note 11 Pro", 175, 160, 145, 130, 115, 18),
    "redmi_note11proplus_5g": ("Redmi Note 11 Pro+ 5G", 175, 160, 145, 130, 115, 18),
    "redmi_note11t_5g": ("Redmi Note 11T 5G", 177, 167, 143, 132, 112, 10),
    "redmi_note11tpro": ("Redmi Note 11T Pro", 180, 157, 142, 132, 114, 16),
    "redmi_note11tproplus": ("Redmi Note 11T Pro+", 181, 163, 147, 135, 122, 17),

    "redmi_note12": ("Redmi Note 12", 175, 160, 145, 130, 115, 18),
    "redmi_note12pro_5g": ("Redmi Note 12 Pro 5G", 175, 160, 145, 130, 115, 18),
    "redmi_note12proplus_5g": ("Redmi Note 12 Pro+ 5G", 175, 160, 145, 130, 115, 18),
    "redmi_note12s": ("Redmi Note 12S", 175, 159, 152, 130, 114, 20),
    "redmi_note12turbo": ("Redmi Note 12 Turbo", 182, 160, 152, 134, 120, 13),
    "redmi_note12tpro": ("Redmi Note 12T Pro", 173, 160, 149, 137, 120, 12),

    "redmi_note13": ("Redmi Note 13", 175, 160, 145, 130, 115, 18),
    "redmi_note13_5g": ("Redmi Note 13 5G", 175, 160, 145, 130, 115, 18),
    "redmi_note13pro": ("Redmi Note 13 Pro", 178, 163, 148, 133, 118, 18),
    "redmi_note13proplus_5g": ("Redmi Note 13 Pro+ 5G", 175, 160, 145, 130, 115, 18),
    "redmi_note13r": ("Redmi Note 13R", 179, 159, 152, 131, 118, 10),
    "redmi_note13rpro": ("Redmi Note 13R Pro", 180, 159, 148, 129, 120, 18),

    "redmi_note14": ("Redmi Note 14", 185, 170, 155, 140, 125, 19),
    "redmi_note14_5g": ("Xiaomi Redmi Note 14 5G", 175, 160, 145, 130, 115, 18),
    "redmi_note14pro_5g": ("Xiaomi Redmi Note 14 Pro 5G", 175, 160, 145, 130, 115, 18),
    "redmi_note14proplus_5g": ("Xiaomi Redmi Note 14 Pro+ 5G", 162, 147, 132, 117, 102, 17),
    "redmi_note14pro": ("Xiaomi Redmi Note 14 Pro", 180, 163, 144, 136, 112, 15),
    "redmi_note14s": ("Xiaomi Redmi Note 14S", 181, 167, 152, 134, 116, 19),

    "redmi_note15": ("Xiaomi Redmi Note 15", 175, 157, 144, 129, 118, 14),
    "redmi_note15_5g": ("Xiaomi Redmi Note 15 5G", 176, 166, 142, 133, 120, 18),
    "redmi_note15pro": ("Xiaomi Redmi Note 15 Pro", 178, 163, 145, 130, 122, 13),
    "redmi_note15pro_5g": ("Xiaomi Redmi Note 15 Pro 5G", 179, 164, 149, 127, 115, 20),
    "redmi_note15proplus_5g": ("Xiaomi Redmi Note 15 Pro+ 5G", 178, 157, 151, 127, 112, 18),

    "redmi_note17": ("Xiaomi Redmi Note 17", 180, 161, 150, 134, 114, 11)
}


# =========================================================
# HONOR CONFIGS
# =========================================================
# Рекомендуемые игровые пресеты Free Fire (шкала 0-200).
# Это не официальные настройки HONOR.

HONOR_CONFIGS = {
    "honor_robot_phone": ("HONOR Robot Phone", 194, 179, 164, 149, 134, 20),
    "honor_magicv6": ("HONOR Magic V6", 193, 178, 163, 148, 133, 20),
    "honor_magic8pro": ("HONOR Magic8 Pro", 192, 177, 162, 147, 132, 20),
    "honor_magicv5": ("HONOR Magic V5", 175, 160, 145, 130, 115, 16),
    "honor_magic7rsr": ("HONOR Magic7 RSR", 178, 163, 148, 133, 118, 17),
    "honor_magic7pro": ("HONOR Magic7 Pro", 177, 162, 147, 132, 117, 18),
    "honor_magicv3": ("HONOR Magic V3", 176, 161, 146, 131, 116, 16),
    "honor_magic7": ("HONOR Magic7", 175, 160, 145, 130, 115, 17),
    "honor_magic6rsr": ("HONOR Magic6 RSR", 178, 163, 148, 133, 118, 18),
    "honor_magic6pro": ("HONOR Magic6 Pro", 177, 162, 147, 132, 117, 16),
    "honor_magicv2rsr": ("HONOR Magic V2 RSR", 176, 161, 146, 131, 116, 17),
    "honor_magicv2": ("HONOR Magic V2", 175, 160, 145, 130, 115, 18),
    "honor_magic5pro": ("HONOR Magic5 Pro", 178, 163, 148, 133, 118, 16),
    "honor_magic5ultimate": ("HONOR Magic5 Ultimate", 177, 162, 147, 132, 117, 17),
    "honor_magic5": ("HONOR Magic5", 176, 161, 146, 131, 116, 18),
    "honor_magicvs": ("HONOR Magic Vs", 175, 160, 145, 130, 115, 16),
    "honor_magic4pro": ("HONOR Magic4 Pro", 178, 163, 148, 133, 118, 17),
    "honor_magic4ultimate": ("HONOR Magic4 Ultimate", 177, 162, 147, 132, 117, 18),
    "honor_magicv": ("HONOR Magic V", 176, 161, 146, 131, 116, 16),
    "honor_600pro": ("HONOR 600 Pro", 175, 160, 145, 130, 115, 17),
    "honor_600": ("HONOR 600", 178, 163, 148, 133, 118, 18),
    "honor_600pro_molly": ("HONOR 600 Pro MOLLY Limited Edition", 177, 162, 147, 132, 117, 16),
    "honor_600lite": ("HONOR 600 Lite", 176, 161, 146, 131, 116, 17),
    "honor_600smart5g": ("HONOR 600 Smart 5G", 175, 160, 145, 130, 115, 18),
    "honor_400pro": ("HONOR 400 Pro", 178, 163, 148, 133, 118, 16),
    "honor_400smart5g": ("HONOR 400 Smart 5G", 177, 162, 147, 132, 117, 17),
    "honor_400": ("HONOR 400", 176, 161, 146, 131, 116, 18),
    "honor_400lite": ("HONOR 400 Lite", 175, 160, 145, 130, 115, 16),
    "honor_200smart": ("HONOR 200 Smart", 178, 163, 148, 133, 118, 17),
    "honor_200pro": ("HONOR 200 Pro", 177, 162, 147, 132, 117, 18),
    "honor_200": ("HONOR 200", 176, 161, 146, 131, 116, 16),
    "honor_200lite": ("HONOR 200 Lite", 175, 160, 145, 130, 115, 17),
    "honor_90": ("HONOR 90", 178, 163, 148, 133, 118, 18),
    "honor_90lite": ("HONOR 90 Lite", 177, 162, 147, 132, 117, 16),
    "honor_70pro": ("HONOR 70 Pro", 176, 161, 146, 131, 116, 17),
    "honor_70proplus": ("HONOR 70 Pro+", 175, 160, 145, 130, 115, 18),
    "honor_70": ("HONOR 70", 178, 163, 148, 133, 118, 16),
    "honor_50": ("HONOR 50", 177, 162, 147, 132, 117, 17),
    "honor_50lite": ("HONOR 50 Lite", 176, 161, 146, 131, 116, 18),
    "honor_x9d": ("HONOR X9d", 175, 160, 145, 130, 115, 16),
    "honor_x7eplus": ("HONOR X7e Plus", 178, 163, 148, 133, 118, 17),
    "honor_x7e": ("HONOR X7e", 177, 162, 147, 132, 117, 18),
    "honor_x8d": ("HONOR X8d", 176, 161, 146, 131, 116, 16),
    "honor_x7d": ("HONOR X7d", 175, 160, 145, 130, 115, 17),
    "honor_x6e": ("HONOR X6e", 178, 163, 148, 133, 118, 18),
    "honor_x6d5g": ("HONOR X6d 5G", 177, 162, 147, 132, 117, 16),
    "honor_x5dplus": ("HONOR X5d Plus", 176, 161, 146, 131, 116, 17),
    "honor_x5d": ("HONOR X5d", 175, 160, 145, 130, 115, 18),
    "honor_x5cplus": ("HONOR X5c Plus", 178, 163, 148, 133, 118, 16),
    "honor_x5c": ("HONOR X5c", 177, 162, 147, 132, 117, 17),
    "honor_x9c": ("HONOR X9c", 176, 161, 146, 131, 116, 18),
    "honor_x8c": ("HONOR X8c", 175, 160, 145, 130, 115, 16),
    "honor_x6c": ("HONOR X6c", 178, 163, 148, 133, 118, 17),
    "honor_x9csmart": ("HONOR X9c Smart", 177, 162, 147, 132, 117, 18),
    "honor_x5b": ("HONOR X5b", 176, 161, 146, 131, 116, 16),
    "honor_x7c": ("HONOR X7c", 175, 160, 145, 130, 115, 17),
    "honor_x9b": ("HONOR X9b", 178, 163, 148, 133, 118, 18),
    "honor_x6b": ("HONOR X6b", 177, 162, 147, 132, 117, 16),
    "honor_x7b5g": ("HONOR X7b 5G", 176, 161, 146, 131, 116, 17),
    "honor_x8b": ("HONOR X8b", 175, 160, 145, 130, 115, 18),
    "honor_x7b": ("HONOR X7b", 178, 163, 148, 133, 118, 16),
    "honor_x5plus": ("HONOR X5 Plus", 177, 162, 147, 132, 117, 17),
    "honor_x9a": ("HONOR X9a", 176, 161, 146, 131, 116, 18),
    "honor_x8a5g": ("HONOR X8a 5G", 175, 160, 145, 130, 115, 16),
    "honor_x6a": ("HONOR X6a", 178, 163, 148, 133, 118, 17),
    "honor_x8a": ("HONOR X8a", 177, 162, 147, 132, 117, 18),
    "honor_x7a": ("HONOR X7a", 176, 161, 146, 131, 116, 16),
    "honor_x8_5g": ("HONOR X8 5G", 175, 160, 145, 130, 115, 17),
    "honor_x9_5g": ("HONOR X9 5G", 178, 163, 148, 133, 118, 18),
    "honor_x9": ("HONOR X9", 177, 162, 147, 132, 117, 16),
    "honor_x8": ("HONOR X8", 176, 161, 146, 131, 116, 17),
    "honor_x7": ("HONOR X7", 175, 160, 145, 130, 115, 18),
    "honor_x6": ("HONOR X6", 178, 163, 148, 133, 118, 16),
    "honor_x5": ("HONOR X5", 177, 162, 147, 132, 117, 17),
    "honor_play20a": ("HONOR Play20A", 176, 161, 146, 131, 116, 18),
    "honor_play20c": ("HONOR Play20C", 175, 160, 145, 130, 115, 16)
}

# =========================================================
# GENERIC OTHER MODELS
# =========================================================

def get_generic_models(brand_name):

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"{brand_name} Модель 1",
                    callback_data=f"model_{brand_name.lower()}1"
                )
            ],
            [
                InlineKeyboardButton(
                    text=f"{brand_name} Модель 2",
                    callback_data=f"model_{brand_name.lower()}2"
                )
            ],
            [
                InlineKeyboardButton(
                    text="⬅️ Назад к брендам",
                    callback_data="back_to_brands"
                )
            ]
        ]
    )

# =========================================================
# TARIFFS
# =========================================================

def get_tariffs_keyboard(model_name):

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🟢 Базовая настройка (1 invite)",
                    callback_data=f"tariff_base_{model_name}"
                )
            ],
            [
                InlineKeyboardButton(
                    text="🟡 Premium настройка (2 invite)",
                    callback_data=f"tariff_prem_{model_name}"
                )
            ],
            [
                InlineKeyboardButton(
                    text="🔴 VIP настройка (3 invite)",
                    callback_data=f"tariff_vip_{model_name}"
                )
            ],
            [
                InlineKeyboardButton(
                    text="⬅️ Назад",
                    callback_data="back_to_brands"
                )
            ]
        ]
    )

# =========================================================
# CONFIG FUNCTIONS
# =========================================================

def get_config(configs, model_name):

    data = configs.get(model_name)

    if not data:
        return None

    name, general, red_dot, scope2, scope4, sniper, free_look = data

    return (
        f"📱 Device: {name} (8GB)\n\n"
        f"📅 Date: 24 Sep 2026\n"
        f"🎯 PRO SENSITIVITY CONFIG:\n\n"
        f"- General: {general}\n"
        f"- Red Dot: {red_dot}\n"
        f"- 2x Scope: {scope2}\n"
        f"- 4x Scope: {scope4}\n"
        f"- Sniper: {sniper}\n"
        f"- Free Look: {free_look}\n\n"
        f"🚀 Generated by Sensitivity Pro"
    )


def get_xiaomi_config(model_name, tariff_type):

    data = XIAOMI_CONFIGS.get(model_name)

    if not data:
        return None

    name, general, red_dot, scope2, scope4, sniper, free_look = data

    if tariff_type == "prem":

        general = max(1, general - 5)
        red_dot = max(1, red_dot - 5)
        scope2 = max(1, scope2 - 5)
        scope4 = max(1, scope4 - 5)
        sniper = max(1, sniper - 5)

        if free_look > 15:
            free_look -= 1

    elif tariff_type == "base":

        general = max(1, general - 10)
        red_dot = max(1, red_dot - 10)
        scope2 = max(1, scope2 - 10)
        scope4 = max(1, scope4 - 10)
        sniper = max(1, sniper - 10)

        if free_look > 16:
            free_look -= 2

        elif free_look > 10:
            free_look -= 1

    return (
        f"📱 Device: {name} (8GB)\n\n"
        f"📅 Date: 24 Sep 2026\n"
        f"🎯 PRO SENSITIVITY CONFIG:\n\n"
        f"- General: {general}\n"
        f"- Red Dot: {red_dot}\n"
        f"- 2x Scope: {scope2}\n"
        f"- 4x Scope: {scope4}\n"
        f"- Sniper: {sniper}\n"
        f"- Free Look: {free_look}\n\n"
        f"🚀 Generated by Sensitivity Pro"
    )


def get_iphone_config(model_name):
    return get_config(IPHONE_CONFIGS, model_name)


def get_samsung_config(model_name):
    return get_config(SAMSUNG_CONFIGS, model_name)


def get_poco_config(model_name):
    return get_config(POCO_CONFIGS, model_name)


def get_tecno_config(model_name):
    return get_config(TECNO_CONFIGS, model_name)


def get_infinix_config(model_name):
    return get_config(INFINIX_CONFIGS, model_name)


def get_realme_config(model_name):
    return get_config(REALME_CONFIGS, model_name)

def get_honor_config(model_name):
    return get_config(HONOR_CONFIGS, model_name)

# =========================================================
# GET MODEL DISPLAY NAME
# =========================================================

def get_model_display_name(model_name):

    all_configs = [
        IPHONE_CONFIGS,
        SAMSUNG_CONFIGS,
        POCO_CONFIGS,
        TECNO_CONFIGS,
        INFINIX_CONFIGS,
        REALME_CONFIGS,
        HONOR_CONFIGS,
        XIAOMI_CONFIGS
    ]

    for configs in all_configs:

        if model_name in configs:
            return configs[model_name][0]

    return model_name.upper()

# =========================================================
# /START
# =========================================================

@dp.message(F.text.startswith("/start"))
async def start_cmd(message: Message):

    user_id = message.from_user.id

    # -----------------------------------------
    # Получаем referral parameter
    # -----------------------------------------

    args = message.text.split()

    referrer_id = None

    if len(args) > 1:

        referral_parameter = args[1].strip()

        if referral_parameter.isdigit():
            referrer_id = int(referral_parameter)

    # Запрещаем self-referral
    if referrer_id == user_id:
        referrer_id = None

    # -----------------------------------------
    # Проверяем пользователя
    # -----------------------------------------

    cursor.execute(
        """
        SELECT user_id, referrer_id
        FROM users
        WHERE user_id = ?
        """,
        (user_id,)
    )

    existing_user = cursor.fetchone()

    # -----------------------------------------
    # НОВЫЙ USER
    # -----------------------------------------

    if not existing_user:

        cursor.execute(
            """
            INSERT INTO users (
                user_id,
                referrer_id,
                invites_count,
                referral_confirmed
            )
            VALUES (?, ?, 0, 0)
            """,
            (user_id, referrer_id)
        )

        conn.commit()

    # -----------------------------------------
    # USER УЖЕ ЕСТЬ
    # -----------------------------------------

    else:

        old_referrer_id = existing_user[1]

        # Если раньше реферера не было,
        # разрешаем один раз привязать его.
        #
        # ВАЖНО:
        # invite здесь НЕ начисляется.
        # Он начислится только после подписки
        # на все 3 канала.

        if old_referrer_id is None and referrer_id:

            cursor.execute(
                """
                SELECT user_id
                FROM users
                WHERE user_id = ?
                """,
                (referrer_id,)
            )

            referrer_exists = cursor.fetchone()

            if referrer_exists:

                cursor.execute(
                    """
                    UPDATE users
                    SET referrer_id = ?
                    WHERE user_id = ?
                    AND referrer_id IS NULL
                    """,
                    (referrer_id, user_id)
                )

                conn.commit()

    # -----------------------------------------
    # SUBSCRIPTION
    # -----------------------------------------

    if await check_subscriptions(user_id):

        # Только после успешной подписки
        # засчитываем referral
        await confirm_referral(user_id)

        await message.answer(
            "✅ Доступ открыт!\n\n"
            "📱 Выберите бренд вашего телефона:",
            reply_markup=get_brands_keyboard()
        )

    else:

        await message.answer(
            "❌ Для использования бота вы должны "
            "быть подписаны на все 3 канала!",
            reply_markup=get_sub_keyboard()
        )

# =========================================================
# CHECK SUBSCRIPTION
# =========================================================

@dp.callback_query(F.data == "check_sub")
async def process_check_sub(callback: CallbackQuery):

    user_id = callback.from_user.id

    if await check_subscriptions(user_id):

        # Только здесь подтверждаем referral
        await confirm_referral(user_id)

        await callback.message.edit_text(
            "✅ Подписка подтверждена!\n\n"
            "📱 Выберите бренд вашего телефона:",
            reply_markup=get_brands_keyboard()
        )

        await callback.answer()

    else:

        await callback.answer(
            "❌ Вы всё ещё не подписались на все каналы!",
            show_alert=True
        )

# =========================================================
# IPHONE BRAND
# =========================================================

@dp.callback_query(F.data == "brand_iphone")
async def process_iphone_brand(callback: CallbackQuery):

    await callback.message.edit_text(
        "📱 Выберите серию iPhone:",
        reply_markup=get_iphone_groups()
    )

    await callback.answer()


@dp.callback_query(F.data.startswith("iphone_group_"))
async def process_iphone_group(callback: CallbackQuery):

    group = callback.data.replace(
        "iphone_group_",
        ""
    )

    group_map = {
        "8": "8",
        "x": "X",
        "11": "11",
        "12": "12",
        "13": "13",
        "14": "14",
        "15": "15",
        "16": "16",
        "17": "17"
    }

    group_name = group_map.get(group, group)

    await callback.message.edit_text(
        f"📱 Выберите модель iPhone {group_name}:",
        reply_markup=get_iphone_group_models(group_name)
    )

    await callback.answer()

# =========================================================
# SAMSUNG
# =========================================================

@dp.callback_query(F.data == "brand_samsung")
async def process_samsung(callback: CallbackQuery):

    await callback.message.edit_text(
        "📱 Выберите серию Samsung:",
        reply_markup=get_samsung_groups()
    )

    await callback.answer()


@dp.callback_query(F.data.startswith("samsung_group_"))
async def process_samsung_group(callback: CallbackQuery):

    group = callback.data.replace(
        "samsung_group_",
        ""
    ).upper()

    await callback.message.edit_text(
        f"📱 Выберите модель Samsung Galaxy {group}:",
        reply_markup=get_samsung_group_models(group)
    )

    await callback.answer()

# =========================================================
# POCO
# =========================================================

@dp.callback_query(F.data == "brand_poco")
async def process_poco(callback: CallbackQuery):

    await callback.message.edit_text(
        "📱 Выберите серию POCO:",
        reply_markup=get_poco_groups()
    )

    await callback.answer()


@dp.callback_query(F.data.startswith("poco_group_"))
async def process_poco_group(callback: CallbackQuery):

    group = callback.data.replace(
        "poco_group_",
        ""
    ).upper()

    await callback.message.edit_text(
        f"📱 Выберите модель POCO {group}:",
        reply_markup=get_poco_group_models(group)
    )

    await callback.answer()

# =========================================================
# TECNO
# =========================================================

@dp.callback_query(F.data == "brand_tecno")
async def process_tecno(callback: CallbackQuery):

    await callback.message.edit_text(
        "📱 Выберите серию TECNO:",
        reply_markup=get_tecno_groups()
    )

    await callback.answer()


@dp.callback_query(F.data.startswith("tecno_group_"))
async def process_tecno_group(callback: CallbackQuery):

    group = callback.data.replace(
        "tecno_group_",
        ""
    )

    group_map = {
        "spark": "Spark",
        "phantom": "Phantom",
        "camon": "Camon",
        "pova": "Pova",
        "pop": "Pop"
    }

    group_name = group_map.get(group, group)

    await callback.message.edit_text(
        f"📱 Выберите модель TECNO {group_name}:",
        reply_markup=get_tecno_group_models(group_name)
    )

    await callback.answer()

# =========================================================
# INFINIX
# =========================================================

@dp.callback_query(F.data == "brand_infinix")
async def process_infinix(callback: CallbackQuery):

    await callback.message.edit_text(
        "📱 Выберите серию Infinix:",
        reply_markup=get_infinix_groups()
    )

    await callback.answer()


@dp.callback_query(F.data.startswith("infinix_group_"))
async def process_infinix_group(callback: CallbackQuery):

    group = callback.data.replace(
        "infinix_group_",
        ""
    )

    group_map = {
        "note": "Note",
        "hot": "Hot",
        "gt": "GT",
        "zero": "Zero"
    }

    group_name = group_map.get(
        group,
        group
    )

    await callback.message.edit_text(
        f"📱 Выберите модель Infinix {group_name}:",
        reply_markup=get_infinix_group_models(group_name)
    )

    await callback.answer()

# =========================================================
# REALME
# =========================================================

@dp.callback_query(F.data == "brand_realme")
async def process_realme(callback: CallbackQuery):

    await callback.message.edit_text(
        "📱 Выберите серию Realme:",
        reply_markup=get_realme_groups()
    )

    await callback.answer()


@dp.callback_query(F.data.startswith("realme_group_"))
async def process_realme_group(callback: CallbackQuery):

    group = callback.data.replace(
        "realme_group_",
        ""
    )

    group_map = {
        "gt": "GT",
        "number": "Number",
        "c": "C",
        "narzo": "Narzo"
    }

    group_name = group_map.get(
        group,
        group
    )

    await callback.message.edit_text(
        f"📱 Выберите модель Realme {group_name}:",
        reply_markup=get_realme_group_models(group_name)
    )

    await callback.answer()


# =========================================================
# HONOR
# =========================================================

@dp.callback_query(F.data == "brand_honor")
async def process_honor(callback: CallbackQuery):

    await callback.message.edit_text(
        "📱 Выберите серию HONOR:",
        reply_markup=get_honor_groups()
    )

    await callback.answer()


@dp.callback_query(F.data.startswith("honor_group_"))
async def process_honor_group(callback: CallbackQuery):

    group = callback.data.replace(
        "honor_group_",
        ""
    )

    group_map = {
        "magic": "Magic",
        "n": "N",
        "x": "X",
        "play": "Play"
    }

    group_name = group_map.get(
        group,
        group
    )

    await callback.message.edit_text(
        f"📱 Выберите модель HONOR {group_name}:",
        reply_markup=get_honor_group_models(group_name)
    )

    await callback.answer()

# =========================================================
# XIAOMI
# =========================================================

@dp.callback_query(F.data == "brand_xiaomi")
async def process_xiaomi(callback: CallbackQuery):

    await callback.message.edit_text(
        "📱 Выберите серию Xiaomi:",
        reply_markup=get_xiaomi_groups()
    )

    await callback.answer()


@dp.callback_query(F.data.startswith("xiaomi_group_"))
async def process_xiaomi_group(callback: CallbackQuery):

    group = callback.data.replace(
        "xiaomi_group_",
        ""
    )

    group_map = {
        "mi": "Mi",
        "t": "T",
        "redminote": "Redmi Note"
    }

    group_name = group_map.get(
        group,
        group
    )

    await callback.message.edit_text(
        f"📱 Выберите модель Xiaomi {group_name}:",
        reply_markup=get_xiaomi_group_models(group_name)
    )

    await callback.answer()

# =========================================================
# OPPO / VIVO SPECIAL SETTINGS
# =========================================================

OPPO_MODELS = [
    ("OPPO Find X8 Pro", "oppo_find_x8_pro"),
    ("OPPO Find X8", "oppo_find_x8"),
    ("OPPO Find X7 Ultra", "oppo_find_x7_ultra"),
    ("OPPO Reno 13 Pro", "oppo_reno_13_pro"),
    ("OPPO Reno 13", "oppo_reno_13"),
    ("OPPO Reno 12 Pro", "oppo_reno_12_pro"),
    ("OPPO Reno 12", "oppo_reno_12"),
    ("OPPO Reno 11 Pro", "oppo_reno_11_pro"),
    ("OPPO Reno 11", "oppo_reno_11"),
    ("OPPO A5 Pro", "oppo_a5_pro"),
    ("OPPO A3 Pro", "oppo_a3_pro"),
    ("OPPO A79", "oppo_a79"),
    ("OPPO A58", "oppo_a58"),
    ("OPPO A78", "oppo_a78"),
]

VIVO_MODELS = [
    ("Vivo X200 Pro", "vivo_x200_pro"),
    ("Vivo X200", "vivo_x200"),
    ("Vivo X100 Pro", "vivo_x100_pro"),
    ("Vivo X100", "vivo_x100"),
    ("Vivo V50", "vivo_v50"),
    ("Vivo V40 Pro", "vivo_v40_pro"),
    ("Vivo V40", "vivo_v40"),
    ("Vivo V30 Pro", "vivo_v30_pro"),
    ("Vivo V30", "vivo_v30"),
    ("Vivo Y200", "vivo_y200"),
    ("Vivo Y100", "vivo_y100"),
    ("Vivo Y36", "vivo_y36"),
    ("Vivo Y28", "vivo_y28"),
    ("Vivo Y18", "vivo_y18"),
]

def get_oppo_models():
    rows = []
    for i in range(0, len(OPPO_MODELS), 2):
        row = [
            InlineKeyboardButton(text=OPPO_MODELS[i][0], callback_data=f"model_{OPPO_MODELS[i][1]}")
        ]
        if i + 1 < len(OPPO_MODELS):
            row.append(InlineKeyboardButton(text=OPPO_MODELS[i+1][0], callback_data=f"model_{OPPO_MODELS[i+1][1]}"))
        rows.append(row)
    rows.append([InlineKeyboardButton(text="⬅️ Назад к брендам", callback_data="back_to_brands")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def get_vivo_models():
    rows = []
    for i in range(0, len(VIVO_MODELS), 2):
        row = [
            InlineKeyboardButton(text=VIVO_MODELS[i][0], callback_data=f"model_{VIVO_MODELS[i][1]}")
        ]
        if i + 1 < len(VIVO_MODELS):
            row.append(InlineKeyboardButton(text=VIVO_MODELS[i+1][0], callback_data=f"model_{VIVO_MODELS[i+1][1]}"))
        rows.append(row)
    rows.append([InlineKeyboardButton(text="⬅️ Назад к брендам", callback_data="back_to_brands")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@dp.callback_query(F.data == "brand_oppo")
async def process_oppo(callback: CallbackQuery):
    await callback.message.edit_text(
        "📱 Выберите модель OPPO:",
        reply_markup=get_oppo_models()
    )
    await callback.answer()


@dp.callback_query(F.data == "brand_vivo")
async def process_vivo(callback: CallbackQuery):
    await callback.message.edit_text(
        "📱 Выберите модель Vivo:",
        reply_markup=get_vivo_models()
    )
    await callback.answer()


@dp.callback_query(F.data == "special_start")
async def process_special_start(callback: CallbackQuery):
    user_id = callback.from_user.id

    cursor.execute(
        """
        SELECT invites_count
        FROM users
        WHERE user_id = ?
        """,
        (user_id,)
    )
    row = cursor.fetchone()
    current_invites = row[0] if row else 0

    cursor.execute(
        """
        UPDATE users
        SET special_active = 1,
            special_brand = ?,
            special_model = NULL,
            special_start_invites = ?,
            special_completed = 0
        WHERE user_id = ?
        """,
        ("OPPO/Vivo", current_invites, user_id)
    )
    conn.commit()

    await callback.message.edit_text(
        "⚙️ Особенная настройка\n\n"
        "Напишите точную модель вашего телефона.\n"
        "Например: OPPO Reno 13 Pro или Vivo X200 Pro.\n\n"
        "После этого нужно пригласить 5 новых пользователей.\n"
        "Каждый пользователь должен зайти по вашей "
        "реферальной ссылке и подписаться на все 3 канала.\n\n"
        "После 5 подтверждённых invite заявка автоматически "
        "отправится администратору.",
        reply_markup=get_special_progress_keyboard()
    )

    await callback.answer()


# =========================================================
# SPECIAL MODEL TEXT INPUT
# =========================================================

@dp.message()
async def process_special_model_text(message: Message):
    # /start обрабатывается отдельным handler выше.
    if not message.text or message.text.startswith("/"):
        return

    user_id = message.from_user.id

    cursor.execute(
        """
        SELECT special_active, special_brand, special_model,
               special_start_invites, special_completed
        FROM users
        WHERE user_id = ?
        """,
        (user_id,)
    )
    row = cursor.fetchone()

    if not row:
        return

    (
        special_active,
        special_brand,
        special_model,
        special_start_invites,
        special_completed
    ) = row

    if not special_active or special_completed:
        return

    model = message.text.strip()

    if len(model) < 2:
        await message.answer(
            "❌ Напишите точную модель телефона."
        )
        return

    # Для особенной настройки принимаем только OPPO или Vivo.
    model_lower = model.lower()
    if not (model_lower.startswith("oppo") or model_lower.startswith("vivo")):
        await message.answer(
            "❌ Для особенной настройки нужно указать модель OPPO или Vivo.\n\n"
            "Например: OPPO Reno 13 Pro или Vivo X200 Pro."
        )
        return

    # Автоматически определяем бренд из введённой модели.
    special_brand = "OPPO" if model_lower.startswith("oppo") else "VIVO"

    cursor.execute(
        """
        UPDATE users
        SET special_model = ?,
            special_brand = ?
        WHERE user_id = ?
        """,
        (model, special_brand, user_id)
    )
    conn.commit()

    cursor.execute(
        """
        SELECT invites_count
        FROM users
        WHERE user_id = ?
        """,
        (user_id,)
    )
    invite_row = cursor.fetchone()
    current_invites = invite_row[0] if invite_row else 0
    start_invites = special_start_invites or 0
    progress = max(0, current_invites - start_invites)
    remaining = max(0, 5 - progress)

    # На случай, если 5 invite уже были набраны после старта заявки.
    if remaining == 0:
        completed = await complete_special_request(user_id)
        if completed:
            return

    bot_info = await bot.get_me()
    ref_link = (
        f"https://t.me/{bot_info.username}?start={user_id}"
    )

    await message.answer(
        f"✅ Модель сохранена: {model}\n\n"
        f"📱 Бренд: {(special_brand or '').upper()}\n"
        f"👥 Прогресс: {progress}/5 invite\n"
        f"📣 Осталось пригласить: {remaining}\n\n"
        "🔗 Ваша реферальная ссылка:\n"
        f"{ref_link}\n\n"
        "Отправьте ссылку друзьям.\n"
        "Invite засчитывается после того, как новый пользователь "
        "зайдёт по вашей ссылке и подпишется на все 3 канала.\n\n"
        "После 5 подтверждённых invite заявка автоматически "
        "уйдёт администратору.",
        reply_markup=get_special_progress_keyboard()
    )


# =========================================================
# OTHER BRANDS
# =========================================================

@dp.callback_query(F.data.startswith("brand_"))
async def process_other_brands(callback: CallbackQuery):

    brand_name = callback.data.split(
        "_",
        1
    )[1]

    await callback.message.edit_text(
        f"📱 Выберите модель вашего телефона "
        f"{brand_name.upper()}:",
        reply_markup=get_generic_models(
            brand_name
        )
    )

    await callback.answer()

# =========================================================
# BACK TO BRANDS
# =========================================================

@dp.callback_query(F.data == "back_to_brands")
async def process_back_brands(callback: CallbackQuery):

    await callback.message.edit_text(
        "📱 Выберите бренд вашего телефона:",
        reply_markup=get_brands_keyboard()
    )

    await callback.answer()

# =========================================================
# MODEL
# =========================================================

@dp.callback_query(F.data.startswith("model_"))
async def process_model(callback: CallbackQuery):

    model_name = callback.data.split(
        "_",
        1
    )[1]

    display_name = get_model_display_name(
        model_name
    )

    await callback.message.edit_text(
        "⚙️ Выберите уровень настроек:\n\n"
        f"📱 {display_name}\n\n"
        "🟢 Base — 1 invite\n"
        "🟡 Premium — 2 invite\n"
        "🔴 VIP — 3 invite",
        reply_markup=get_tariffs_keyboard(
            model_name
        )
    )

    await callback.answer()

# =========================================================
# TARIFF
# =========================================================

@dp.callback_query(F.data.startswith("tariff_"))
async def process_tariff(callback: CallbackQuery):

    data = callback.data.split(
        "_",
        2
    )

    if len(data) < 3:

        await callback.answer(
            "Ошибка тарифа",
            show_alert=True
        )

        return

    tariff_type = data[1]
    model_name = data[2]

    # =========================================
    # INVITE REQUIREMENTS
    # =========================================

    required_invites = {
        "base": 1,
        "prem": 2,
        "vip": 3
    }

    if tariff_type not in required_invites:

        await callback.answer(
            "Неизвестный тариф",
            show_alert=True
        )

        return

    required = required_invites[
        tariff_type
    ]

    # =========================================
    # TARIFF NAMES
    # =========================================

    tariff_names = {
        "base": "🟢 Базовая настройка",
        "prem": "🟡 Premium настройка",
        "vip": "🔴 VIP настройка"
    }

    user_id = callback.from_user.id

    # =========================================
    # GET INVITES
    # =========================================

    cursor.execute(
        """
        SELECT invites_count
        FROM users
        WHERE user_id = ?
        """,
        (user_id,)
    )

    row = cursor.fetchone()

    current_invites = (
        row[0]
        if row
        else 0
    )

    # =========================================
    # REFERRAL LINK
    # =========================================

    bot_info = await bot.get_me()

    bot_username = bot_info.username

    ref_link = (
        f"https://t.me/"
        f"{bot_username}"
        f"?start={user_id}"
    )

    # =========================================
    # NOT ENOUGH INVITES
    # =========================================

    if current_invites < required:

        needed = (
            required -
            current_invites
        )

        await callback.message.answer(
            f"❌ Недостаточно invite!\n\n"
            f"⚙️ Тариф: "
            f"{tariff_names[tariff_type]}\n"
            f"📊 У вас invite: "
            f"{current_invites} из {required}\n"
            f"📣 Нужно пригласить ещё: "
            f"{needed}\n\n"
            f"🔗 Ваша реферальная ссылка:\n"
            f"{ref_link}\n\n"
            f"Отправьте эту ссылку друзьям.\n"
            f"Когда новый пользователь "
            f"зайдёт в бота по вашей ссылке "
            f"и подпишется на все 3 канала, "
            f"вам засчитается +1 invite."
        )

        await callback.answer()

        return

    # =========================================
    # GET CONFIG
    # =========================================

    text_config = None

    # iPhone
    text_config = get_iphone_config(
        model_name
    )

    # Samsung
    if not text_config:

        text_config = get_samsung_config(
            model_name
        )

    # POCO
    if not text_config:

        text_config = get_poco_config(
            model_name
        )

    # TECNO
    if not text_config:

        text_config = get_tecno_config(
            model_name
        )

    # INFINIX
    if not text_config:

        text_config = get_infinix_config(
            model_name
        )

    # REALME
    if not text_config:

        text_config = get_realme_config(
            model_name
        )

    # HONOR
    if not text_config:

        text_config = get_honor_config(
            model_name
        )

    # XIAOMI / REDMI
    if not text_config:

        text_config = get_xiaomi_config(
            model_name,
            tariff_type
        )

    # =========================================
    # FALLBACK
    # =========================================

    if not text_config:

        display_name = get_model_display_name(
            model_name
        )

        text_config = (
            f"{tariff_names[tariff_type]}\n\n"
            f"📱 Device: "
            f"{display_name} (8GB)\n\n"
            f"📅 Date: 24 Sep 2026\n"
            f"🎯 PRO SENSITIVITY CONFIG:\n\n"
            f"- General: 175\n"
            f"- Red Dot: 160\n"
            f"- 2x Scope: 145\n"
            f"- 4x Scope: 130\n"
            f"- Sniper: 115\n"
            f"- Free Look: 18\n\n"
            f"🚀 Generated by Sensitivity Pro"
        )

    # =========================================
    # СПИСЫВАЕМ INVITE
    # =========================================
    #
    # ВАЖНО:
    # Списываем только после того,
    # как настройка найдена.
    #
    # Поэтому:
    #
    # 1 invite -> Base -> остаётся 0
    # 2 invite -> Premium -> остаётся 0
    # 3 invite -> VIP -> остаётся 0
    #
    # Для следующего устройства нужны
    # новые invite.

    cursor.execute(
        """
        UPDATE users
        SET invites_count = invites_count - ?
        WHERE user_id = ?
        AND invites_count >= ?
        """,
        (
            required,
            user_id,
            required
        )
    )

    if cursor.rowcount == 0:

        await callback.answer(
            "❌ Недостаточно invite!",
            show_alert=True
        )

        return

    conn.commit()

    # =========================================
    # SEND CONFIG
    # =========================================

    await callback.message.answer(
        text_config
    )

    await callback.answer(
        "✅ Настройка готова!"
    )

# =========================================================
# RUN
# =========================================================

async def main():

    logging.info(
        "🚀 Sensitivity Pro started"
    )

    await dp.start_polling(
        bot
    )


if __name__ == "__main__":

    asyncio.run(main())
