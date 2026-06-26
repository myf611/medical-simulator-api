# Patient Simulator — Backend

Простой прокси-сервер на FastAPI. Прячет Groq API ключ от пользователей.

## Деплой на Railway

1. Зайди на railway.app → New Project → Deploy from GitHub repo
2. Создай новый репозиторий на GitHub и загрузи эти файлы:
   - main.py
   - requirements.txt
   - railway.json
   - Procfile

3. В Railway → Variables добавь:
   GROQ_API_KEY = твой_ключ_groq

4. Railway автоматически задеплоит сервер и выдаст URL вида:
   https://your-app.railway.app

5. Этот URL вставь в переменную BACKEND_URL во фронтенде (index.html и case2.html)

## Локальный запуск (для теста)

pip install -r requirements.txt
GROQ_API_KEY=your_key uvicorn main:app --reload

Сервер запустится на http://localhost:8000
