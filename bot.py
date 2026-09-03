import os
import telebot
import openai
import gspread
import pandas as pd
import io
import re
import threading
from datetime import datetime
from flask import Flask
from oauth2client.service_account import ServiceAccountCredentials

# ===== ТОКЕНЫ И КЛЮЧИ ИЗ ПЕРЕМЕННЫХ ОКРУЖЕНИЯ =====
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
GOOGLE_SHEET_URL = os.getenv("GOOGLE_SHEET_URL")
MY_USER_ID = int(os.getenv("MY_USER_ID", 0))

if not all([TELEGRAM_TOKEN, DEEPSEEK_API_KEY, GOOGLE_SHEET_URL, MY_USER_ID]):
    raise ValueError("❌ Ошибка: не все переменные окружения заданы! Проверьте настройки Render.")

# ===== НАСТРОЙКА =====
bot = telebot.TeleBot(TELEGRAM_TOKEN)

# ИНИЦИАЛИЗАЦИЯ КЛИЕНТА OPENAI (НОВЫЙ СИНТАКСИС)
client = openai.OpenAI(
    api_key=DEEPSEEK_API_KEY,
    base_url="https://api.deepseek.com/v1"
)

# ===== ПОДКЛЮЧЕНИЕ К GOOGLE SHEETS =====
def get_sheet():
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    creds = ServiceAccountCredentials.from_json_keyfile_name('credentials.json', scope)
    gclient = gspread.authorize(creds)
    return gclient.open_by_url(GOOGLE_SHEET_URL).sheet1

# ===== ФУНКЦИИ ДЛЯ ИСТОРИИ ДИАЛОГА =====
def save_to_history(user_id, role, text):
    """Сохраняет сообщение в историю (в Google Sheets)"""
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
    """Загружает последние 100 сообщений из истории"""
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

# ===== ОБРАБОТКА ФАЙЛОВ (Excel/CSV) =====
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
        
        # Авто-определение колонок
        date_col, amount_col, desc_col = None, None, None
        for col in df.columns:
            c = str(col).lower()
            if any(word in c for word in ['дата', 'date', 'день']):
                date_col = col
            elif any(word in c for word in ['сумм', 'amount', 'списан', 'зачисл', 'сумма']):
                amount_col = col
            elif any(word in c for word in ['опис', 'назнач', 'description', 'коммент']):
                desc_col = col
        
        if not date_col or not amount_col:
            bot.send_message(
                message.chat.id,
                f"❌ Не найдены колонки. Вот все колонки: {', '.join(df.columns)}\n\nПереименуйте их в: Дата, Сумма, Описание"
            )
            return
        
        df['Дата'] = pd.to_datetime(df[date_col]).dt.strftime('%Y-%m-%d')
        df['Сумма'] = pd.to_numeric(df[amount_col], errors='coerce')
        df['Описание'] = df[desc_col].astype(str) if desc_col else ''
        df = df[df['Сумма'].notna()]
        df['Тип'] = df['Сумма'].apply(lambda x: 'Доход' if x > 0 else 'Расход')
        
        def detect_category(row):
            text = str(row['Описание']).lower()
            categories = {
                'Еда': ['еда', 'продукты', 'супермаркет', 'магнит', 'пятерочка'],
                'Транспорт': ['такси', 'метро', 'автобус', 'аэро', 'бензин', 'заправка'],
                'ЖКХ': ['жкх', 'кварт', 'свет', 'вода', 'отопление', 'газ'],
                'Связь': ['интернет', 'телефон', 'мтс', 'билайн', 'мегафон'],
                'Рестораны': ['кафе', 'ресторан', 'кофейня', 'столовая'],
                'Аренда': ['аренда', 'квартира']
            }
            for cat, keywords in categories.items():
                if any(k in text for k in keywords):
                    return cat
            return 'Прочее'
        
        df['Категория'] = df.apply(detect_category, axis=1)
        
        existing_df = load_data()
        if existing_df.empty:
            combined_df = df[['Дата', 'Сумма', 'Описание', 'Категория', 'Тип']]
        else:
            combined_df = pd.concat([existing_df, df[['Дата', 'Сумма', 'Описание', 'Категория', 'Тип']]], ignore_index=True)
        
        save_data(combined_df)
        bot.send_message(
            message.chat.id,
            f"✅ Загружено и сохранено {len(df)} транзакций в Google Таблицу!\n"
            f"Всего в базе: {len(combined_df)} записей."
        )
    except Exception as e:
        bot.send_message(message.chat.id, f"❌ Ошибка: {str(e)}")

