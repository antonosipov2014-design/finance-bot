
import os
import telebot
import openai
import gspread
import pandas as pd
import io
import re
from datetime import datetime
from oauth2client.service_account import ServiceAccountCredentials

# ===== ТОКЕНЫ И КЛЮЧИ БЕРУТСЯ ИЗ ПЕРЕМЕННЫХ ОКРУЖЕНИЯ НА RENDER =====
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
GOOGLE_SHEET_URL = os.getenv("GOOGLE_SHEET_URL")
MY_USER_ID = int(os.getenv("MY_USER_ID", 0))

# ===== ПРОВЕРКА, ЧТО ВСЕ ПЕРЕМЕННЫЕ ЗАДАНЫ =====
if not all([TELEGRAM_TOKEN, DEEPSEEK_API_KEY, GOOGLE_SHEET_URL, MY_USER_ID]):
    raise ValueError("❌ Ошибка: не все переменные окружения заданы! Проверьте настройки Render.")

# ===== НАСТРОЙКА =====
bot = telebot.TeleBot(TELEGRAM_TOKEN)
openai.api_key = DEEPSEEK_API_KEY
openai.api_base = "https://api.deepseek.com/v1"

# ===== ПОДКЛЮЧЕНИЕ К GOOGLE SHEETS =====
def get_sheet():
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    creds = ServiceAccountCredentials.from_json_keyfile_name('credentials.json', scope)
    client = gspread.authorize(creds)
    return client.open_by_url(GOOGLE_SHEET_URL).sheet1

# ===== ФУНКЦИИ РАБОТЫ С ТАБЛИЦЕЙ =====
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

# ===== ОБРАБОТКА ВОПРОСОВ =====
@bot.message_handler(func=lambda message: is_authorized(message) and not message.text.startswith('/'))
def handle_question(message):
    user_text = message.text
    df = load_data()
    if df.empty:
        bot.send_message(
            message.chat.id,
            "📭 У меня пока нет данных. Отправьте файл выписки из банка или добавьте долги командой /debt."
        )
        return
    
    bot.send_message(message.chat.id, "🧮 Анализирую данные...")
    
    income = df[df['Сумма'] > 0]['Сумма'].sum()
    expense = df[df['Сумма'] < 0]['Сумма'].sum()
    balance = income + expense
    
    cat_expense = df[df['Сумма'] < 0].groupby('Категория')['Сумма'].sum().abs()
    top_cats = cat_expense.sort_values(ascending=False).head(5)
    top_text = "\n".join([f"  • {cat}: {val:,.0f} ₽" for cat, val in top_cats.items()])
    
    debts = df[df['Тип'] == 'Долг']['Описание'].tolist()
    debts_text = "\n".join([f"  • {d}" for d in debts]) if debts else "Нет долгов"
    
    summary = f"""
    Всего транзакций: {len(df)}
    Доходы: {income:,.0f} ₽
    Расходы: {abs(expense):,.0f} ₽
    Баланс: {balance:,.0f} ₽
    
    Основные категории расходов:
    {top_text}
    
    Долги:
    {debts_text}
    """
    
    prompt = f"""
    Ты — ИИ-аналитик финансовых данных. У тебя есть данные о транзакциях пользователя.
    Вот сводка:
    {summary}
    
    Вопрос пользователя: "{user_text}"
    
    Ответь на вопрос максимально точно, используя только данные из сводки.
    Если данных недостаточно — скажи честно и предложи, что нужно добавить.
    Если вопрос требует расчётов — проведи их.
    
    Ответ должен быть на русском языке, коротким и понятным.
    """
    
    try:
        response = openai.ChatCompletion.create(
            model="deepseek-chat",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.7,
            max_tokens=1000
        )
        answer = response.choices[0].message.content
        bot.send_message(message.chat.id, f"💬 {answer}")
    except Exception as e:
        bot.send_message(message.chat.id, f"❌ Ошибка при обращении к DeepSeek: {str(e)}")

# ===== КОМАНДА /START =====
@bot.message_handler(commands=['start'], func=is_authorized)
def start(message):
    welcome = """
💰 Привет! Я твой личный финансовый аналитик.

Я умею:
1. 📂 Принимать выписки из банка (.xlsx, .csv)
2. 💸 Запоминать долги (/debt Кому и сколько)
3. 📊 Отвечать на любые вопросы по твоим финансам

Просто отправь мне файл или задай вопрос — и я помогу разобраться с деньгами.

Примеры вопросов:
• Сколько я потратил на такси?
• Какой у меня средний чек?
• Смогу ли я выплатить долги до нового года?
• На что я трачу больше всего?
"""
    bot.send_message(message.chat.id, welcome)

# ===== ЗАПУСК =====
print("✅ Финансовый аналитик запущен!")
bot.infinity_polling()
