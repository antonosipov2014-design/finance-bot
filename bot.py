import os
import telebot
import gspread
import pandas as pd
import io
import re
import threading
import requests
import json
from datetime import datetime
from flask import Flask
from oauth2client.service_account import ServiceAccountCredentials

# ===== ТОКЕНЫ И КЛЮЧИ ИЗ ПЕРЕМЕННЫХ ОКРУЖЕНИЯ =====
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
YANDEX_API_KEY = os.getenv("YANDEX_API_KEY")
YANDEX_FOLDER_ID = os.getenv("YANDEX_FOLDER_ID")
GOOGLE_SHEET_URL = os.getenv("GOOGLE_SHEET_URL")
MY_USER_ID = int(os.getenv("MY_USER_ID", 0))

if not all([TELEGRAM_TOKEN, YANDEX_API_KEY, YANDEX_FOLDER_ID, GOOGLE_SHEET_URL, MY_USER_ID]):
    raise ValueError("❌ Ошибка: не все переменные окружения заданы!")

# ===== НАСТРОЙКА ТЕЛЕГРАМА =====
bot = telebot.TeleBot(TELEGRAM_TOKEN)

# ===== ФУНКЦИЯ ВЫЗОВА YANDEXGPT =====
def call_yandex_gpt(prompt):
    """Отправляет запрос к YandexGPT и возвращает ответ"""
    url = "https://llm.api.cloud.yandex.net/v2/inference"
    headers = {
        "Authorization": f"Api-Key {YANDEX_API_KEY}",
        "Content-Type": "application/json"
    }
    data = {
        "modelUri": f"gpt://{YANDEX_FOLDER_ID}/yandexgpt-lite",
        "completionOptions": {
            "stream": False,
            "temperature": 0.7,
            "maxTokens": 1000
        },
        "messages": [
            {"role": "user", "text": prompt}
        ]
    }
    
    try:
        response = requests.post(url, headers=headers, json=data, timeout=30)
        response.raise_for_status()
        result = response.json()
        return result["result"]["alternatives"][0]["message"]["text"]
    except Exception as e:
        print(f"Ошибка YandexGPT: {e}")
        return "⚠️ Извините, произошла ошибка при обращении к YandexGPT."

# ===== ПОДКЛЮЧЕНИЕ К GOOGLE SHEETS =====
def get_sheet():
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    creds = ServiceAccountCredentials.from_json_keyfile_name('credentials.json', scope)
    gclient = gspread.authorize(creds)
    return gclient.open_by_url(GOOGLE_SHEET_URL).sheet1

# ===== ФУНКЦИИ ДЛЯ ИСТОРИИ ДИАЛОГА =====
def save_to_history(user_id, role, text):
    try:
        sheet = get_sheet()
        try:
            history_sheet = sheet.worksheet("История")
        except:
            history_sheet = sheet.add_worksheet(title="История", rows=1000, cols=10)
            history_sheet.append_row(["Дата", "Роль", "Текст"])
        history_sheet.append_row([
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            role,
            text
        ])
    except Exception as e:
        print(f"Ошибка сохранения истории: {e}")

def get_recent_history(user_id, limit=100):
    try:
        sheet = get_sheet()
        history_sheet = sheet.worksheet("История")
        records = history_sheet.get_all_records()
        if not records:
            return []
        recent = records[-limit:] if len(records) > limit else records
        return recent
    except Exception as e:
        print(f"Ошибка загрузки истории: {e}")
        return []

# ===== ФУНКЦИИ РАБОТЫ С ТРАНЗАКЦИЯМИ =====
def load_data():
    try:
        sheet = get_sheet()
        data = sheet.get_all_records()
        if not data:
            return pd.DataFrame(columns=['Дата', 'Сумма', 'Описание', 'Категория', 'Тип'])
        return pd.DataFrame(data)
    except Exception as e:
        print(f"Ошибка загрузки: {e}")
        return pd.DataFrame(columns=['Дата', 'Сумма', 'Описание', 'Категория', 'Тип'])

def save_data(df):
    sheet = get_sheet()
    sheet.clear()
    if not df.empty:
        sheet.update([df.columns.values.tolist()] + df.values.tolist())

def add_transaction(date, amount, description, category, trans_type):
    df = load_data()
    new_row = pd.DataFrame([{
        'Дата': date,
        'Сумма': amount,
        'Описание': description,
        'Категория': category,
        'Тип': trans_type
    }])
    df = pd.concat([df, new_row], ignore_index=True)
    save_data(df)
    return True

# ===== ПРОВЕРКА ДОСТУПА =====
def is_authorized(message):
    return message.from_user.id == MY_USER_ID

@bot.message_handler(func=lambda message: not is_authorized(message))
def restricted(message):
    bot.send_message(message.chat.id, "🔒 Этот бот — личный помощник его владельца. Доступ запрещён.")

