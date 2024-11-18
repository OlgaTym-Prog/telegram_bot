import os
import telebot
from dotenv import load_dotenv
import random
import psycopg2
from telebot import types, custom_filters
from telebot.handler_backends import State, StatesGroup
from telebot.storage import StateMemoryStorage
from PIL import Image

# Загружаем переменные из .env
load_dotenv()

db_name = os.getenv('DB_NAME')
db_user = os.getenv('DB_USER')
db_password = os.getenv('DB_PASSWORD')
db_host = os.getenv('DB_HOST')
db_port = os.getenv('DB_PORT')

print('Start telegram bot...')

state_storage = StateMemoryStorage()
token_bot = os.getenv('TOKEN')
bot = telebot.TeleBot(token_bot, state_storage=state_storage)

# Подключение к базе данных PostgreSQL
conn = psycopg2.connect(
    dbname=db_name,
    user=db_user,
    password=db_password,
    host=db_host,
    port=db_port
)
cursor = conn.cursor()

# Получаем путь к изображению из переменной окружения
image_path = os.getenv("img")
if image_path:
    try:
        image = Image.open(image_path)
        image.save("converted_image.png", "PNG")
    except Exception as e:
        print(f"Произошла ошибка при обработке изображения: {e}")
else:
    print("Переменная окружения img не задана.")

# Очистка базы данных и создание новых таблиц
with conn.cursor() as cur:
    cur.execute("""
    DROP TABLE IF EXISTS user_words, words, users
    """)

    # Таблица пользователей
    cur.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id SERIAL PRIMARY KEY,
        user_id INTEGER UNIQUE NOT NULL,
        username VARCHAR(255) NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)

    # Общий словарь
    cur.execute("""
        CREATE TABLE IF NOT EXISTS words (
            id SERIAL PRIMARY KEY,
            target_word VARCHAR(255) UNIQUE NOT NULL,
            translate_word VARCHAR(255) NOT NULL
        )
        """)

    # Персональный словарь
    cur.execute("""
    CREATE TABLE IF NOT EXISTS user_words (
        id SERIAL PRIMARY KEY,
        user_id INTEGER NOT NULL REFERENCES users (user_id),
        target_word VARCHAR(255) NOT NULL,
        translate_word VARCHAR(255) NOT NULL,
        UNIQUE (user_id, target_word)
    )
    """)

    conn.commit()


# Проверка и создание пользователя
def ensure_user_exists(user_id, username):
    cursor.execute("""
    INSERT INTO users (user_id, username)
    VALUES (%s, %s)
    ON CONFLICT (user_id) DO NOTHING
    """, (user_id, username))
    conn.commit()


# Состояния
class MyStates(StatesGroup):
    target_word = State()
    translate_word = State()


# Общие слова для обучения
common_words = [
    ('Peace', 'Мир'), ('Green', 'Зелёный'), ('White', 'Белый'),
    ('Hello', 'Привет'), ('Car', 'Машина'), ('Sky', 'Небо'),
    ('Tree', 'Дерево'), ('Book', 'Книга'), ('Love', 'Любовь'),
    ('Friend', 'Друг')
]

# Заполнение общего словаря
cursor.executemany("""
INSERT INTO words (target_word, translate_word)
SELECT %s, %s WHERE NOT EXISTS (
    SELECT 1 FROM words WHERE target_word = %s AND translate_word = %s
)
""", [(w[0], w[1], w[0], w[1]) for w in common_words])
conn.commit()


# Обработчики
@bot.message_handler(commands=['start', 'cards'])
def send_welcome(message):
    cid = message.chat.id
    username = message.chat.username or "Unknown"
    ensure_user_exists(cid, username)

    print("Starting bot for the first time...")

    sti = open("converted_image.png", 'rb')
    bot.send_sticker(cid, sti)

    bot.send_message(cid, f"Приветствую, {message.from_user.first_name}!\nЯ {bot.get_me().first_name}!\n"
                          f"Начнём учить язык 🇬🇧", parse_mode='html')
    create_cards(message)


