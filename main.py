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
try:
    conn = psycopg2.connect(
        dbname=db_name,
        user=db_user,
        password=db_password,
        host=db_host,
        port=db_port
    )
    cursor = conn.cursor()

    print("Подключение к базе данных PostgreSQL успешно установлено.")
except Exception as e:
    print(f"Произошла ошибка при подключении к базе данных PostgreSQL: {e}")
    exit(1)

# Получаем путь к изображению из переменной окружения
image_path = os.getenv("img")
if image_path:
    try:
        image = Image.open(image_path)
        image.save("converted_image.png", "PNG")
    except Exception as e:
        print(f"Произошла ошибка при обработке изображения: {e}")
else:
    print("Переменная окружения img не задана. Проверьте файл .env.")
    exit(1)

# Очистка базы данных и создание новых таблиц
with conn.cursor() as cur:
    cur.execute("""
    DROP TABLE IF EXISTS user_words, words, users
    """)

    # Таблица пользователей
    cur.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id SERIAL  PRIMARY KEY,
        user_id    INTEGER UNIQUE NOT NULL,
        username   VARCHAR(255)   NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)

    # Общий словарь
    cur.execute("""
        CREATE TABLE IF NOT EXISTS words (
            id SERIAL      PRIMARY KEY,
            target_word    VARCHAR(255) UNIQUE NOT NULL,
            translate_word VARCHAR(255) NOT NULL
        )
        """)

    # Персональный словарь
    cur.execute("""
    CREATE TABLE IF NOT EXISTS user_words (
        id SERIAL      PRIMARY KEY,
        user_id        INTEGER      NOT NULL REFERENCES users (user_id),
        target_word    VARCHAR(255) NOT NULL,
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


class Command:
    ADD_WORD = 'Добавить слово ➕'
    DELETE_WORD = 'Удалить слово 🔙'
    NEXT = 'Следующее слово ➡️'


# Состояния
class MyStates(StatesGroup):
    target_word = State()
    translate_word = State()
    other_words = State()
    adding_new_word = State()
    saving_new_word = State()
    deleting_word = State()


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
SELECT %s, %s 
 WHERE NOT EXISTS (
    SELECT 1 
      FROM words 
     WHERE target_word = %s 
       AND translate_word = %s
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

    try:
        sti = open("converted_image.png", 'rb')
        bot.send_sticker(cid, sti)
    except Exception as e:
        print(f"Произошла ошибка при отправке стикера: {e}")

    bot.send_message(cid, f"Приветствую, {message.from_user.first_name}!\nЯ {bot.get_me().first_name}! "
                          f"Начнём учить язык 🇬🇧\nУ тебя есть возможность использовать тренажёр,\nкак конструктор, "
                          f"и собирать свою собственную базу для обучения.\nДля этого воспрользуйся инструментами:\n"
                          f"- добавить слово ➕\n"
                          f"- удалить слово 🔙\n"
                          f"Приступим ⬇️", parse_mode='html'
                     )

    create_cards(message)


def create_cards(message):
    cid = message.chat.id

    # Получение случайного слова
    cursor.execute("""
        SELECT target_word, translate_word 
          FROM words
         WHERE NOT EXISTS (
               SELECT 1 
                 FROM user_words 
                WHERE user_words.user_id = %s 
                  AND words.target_word = user_words.target_word
        )
      ORDER BY RANDOM() 
         LIMIT 1
        """, (cid,))
    word = cursor.fetchone()
    print(f"Случайное слово: {word}")

    if not word:
        bot.send_message(cid, "Все слова изучены!\nДобавьте новые через 'Добавить слово ➕'.")
        print("Все слова изучены.")
        return

    target_word, translate_word = word

    # Установка нового состояния
    bot.set_state(user_id=message.from_user.id, chat_id=message.chat.id, state=MyStates.target_word)
    current_state = bot.get_state(user_id=message.from_user.id, chat_id=message.chat.id)
    print(f"Текущее состояние: {current_state}")

    # Сохранение данных в состоянии
    with bot.retrieve_data(user_id=message.from_user.id, chat_id=message.chat.id) as data:
        data['target_word'] = target_word
        data['translate_word'] = translate_word
        if 'target_word' not in data or 'translate_word' not in data:
            print("Ошибка: данные не найдены в состоянии.")
            return

    retrieved_state = bot.get_state(user_id=message.from_user.id, chat_id=message.chat.id)
    print(f"Полученное состояние: {retrieved_state}")

    # Создаём кнопки для вариантов ответа
    cursor.execute("""
        SELECT target_word 
          FROM words 
         WHERE target_word != %s 
      ORDER BY RANDOM() 
         LIMIT 3
        """, (target_word,))
    other_words = [w[0] for w in cursor.fetchall()]
    options = other_words + [target_word]
    random.shuffle(options)

    markup = types.ReplyKeyboardMarkup(row_width=2)
    buttons = [types.KeyboardButton(option) for option in options]
    buttons.append(types.KeyboardButton(Command.NEXT))
    buttons.append(types.KeyboardButton(Command.ADD_WORD))
    buttons.append(types.KeyboardButton(Command.DELETE_WORD))
    markup.add(*buttons)

    # Отправляем сообщение
    greeting = f"Выбери перевод слова:\n🇷🇺 {translate_word}"
    bot.send_message(cid, greeting, reply_markup=markup)


@bot.message_handler(func=lambda message: message.text == Command.NEXT)
def next_word(message):
    create_cards(message)


@bot.message_handler(func=lambda message: message.text == Command.ADD_WORD)
def add_word_start(message):
    cid = message.chat.id
    bot.set_state(user_id=message.from_user.id, chat_id=cid, state=MyStates.adding_new_word)
    bot.send_message(cid, "Введите слово, которое вы хотите добавить, на английском:")


@bot.message_handler(state=MyStates.adding_new_word)
def add_translate_word(message):
    cid = message.chat.id
    word = message.text.strip().capitalize()

    # Проверяем, что слово не пустое
    if not word:
        bot.send_message(cid, "Слово не может быть пустым. Пожалуйста, введите слово.")
        return

    with bot.retrieve_data(user_id=message.from_user.id, chat_id=cid) as data:
        data['target_word'] = word

    bot.set_state(user_id=message.from_user.id, chat_id=cid, state=MyStates.saving_new_word)
    bot.send_message(cid, f"Теперь введите перевод для слова '{word}':")


@bot.message_handler(state=MyStates.saving_new_word)
def save_new_word(message):
    cid = message.chat.id
    translation = message.text.strip().capitalize()

    # Проверяем, что перевод не пустой
    if not translation:
        bot.send_message(cid, "Перевод не может быть пустым. Пожалуйста, введите перевод.")
        return

    try:
        # Извлекаем данные из состояния
        with bot.retrieve_data(user_id=message.from_user.id, chat_id=cid) as data:
            target_word = data.get('target_word').capitalize()

        if not target_word:
            bot.send_message(cid, "Ошибка! Попробуй снова начать с /start.")
            bot.delete_state(user_id=message.from_user.id, chat_id=cid)
            return

        # Сохраняем новое слово в персональный словарь пользователя

        cursor.execute("""
        INSERT INTO user_words (user_id, target_word, translate_word)
        VALUES (%s, %s, %s)
        ON CONFLICT (user_id, target_word) DO NOTHING
        """, (message.from_user.id, target_word, translation))
        conn.commit()

        bot.send_message(cid, f"Слово '{target_word}' и его перевод '{translation}' успешно добавлены!")
    except Exception as e:
        print(f"Произошла ошибка при сохранении слова: {e}")
        bot.send_message(cid, "Произошла ошибка при сохранении слова.")
    finally:
        bot.delete_state(user_id=message.from_user.id, chat_id=cid)


@bot.message_handler(func=lambda message: message.text == Command.DELETE_WORD)
def delete_word_start(message):
    cid = message.chat.id

    bot.set_state(user_id=message.from_user.id, chat_id=message.chat.id, state=MyStates.deleting_word)
    bot.send_message(cid, "Введите слово, которое хотите удалить из вашего словаря:")


@bot.message_handler(state=MyStates.deleting_word)
def delete_word(message):
    cid = message.chat.id
    word_to_delete = message.text.strip().capitalize()

    # Проверяем, существует ли выбранное слово в персональном словаре
    cursor.execute("""
        SELECT 1
          FROM user_words
         WHERE user_id = %s
           AND target_word = %s
    """, (cid, word_to_delete))
    exists = cursor.fetchone()

    if not exists:
        bot.send_message(cid, "Указанное слово отсутствует в вашем словаре. Попробуйте снова.")
        return

    # Удаляем слово
    cursor.execute("""
        DELETE FROM user_words
         WHERE user_id = %s
           AND target_word = %s
    """, (cid, word_to_delete))
    conn.commit()

    bot.send_message(cid, f"Слово '{word_to_delete}' успешно удалено из вашего словаря!")
    bot.delete_state(user_id=message.from_user.id, chat_id=message.chat.id)
    send_main_menu(cid)


# Функция для отправки основного меню
def send_main_menu(chat_id):
    markup = types.ReplyKeyboardMarkup(row_width=2)
    buttons = [
        types.KeyboardButton(Command.ADD_WORD),
        types.KeyboardButton(Command.DELETE_WORD),
        types.KeyboardButton(Command.NEXT)
    ]
    markup.add(*buttons)
    bot.send_message(chat_id, "Выберите дальнейшее действие:", reply_markup=markup)


@bot.message_handler(func=lambda message: True, content_types=['text'])
def message_reply(message):
    user_response = message.text
    print(f"Ответ пользователя: {user_response}")

    # Проверяем текущее состояние
    state = bot.get_state(user_id=message.from_user.id, chat_id=message.chat.id)
    print(f"Полученное состояние для пользователя {message.from_user.id}, чат {message.chat.id}: {state}")

    if state != MyStates.target_word.name:
        bot.send_message(message.chat.id, "Ошибка! Начните заново с /start.")
        return

    # Извлекаем данные из состояний
    with bot.retrieve_data(message.from_user.id, message.chat.id) as data:
        target_word = data.get('target_word')
        translate_word = data.get('translate_word')
        print(f"Данные из состояний: target_word={target_word}, translate_word={translate_word}")

    if not target_word or not translate_word:
        bot.send_message(message.chat.id, "Ошибка! Попробуй снова начать с /start.")
        return

    # Проверяем и обновляем количество попыток
    attempts = data.get('attempts', 0)
    if user_response == target_word:
        cursor.execute("""
        INSERT INTO user_words (user_id, target_word, translate_word)
        VALUES (%s, %s, %s)
        ON CONFLICT (user_id, target_word) DO NOTHING
        """, (message.chat.id, target_word, translate_word))
        conn.commit()

        bot.send_message(message.chat.id, f"✅ Правильно!\n{target_word} => {translate_word}!")
        data.clear()
    else:
        attempts += 1
        data['attempts'] = attempts
        if attempts < 3:
            bot.send_message(
                message.chat.id, f"❌ Неправильно! Попробуй снова.\nПеревод слова: {translate_word}\n"
                                 f"Попытка {attempts} из 3."
            )
        else:
            bot.send_message(
                message.chat.id,
                f"К сожалению, вы исчерпали попытки.\n"
                f"Правильный перевод: {target_word}"
            )
            data.clear()


bot.add_custom_filter(custom_filters.StateFilter(bot))
bot.infinity_polling(timeout=10, long_polling_timeout=5, skip_pending=True)
