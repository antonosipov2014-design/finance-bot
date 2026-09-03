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
        print(f"🔄 Отправка запроса к YandexGPT с данными: {data}")
        response = requests.post(url, headers=headers, json=data, timeout=30)
        print(f"📡 Статус ответа: {response.status_code}")
        print(f"📄 Тело ответа: {response.text[:500]}...")
        
        response.raise_for_status()
        result = response.json()
        return result["result"]["alternatives"][0]["message"]["text"]
    except Exception as e:
        print(f"❌ Ошибка YandexGPT: {e}")
        return "⚠️ Извините, произошла ошибка при обращении к YandexGPT."