def create_cards(message):
    cid = message.chat.id

    # Очистка предыдущего состояния
    print(f"Deleting state for user {message.from_user.id}, chat {message.chat.id}")
    bot.delete_state(message.from_user.id, message.chat.id)

    # Получение случайного слова
    cursor.execute("""
        SELECT target_word, translate_word FROM words
        WHERE NOT EXISTS (
            SELECT 1 FROM user_words WHERE user_id = %s AND words.target_word = user_words.target_word
        )
        ORDER BY RANDOM() LIMIT 1
        """, (cid,))
    word = cursor.fetchone()
    print(f"Fetched word: {word}")

    if not word:
        bot.send_message(cid, "Все слова изучены! Добавьте новые через 'Добавить слово ➕'.")
        print("Все слова изучены.")
        return

    target_word, translate_word = word

    # Установка нового состояния
    print(f"Setting state for user {message.from_user.id}, chat {message.chat.id} to {MyStates.target_word}")
    bot.set_state(user_id=message.from_user.id, chat_id=message.chat.id, state=MyStates.target_word)
    with bot.retrieve_data(message.from_user.id, message.chat.id) as data:
        data['target_word'] = target_word
        data['translate_word'] = translate_word

    print(f"Data saved to state: {data}")

    # Создаём кнопки для вариантов ответа
    cursor.execute("""
        SELECT target_word FROM words 
        WHERE target_word != %s 
        ORDER BY RANDOM() LIMIT 3
        """, (target_word,))
    other_words = [w[0] for w in cursor.fetchall()]
    options = other_words + [target_word]
    random.shuffle(options)

    markup = types.ReplyKeyboardMarkup(row_width=2)
    buttons = [types.KeyboardButton(option) for option in options]
    buttons.append(types.KeyboardButton('Добавить слово ➕'))
    buttons.append(types.KeyboardButton('Удалить слово 🔙'))
    markup.add(*buttons)

    # Отправляем сообщение
    greeting = f"Выбери перевод слова:\n🇷🇺 {translate_word}"
    bot.send_message(cid, greeting, reply_markup=markup)


# @bot.message_handler(func=lambda message: message.text == 'Добавить слово ➕')
# def add_word(message):
#     bot.send_message(message.chat.id, "Введите слово на английском:")
#     bot.set_state(message.from_user.id, MyStates.target_word, message.chat.id)
#
#
# @bot.message_handler(state=MyStates.target_word, content_types=['text'])
# def process_target_word(message):
#     with bot.retrieve_data(message.from_user.id, message.chat.id) as data:
#         data['target_word'] = message.text
#     bot.send_message(message.chat.id, "Введите перевод на русском:")
#     bot.set_state(message.from_user.id, MyStates.translate_word, message.chat.id)
#
#
# @bot.message_handler(state=MyStates.translate_word, content_types=['text'])
# def process_translate_word(message):
#     with bot.retrieve_data(message.from_user.id, message.chat.id) as data:
#         target_word = data['target_word']
#         translate_word = message.text
#         user_id = message.chat.id
#         cursor.execute("""
#         INSERT INTO words (user_id, target_word, translate_word)
#         VALUES (%s, %s, %s)
#         ON CONFLICT (user_id, target_word) DO NOTHING
#         """, (user_id, target_word, translate_word))
#         conn.commit()
#     bot.send_message(message.chat.id, f"Слово '{target_word}' добавлено!")
#     create_cards(message)
#
#
# @bot.message_handler(func=lambda message: message.text == 'Удалить слово 🔙')
# def delete_word(message):
#     bot.send_message(message.chat.id, "Введите слово на английском, которое вы хотите удалить:")
#     bot.set_state(message.from_user.id, MyStates.target_word, message.chat.id)
#
#
# @bot.message_handler(state=MyStates.target_word, content_types=['text'])
# def process_delete_word(message):
#     target_word = message.text
#     user_id = message.chat.id
#     cursor.execute("""
#     DELETE FROM words
#     WHERE user_id = %s
#     AND target_word = %s
#     """, (user_id, target_word))
#     conn.commit()
#     bot.send_message(message.chat.id, f"Слово '{target_word}' удалено!")
#     create_cards(message)


@bot.message_handler(func=lambda message: True, content_types=['text'])
def message_reply(message):
    user_response = message.text

    print(f"User response: {user_response}")

    # Проверяем текущее состояние
    state = bot.get_state(user_id=message.from_user.id, chat_id=message.chat.id)
    print(f"Retrieved state for user {message.from_user.id}, chat {message.chat.id}: {state}")

    if state != MyStates.target_word:
        bot.send_message(message.chat.id, "Ошибка! Начните заново с /start.")
        return

    # Извлекаем данные из состояний
    with bot.retrieve_data(message.from_user.id, message.chat.id) as data:
        target_word = data.get('target_word')
        translate_word = data.get('translate_word')
        print(f"Retrieved state: target_word={target_word}, translate_word={translate_word}")

        if not target_word or not translate_word:
            bot.send_message(message.chat.id, "Ошибка! Попробуй снова начать с /start.")
            return

        if user_response == target_word:
            cursor.execute("""
            INSERT INTO user_words (user_id, target_word, translate_word)
            VALUES (%s, %s, %s)
            ON CONFLICT (user_id, target_word) DO NOTHING
            """, (message.chat.id, target_word, translate_word))
            conn.commit()

            bot.send_message(message.chat.id, f"✅ Правильно!\n{target_word} => {translate_word}!")
            data.clear()
            create_cards(message)
        else:
            bot.send_message(message.chat.id, f"❌ Неправильно! Попробуй снова.\nПеревод: {translate_word}")


bot.add_custom_filter(custom_filters.StateFilter(bot))
bot.infinity_polling(timeout=10, long_polling_timeout=5, skip_pending=True)