# ===== ДОБАВЛЕНИЕ ДОЛГА =====
@bot.message_handler(commands=['debt'], func=is_authorized)
def add_debt(message):
    text = message.text.replace('/debt', '').strip()
    if not text:
        bot.send_message(message.chat.id, "Напиши: /debt Кому и сколько, например: /debt Ивану 50000 до декабря")
        return
    add_transaction(
        date=datetime.now().strftime('%Y-%m-%d'),
        amount=0,
        description=text,
        category='Долги',
        trans_type='Долг'
    )
    bot.send_message(message.chat.id, f"✅ Долг запомнен: {text}")

# ===== ОБРАБОТКА ВОПРОСОВ С ИСТОРИЕЙ =====
@bot.message_handler(func=lambda message: is_authorized(message) and not message.text.startswith('/'))
def handle_question(message):
    user_text = message.text
    user_id = message.from_user.id
    
    # Сохраняем вопрос в историю
    save_to_history(user_id, "user", user_text)
    
    bot.send_message(message.chat.id, "🧮 Думаю...")
    
    # Загружаем последние 100 сообщений из истории
    history = get_recent_history(user_id, limit=100)
    
    # Формируем контекст из истории
    context = ""
    for record in history:
        role = "Пользователь" if record['Роль'] == "user" else "Бот"
        context += f"{role}: {record['Текст']}\n"
    
    # Загружаем финансовые данные
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
Твои финансовые данные:
- Доходы: {income:,.0f} ₽
- Расходы: {abs(expense):,.0f} ₽
- Баланс: {balance:,.0f} ₽
Основные категории расходов:
{top_text}
"""
    
    # Формируем промпт для DeepSeek (НОВЫЙ СИНТАКСИС)
    prompt = f"""
Ты — умный ИИ-помощник. Ты отвечаешь на любые вопросы пользователя, используя контекст диалога и финансовые данные, если они есть.

Вот история вашего диалога (последние сообщения):
{context}

{financial_summary}

Вопрос пользователя: "{user_text}"

Ответь на вопрос максимально полезно и естественно. Если вопрос касается финансов, используй данные выше. Если вопрос общий — ответь как DeepSeek.
"""
    
    try:
        # НОВЫЙ СПОСОБ ВЫЗОВА CHAT COMPLETIONS
        response = client.chat.completions.create(
            model="deepseek-chat",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.7,
            max_tokens=1500
        )
        answer = response.choices[0].message.content
        
        # Сохраняем ответ в историю
        save_to_history(user_id, "bot", answer)
        
        bot.send_message(message.chat.id, f"💬 {answer}")
    except Exception as e:
        bot.send_message(message.chat.id, f"❌ Ошибка при обращении к DeepSeek: {str(e)}")

# ===== КОМАНДА /START =====
@bot.message_handler(commands=['start'], func=is_authorized)
def start(message):
    welcome = """
💰 Привет! Я твой умный финансовый аналитик с бесконечной памятью.

Я умею:
1. 📂 Принимать выписки из банка (.xlsx, .csv)
2. 💸 Запоминать долги (/debt Кому и сколько)
3. 🧠 Помнить всю историю диалога (в Google Sheets)
4. 📊 Отвечать на любые вопросы по твоим финансам
5. 🌍 Отвечать на любые общие вопросы (как DeepSeek)

Просто задавай вопросы — я помню всё, что мы обсуждали!
"""
    bot.send_message(message.chat.id, welcome)

# ===== ЗАПУСК БОТА И ВЕБ-СЕРВЕРА ДЛЯ RENDER =====
if __name__ == "__main__":
    def run_bot():
        print("✅ Финансовый аналитик запущен с бесконечной памятью!")
        bot.infinity_polling()
    
    bot_thread = threading.Thread(target=run_bot)
    bot_thread.daemon = True
    bot_thread.start()
    
    app = Flask(__name__)
    
    @app.route('/')
    def index():
        return "Бот работает с бесконечной памятью!"
    
    app.run(host='0.0.0.0', port=10000)