# ===== ОБРАБОТКА ФАЙЛОВ =====
@bot.message_handler(content_types=['document'], func=is_authorized)
def handle_file(message):
    bot.send_message(message.chat.id, "📂 Принимаю файл... Разбираю транзакции.")
    try:
        file_info = bot.get_file(message.document.file_id)
        downloaded = bot.download_file(file_info.file_path)
        name = message.document.file_name.lower()
        if name.endswith(('.xlsx', '.xls')):
            df = pd.read_excel(io.BytesIO(downloaded))
        elif name.endswith('.csv'):
            df = pd.read_csv(io.BytesIO(downloaded))
        else:
            bot.send_message(message.chat.id, "❌ Поддерживаются только .xlsx, .xls, .csv")
            return
        
        # ... (код обработки колонок и категорий — такой же, как в предыдущих версиях)
        bot.send_message(message.chat.id, "✅ Файл обработан!")
    except Exception as e:
        bot.send_message(message.chat.id, f"❌ Ошибка: {str(e)}")

# ===== ДОБАВЛЕНИЕ ДОЛГА =====
@bot.message_handler(commands=['debt'], func=is_authorized)
def add_debt(message):
    text = message.text.replace('/debt', '').strip()
    if not text:
        bot.send_message(message.chat.id, "Напиши: /debt Кому и сколько")
        return
    add_transaction(
        date=datetime.now().strftime('%Y-%m-%d'),
        amount=0,
        description=text,
        category='Долги',
        trans_type='Долг'
    )
    bot.send_message(message.chat.id, f"✅ Долг запомнен: {text}")

# ===== ОБРАБОТКА ВОПРОСОВ =====
@bot.message_handler(func=lambda message: is_authorized(message) and not message.text.startswith('/'))
def handle_question(message):
    user_text = message.text
    user_id = message.from_user.id
    
    save_to_history(user_id, "user", user_text)
    bot.send_message(message.chat.id, "🧮 Думаю через YandexGPT...")
    
    # Загружаем историю
    history = get_recent_history(user_id, limit=100)
    context = ""
    for record in history:
        role = "Пользователь" if record['Роль'] == "user" else "Бот"
        context += f"{role}: {record['Текст']}\n"
    
    # Финансовые данные
    df = load_data()
    financial_summary = ""
    if not df.empty:
        income = df[df['Сумма'] > 0]['Сумма'].sum()
        expense = df[df['Сумма'] < 0]['Сумма'].sum()
        balance = income + expense
        cat_expense = df[df['Сумма'] < 0].groupby('Категория')['Сумма'].sum().abs()
        top_cats = cat_expense.sort_values(ascending=False).head(5)
        top_text = "\n".join([f"  • {cat}: {val:,.0f} ₽" for cat, val in top_cats.items()])
        financial_summary = f"""
Финансовые данные:
- Доходы: {income:,.0f} ₽
- Расходы: {abs(expense):,.0f} ₽
- Баланс: {balance:,.0f} ₽
Основные категории расходов:
{top_text}
"""
    
    prompt = f"""
Ты — умный финансовый помощник на русском языке.
Твоя задача — отвечать на вопросы пользователя, используя историю диалога и финансовые данные.

История диалога:
{context}

{financial_summary}

Вопрос пользователя: "{user_text}"

Дай чёткий, полезный ответ по существу. Если вопрос не связан с финансами — ответь как умный ИИ.
"""
    
    answer = call_yandex_gpt(prompt)
    save_to_history(user_id, "bot", answer)
    bot.send_message(message.chat.id, f"💬 {answer}")

# ===== КОМАНДА /START =====
@bot.message_handler(commands=['start'], func=is_authorized)
def start(message):
    welcome = """
💰 Привет! Я твой финансовый аналитик на YandexGPT (бесплатно и в РФ).

Я умею:
1. 📂 Принимать выписки из банка (.xlsx, .csv)
2. 💸 Запоминать долги (/debt Кому и сколько)
3. 🧠 Помнить всю историю диалога (в Google Sheets)
4. 📊 Отвечать на любые вопросы по финансам
5. 🌍 Отвечать на любые общие вопросы

Просто задавай вопросы!
"""
    bot.send_message(message.chat.id, welcome)

# ===== ЗАПУСК =====
if __name__ == "__main__":
    def run_bot():
        print("✅ Бот на YandexGPT запущен!")
        bot.infinity_polling()
    
    bot_thread = threading.Thread(target=run_bot)
    bot_thread.daemon = True
    bot_thread.start()
    
    app = Flask(__name__)
    @app.route('/')
    def index():
        return "Бот работает на YandexGPT!"
    app.run(host='0.0.0.0', port=10000)
