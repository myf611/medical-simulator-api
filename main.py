from fastapi import FastAPI, Request, HTTPException
from uuid import UUID
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
import httpx
import json
import os
import re
from datetime import datetime

app = FastAPI()

limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://myf611.github.io"],
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"],
    allow_headers=["*"],
)

GROQ_KEY = os.environ.get("GROQ_API_KEY")
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_KEY")
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"

UZ_PHONE_REGEX = re.compile(r"^\+998(90|91|93|94|95|97|98|99)\d{7}$")

def supabase_headers():
    return {
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "apikey": SUPABASE_KEY,
        "Content-Type": "application/json",
        "Prefer": "return=representation"
    }

# ── HEALTH ──────────────────────────────────────────
@app.get("/")
@app.head("/")
def root():
    return {"status": "ok", "service": "Patient Simulator API"}

@app.get("/health")
@app.head("/health")
def health():
    return {"status": "ok"}

# ── AI CHAT ─────────────────────────────────────────
@app.post("/api/chat")
async def chat(request: Request):
    if not GROQ_KEY:
        return JSONResponse({"error": "API key not configured"}, status_code=500)
    body = await request.json()
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(
            GROQ_URL,
            headers={"Authorization": f"Bearer {GROQ_KEY}", "Content-Type": "application/json"},
            json=body
        )
    return JSONResponse(content=response.json(), status_code=response.status_code)

# ── STUDENT REGISTRATION ────────────────────────────
# ── TELEGRAM VERIFICATION ──────────────────────────────
import random
from datetime import timedelta
from urllib.parse import quote as urlquote


def normalize_phone_str(raw: str) -> str:
    raw = (raw or "").strip()
    return '+' + raw.replace('+', '').replace(' ', '').replace('-', '')


@app.post("/api/telegram-webhook")
async def telegram_webhook(request: Request):
    if not TELEGRAM_BOT_TOKEN:
        return {"ok": True}
    update = await request.json()
    message = update.get("message") or {}
    chat = message.get("chat") or {}
    chat_id = chat.get("id")
    username = chat.get("username", "")
    text = message.get("text", "")
    contact = message.get("contact")

    async with httpx.AsyncClient(timeout=10) as client:
        if contact:
            phone = normalize_phone_str(contact.get("phone_number", ""))
            await client.post(
                f"{SUPABASE_URL}/rest/v1/telegram_links",
                headers={**supabase_headers(), "Prefer": "resolution=merge-duplicates"},
                params={"on_conflict": "phone"},
                json={"phone": phone, "chat_id": chat_id, "telegram_username": username}
            )
            await client.post(
                f"{TELEGRAM_API}/sendMessage",
                json={"chat_id": chat_id, "text": "Готово! Номер привязан ✅ Теперь вернитесь на сайт и нажмите «Получить код»."}
            )
        elif text == "/start":
            keyboard = {
                "keyboard": [[{"text": "📱 Отправить номер телефона", "request_contact": True}]],
                "resize_keyboard": True,
                "one_time_keyboard": True
            }
            await client.post(
                f"{TELEGRAM_API}/sendMessage",
                json={
                    "chat_id": chat_id,
                    "text": "Здравствуйте! Это бот подтверждения номера для «Симулятора пациента».\n\nНажмите кнопку ниже, чтобы поделиться номером телефона.",
                    "reply_markup": keyboard
                }
            )
        else:
            await client.post(
                f"{TELEGRAM_API}/sendMessage",
                json={"chat_id": chat_id, "text": "Отправьте /start чтобы начать."}
            )
    return {"ok": True}


@app.post("/api/telegram-send-code")
@limiter.limit("3/minute")
async def telegram_send_code(request: Request):
    if not TELEGRAM_BOT_TOKEN:
        raise HTTPException(500, "Telegram бот не настроен")
    data = await request.json()
    phone = normalize_phone_str(data.get("phone", ""))
    if not UZ_PHONE_REGEX.match(phone):
        raise HTTPException(400, f"Неверный формат номера: {phone}")

    phone_encoded = urlquote(phone, safe='')

    async with httpx.AsyncClient(timeout=10) as client:
        link_resp = await client.get(
            f"{SUPABASE_URL}/rest/v1/telegram_links?phone=eq.{phone_encoded}",
            headers=supabase_headers()
        )
        links = link_resp.json()
        if not links:
            raise HTTPException(404, "Сначала напишите боту /start и поделитесь номером в Telegram")
        chat_id = links[0]["chat_id"]

        code = str(random.randint(100000, 999999))
        expires_at = (datetime.utcnow() + timedelta(minutes=10)).isoformat()

        await client.post(
            f"{SUPABASE_URL}/rest/v1/telegram_verifications",
            headers=supabase_headers(),
            json={"phone": phone, "code": code, "expires_at": expires_at}
        )

        send_resp = await client.post(
            f"{TELEGRAM_API}/sendMessage",
            json={"chat_id": chat_id, "text": f"Ваш код подтверждения: {code}\n\nДействителен 10 минут."}
        )
        if send_resp.status_code != 200:
            raise HTTPException(502, "Не удалось отправить код в Telegram")

    return {"ok": True}


@app.post("/api/telegram-verify-code")
async def telegram_verify_code(request: Request):
    data = await request.json()
    phone = normalize_phone_str(data.get("phone", ""))
    code = (data.get("code") or "").strip()

    phone_encoded = urlquote(phone, safe='')

    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(
            f"{SUPABASE_URL}/rest/v1/telegram_verifications?phone=eq.{phone_encoded}&code=eq.{code}&verified=eq.false&order=created_at.desc&limit=1",
            headers=supabase_headers()
        )
        rows = resp.json()
        if not rows:
            raise HTTPException(400, "Неверный код")

        row = rows[0]
        expires_at = datetime.fromisoformat(row["expires_at"].replace("Z", "+00:00"))
        if expires_at < datetime.now(expires_at.tzinfo):
            raise HTTPException(400, "Код истёк, запросите новый")

        await client.patch(
            f"{SUPABASE_URL}/rest/v1/telegram_verifications?id=eq.{row['id']}",
            headers=supabase_headers(),
            json={"verified": True, "verified_at": datetime.utcnow().isoformat()}
        )

    return {"ok": True}


@app.post("/api/register")
@limiter.limit("5/minute")
async def register(request: Request):
    data = await request.json()
    last_name = data.get("last_name", "").strip()
    first_name = data.get("first_name", "").strip()
    raw = data.get("phone", "").strip()
    phone = '+' + raw.replace('+', '').replace(' ', '').replace('-', '')
    password = data.get("password", "")
    workplace = data.get("workplace", "").strip()
    region = data.get("region", "").strip()
    city = data.get("city", "").strip()
    org_slug = data.get("org_slug", "tashkent-endo")

    # Validate fields
    if not last_name or not first_name:
        raise HTTPException(400, "Введите фамилию и имя")
    if not UZ_PHONE_REGEX.match(phone):
        raise HTTPException(400, "Неверный формат номера. Пример: +998901234567")
    if len(password) < 6:
        raise HTTPException(400, "Пароль минимум 6 символов")

    async with httpx.AsyncClient(timeout=10) as client:
        # Get organization
        org_resp = await client.get(
            f"{SUPABASE_URL}/rest/v1/organizations?slug=eq.{org_slug}&is_active=eq.true",
            headers=supabase_headers()
        )
        orgs = org_resp.json()
        if not orgs:
            raise HTTPException(404, "Организация не найдена или неактивна")
        org = orgs[0]

        # Check subscription
        if org.get("subscription_end"):
            end = datetime.fromisoformat(org["subscription_end"].replace("Z", "+00:00"))
            if end < datetime.now(end.tzinfo):
                raise HTTPException(403, "Подписка организации истекла")

        # Check student limit
        count_resp = await client.get(
            f"{SUPABASE_URL}/rest/v1/students?organization_id=eq.{org['id']}&is_active=eq.true",
            headers={**supabase_headers(), "Prefer": "count=exact", "Range": "0-0"}
        )
        count = int(count_resp.headers.get("content-range", "0/0").split("/")[-1])
        if count >= org.get("max_students", 30):
            raise HTTPException(403, f"Достигнут лимит студентов ({org['max_students']}). Обратитесь к администратору.")

        # Check if phone already registered
        from urllib.parse import quote
        phone_encoded = quote(phone, safe='')
        existing = await client.get(
            f"{SUPABASE_URL}/rest/v1/students?phone=eq.{phone_encoded}",
            headers=supabase_headers()
        )
        if existing.json():
            raise HTTPException(409, "Этот номер уже зарегистрирован. Войдите вместо регистрации.")

        pwd_hash, salt = hash_password(password)

        # Create student
        create_resp = await client.post(
            f"{SUPABASE_URL}/rest/v1/students",
            headers=supabase_headers(),
            json={
                "organization_id": org["id"],
                "last_name": last_name,
                "first_name": first_name,
                "phone": phone,
                "password_hash": pwd_hash,
                "password_salt": salt,
                "workplace": workplace,
                "region": region,
                "city": city
            }
        )
        if create_resp.status_code not in (200, 201):
            raise HTTPException(500, "Не удалось создать аккаунт")
        student = create_resp.json()[0]
        return {"student_id": student["id"], "name": f"{student['last_name']} {student['first_name']}", "phone": student.get("phone",""), "existing": False}


# ── LOGIN ────────────────────────────────────────────
@app.post("/api/login")
@limiter.limit("10/minute")
async def login(request: Request):
    data = await request.json()
    # Normalize phone - remove all spaces and non-digits except leading +
    raw_phone = data.get("phone", "").strip()
    # Remove spaces from phone number
    phone = '+' + raw_phone.replace('+', '').replace(' ', '').replace('-', '')
    password = data.get("password", "")
    org_slug = data.get("org_slug", "tashkent-endo")

    if not UZ_PHONE_REGEX.match(phone):
        raise HTTPException(400, f"Неверный формат номера: {phone}")

    from urllib.parse import quote
    phone_encoded = quote(phone, safe='')
    async with httpx.AsyncClient(timeout=10) as client:
        existing = await client.get(
            f"{SUPABASE_URL}/rest/v1/students?phone=eq.{phone_encoded}&is_active=eq.true",
            headers=supabase_headers()
        )
        students = existing.json()
        if not students:
            raise HTTPException(404, f"Номер не найден: {phone}")

        student = students[0]

        if not student.get("password_hash"):
            raise HTTPException(401, "У аккаунта не задан пароль. Обратитесь к администратору.")

        check_hash, _ = hash_password(password, student["password_salt"])
        if check_hash != student["password_hash"]:
            raise HTTPException(401, "Неверный номер или пароль")

        return {
            "student_id": student["id"],
            "name": f"{student['last_name']} {student['first_name']}",
            "phone": student.get("phone", ""),
            "existing": True
        }


# ── LAB TESTS SEARCH ─────────────────────────────────
@app.get("/api/lab-search")
async def lab_search(q: str = "", limit: int = 8):
    if len(q.strip()) < 2:
        return []
    from urllib.parse import quote
    q_encoded = quote(q.strip(), safe='')
    async with httpx.AsyncClient(timeout=10) as client:
        # Search by name
        resp = await client.get(
            f"{SUPABASE_URL}/rest/v1/lab_tests?is_active=eq.true&name=ilike.*{q_encoded}*&limit={limit}&select=id,name,aliases,category&order=name",
            headers=supabase_headers()
        )
        results = resp.json() if resp.status_code == 200 else []

        # Also search aliases if not enough results
        if len(results) < limit:
            resp2 = await client.get(
                f"{SUPABASE_URL}/rest/v1/lab_tests?is_active=eq.true&aliases=ilike.*{q_encoded}*&limit={limit}&select=id,name,aliases,category&order=name",
                headers=supabase_headers()
            )
            aliases_results = resp2.json() if resp2.status_code == 200 else []
            # Merge and deduplicate
            existing_ids = {r['id'] for r in results}
            for r in aliases_results:
                if r['id'] not in existing_ids:
                    results.append(r)
                    existing_ids.add(r['id'])

    return results[:limit]

# ── SAVE RESULT ─────────────────────────────────────
@app.get("/api/workplace-search")
async def workplace_search(q: str = "", limit: int = 8):
    if len(q.strip()) < 2:
        return []
    from urllib.parse import quote
    q_encoded = quote(q.strip(), safe='')
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(
            f"{SUPABASE_URL}/rest/v1/workplaces?is_active=eq.true&name=ilike.*{q_encoded}*&limit={limit}&select=id,name,region,category&order=name",
            headers=supabase_headers()
        )
        results = resp.json() if resp.status_code == 200 else []
    return results[:limit]



# ── FINISH ATTEMPT: EVALUATE + SAVE (new 5-block exam flow) ─
EVALUATOR_SYSTEMS = {
"ru": """Ты — опытный клинический преподаватель медицинского вуза. Оцениваешь работу студента по разбору клинического кейса, который прошёл через 5 этапов: сбор анамнеза, физикальный осмотр, предварительный диагноз, назначение анализов, финальный диагноз и план лечения.

ПРАВИЛЬНЫЙ ДИАГНОЗ И ОБОСНОВАНИЕ ты получишь в данных кейса ниже.

Оцени работу студента и верни ТОЛЬКО валидный JSON без каких-либо пояснений до или после, строго в следующем формате:

{
  "overall_grade": <число от 0 до 100>,
  "positive_points": [<массив строк — что сделано правильно, конкретно, со ссылкой на этап>],
  "negative_points": [<массив строк — что пропущено или сделано неверно, конкретно>],
  "block_grades": {
    "anamnesis": <0-100>,
    "exam": <0-100>,
    "preliminary_diagnosis": <0-100>,
    "labs": <0-100>,
    "final": <0-100>
  }
}

Правила:
- positive_points и negative_points должны быть конкретными и объяснять ЗА ЧТО именно (не общими фразами).
- Каждый пункт — отдельная строка, 1 предложение.
- overall_grade — средневзвешенная оценка с учётом важности финального диагноза и плана лечения.
- Если этап не пройден студентом (пустой) — соответствующий block_grade = 0 и упомяни это в negative_points.
- Отвечай на русском языке.""",
"en": """You are an experienced clinical instructor at a medical school. You are evaluating a student's work on a clinical case that went through 5 stages: history taking, physical examination, preliminary diagnosis, ordering labs/investigations, final diagnosis and treatment plan.

You will receive the CORRECT DIAGNOSIS AND RATIONALE in the case data below.

Evaluate the student's work and return ONLY valid JSON with no explanation before or after, strictly in the following format:

{
  "overall_grade": <number from 0 to 100>,
  "positive_points": [<array of strings — what was done correctly, specific, referencing the stage>],
  "negative_points": [<array of strings — what was missed or done incorrectly, specific>],
  "block_grades": {
    "anamnesis": <0-100>,
    "exam": <0-100>,
    "preliminary_diagnosis": <0-100>,
    "labs": <0-100>,
    "final": <0-100>
  }
}

Rules:
- positive_points and negative_points must be specific and explain EXACTLY WHY (not generic phrases).
- Each point is a separate string, 1 sentence.
- overall_grade is a weighted average, giving importance to the final diagnosis and treatment plan.
- If a stage was not completed by the student (empty) — the corresponding block_grade = 0 and mention this in negative_points.
- Respond entirely in English."""
}


@app.post("/api/finish-attempt")
async def finish_attempt(request: Request):
    if not GROQ_KEY:
        raise HTTPException(500, "API key not configured")

    data = await request.json()
    student_id = data.get("student_id")
    if not student_id:
        raise HTTPException(400, "student_id обязателен")

    lang = data.get("lang", "ru")
    if lang not in EVALUATOR_SYSTEMS:
        lang = "ru"

    case_title = data.get("case_title", "Кейс" if lang == "ru" else "Case")
    case_context = data.get("case_context", "")
    blocks = data.get("blocks", {})
    duration_seconds = data.get("duration_seconds", 0)

    empty_stage_text = {"ru": "(пусто, студент не успел)", "en": "(empty, student did not complete)"}[lang]
    nothing_selected_text = {"ru": "(ничего не выбрано)", "en": "(nothing selected)"}[lang]
    nothing_ordered_text = {"ru": "(ничего не назначено)", "en": "(nothing ordered)"}[lang]
    not_specified_text = {"ru": "(не указан)", "en": "(not specified)"}[lang]

    def fmt_transcript(block):
        msgs = block.get("transcript", [])
        return "\n".join(f"{m.get('role','?')}: {m.get('text','')}" for m in msgs) or empty_stage_text

    def fmt_ordered(block):
        items = block.get("ordered", [])
        return ", ".join(items) if items else nothing_selected_text

    anamnesis = blocks.get("anamnesis", {})
    exam = blocks.get("exam", {})
    prelim = blocks.get("preliminary_diagnosis", {})
    labs = blocks.get("labs", {})
    final = blocks.get("final", {})

    if lang == "en":
        prompt = f"""CASE DATA:
{case_context}

═══ STAGE 1: HISTORY TAKING ═══
{fmt_transcript(anamnesis)}

═══ STAGE 2: PHYSICAL EXAMINATION (selected items) ═══
{fmt_ordered(exam)}

═══ STAGE 3: PRELIMINARY DIAGNOSIS ═══
{prelim.get('text') or not_specified_text}

═══ STAGE 4: ORDERED LABS/INVESTIGATIONS ═══
{', '.join(labs.get('ordered', [])) or nothing_ordered_text}

═══ STAGE 5: FINAL DIAGNOSIS AND TREATMENT PLAN ═══
Diagnosis: {final.get('diagnosis') or not_specified_text}
Treatment plan: {final.get('treatment_plan') or not_specified_text}

Evaluate the student's work across all 5 stages according to the instructions."""
    else:
        prompt = f"""ДАННЫЕ КЕЙСА:
{case_context}

═══ ЭТАП 1: СБОР АНАМНЕЗА ═══
{fmt_transcript(anamnesis)}

═══ ЭТАП 2: ФИЗИКАЛЬНЫЙ ОСМОТР (выбранные пункты осмотра) ═══
{fmt_ordered(exam)}

═══ ЭТАП 3: ПРЕДВАРИТЕЛЬНЫЙ ДИАГНОЗ ═══
{prelim.get('text') or not_specified_text}

═══ ЭТАП 4: НАЗНАЧЕННЫЕ АНАЛИЗЫ/ИССЛЕДОВАНИЯ ═══
{', '.join(labs.get('ordered', [])) or nothing_ordered_text}

═══ ЭТАП 5: ФИНАЛЬНЫЙ ДИАГНОЗ И ПЛАН ЛЕЧЕНИЯ ═══
Диагноз: {final.get('diagnosis') or not_specified_text}
План лечения: {final.get('treatment_plan') or not_specified_text}

Оцени работу студента по всем 5 этапам согласно инструкции."""

    async with httpx.AsyncClient(timeout=30) as client:
        groq_resp = await client.post(
            GROQ_URL,
            headers={"Authorization": f"Bearer {GROQ_KEY}", "Content-Type": "application/json"},
            json={
                "model": "llama-3.3-70b-versatile",
                "messages": [
                    {"role": "system", "content": EVALUATOR_SYSTEMS[lang]},
                    {"role": "user", "content": prompt}
                ],
                "response_format": {"type": "json_object"},
                "temperature": 0.3
            }
        )

    if groq_resp.status_code != 200:
        raise HTTPException(502, "Ошибка оценки: сервис ИИ недоступен")

    groq_data = groq_resp.json()
    try:
        raw_eval = groq_data["choices"][0]["message"]["content"]
        evaluation = json.loads(raw_eval)
    except (KeyError, json.JSONDecodeError, IndexError):
        fallback_msg = {"ru": "Не удалось автоматически оценить попытку.", "en": "Failed to automatically evaluate the attempt."}[lang]
        evaluation = {
            "overall_grade": None,
            "positive_points": [],
            "negative_points": [fallback_msg],
            "block_grades": {}
        }

    # Save to Supabase
    async with httpx.AsyncClient(timeout=10) as client:
        student_resp = await client.get(
            f"{SUPABASE_URL}/rest/v1/students?id=eq.{student_id}&select=organization_id",
            headers=supabase_headers()
        )
        students = student_resp.json()
        if not students:
            raise HTTPException(404, "Студент не найден")
        organization_id = students[0]["organization_id"]

        save_resp = await client.post(
            f"{SUPABASE_URL}/rest/v1/attempts",
            headers=supabase_headers(),
            json={
                "student_id": student_id,
                "organization_id": organization_id,
                "case_title": case_title,
                "finished_at": datetime.utcnow().isoformat(),
                "grade": evaluation.get("overall_grade"),
                "diagnosis": final.get("diagnosis"),
                "treatment_plan": final.get("treatment_plan"),
                "blocks": blocks,
                "evaluation": evaluation,
                "duration_seconds": duration_seconds
            }
        )

    saved = save_resp.json() if save_resp.status_code in (200, 201) else None
    attempt_id = saved[0]["id"] if saved else None

    return {"evaluation": evaluation, "attempt_id": attempt_id}


# ── ATTEMPT DETAIL: for reviewing past cases in cabinet ──
@app.get("/api/attempt-detail")
async def attempt_detail(attempt_id: UUID):
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(
            f"{SUPABASE_URL}/rest/v1/attempts?id=eq.{str(attempt_id)}&select=*",
            headers=supabase_headers()
        )
    results = resp.json() if resp.status_code == 200 else []
    if not results:
        raise HTTPException(404, "Попытка не найдена")
    return results[0]


# ── STUDENT: PROFILE ──────────────────────────────────
@app.get("/api/student-profile")
async def get_student_profile(student_id: UUID):
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(
            f"{SUPABASE_URL}/rest/v1/students?id=eq.{student_id}&select=id,last_name,first_name,phone,region,city,workplace",
            headers=supabase_headers()
        )
        students = resp.json()
        if not students:
            raise HTTPException(404, "Студент не найден")
    return students[0]


@app.put("/api/student-profile")
async def update_student_profile(request: Request):
    data = await request.json()
    student_id = data.get("student_id")
    if not student_id:
        raise HTTPException(400, "student_id обязателен")

    update_fields = {}
    for field in ["last_name", "first_name", "region", "city", "workplace"]:
        if data.get(field):
            update_fields[field] = data[field]

    if not update_fields:
        raise HTTPException(400, "Нет данных для обновления")

    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.patch(
            f"{SUPABASE_URL}/rest/v1/students?id=eq.{student_id}",
            headers=supabase_headers(),
            json=update_fields
        )
    if resp.status_code not in (200, 204):
        raise HTTPException(resp.status_code, "Не удалось обновить профиль")

    result = resp.json() if resp.text else [update_fields]
    return result[0] if result else update_fields


# ── STUDENT: OWN HISTORY ─────────────────────────────
@app.get("/api/student-history")
async def student_history(student_id: UUID):
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(
            f"{SUPABASE_URL}/rest/v1/attempts?student_id=eq.{student_id}&order=finished_at.desc&select=id,case_title,grade,diagnosis,finished_at,duration_seconds",
            headers=supabase_headers()
        )
        attempts = resp.json() if resp.status_code == 200 else []

    def to_num(g):
        if isinstance(g, (int, float)):
            return g
        if isinstance(g, str):
            try:
                return float(g)
            except ValueError:
                return None
        return None

    grades = [n for n in (to_num(a.get("grade")) for a in attempts) if n is not None]
    total_seconds = sum(a.get("duration_seconds") or 0 for a in attempts)

    avg_grade = round(sum(grades) / len(grades)) if grades else None
    total_hours = round(total_seconds / 3600, 1) if total_seconds else 0

    return {
        "attempts": attempts,
        "stats": {
            "cases_completed": len(attempts),
            "average_grade": avg_grade,
            "total_practice_hours": total_hours
        }
    }

# ── ADMIN: GET RESULTS ──────────────────────────────

# ── ADMIN / INSTRUCTOR AUTH ───────────────────────────
import hashlib
import secrets

def hash_password(password: str, salt: str = None) -> tuple:
    if salt is None:
        salt = secrets.token_hex(16)
    pwd_hash = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 100_000).hex()
    return pwd_hash, salt


ADMIN_ALLOWED_PHONES = {"+998900060611", "+998909111752", "+998933814560"}

@app.post("/api/admin-register")
@limiter.limit("5/minute")
async def admin_register(request: Request):
    data = await request.json()
    password = data.get("password") or ""
    full_name = (data.get("full_name") or "").strip()
    org_slug = data.get("org_slug") or "tashkent-endo"
    raw_phone = (data.get("phone") or "").strip()
    phone = '+' + raw_phone.replace('+', '').replace(' ', '').replace('-', '')

    if len(password) < 6:
        raise HTTPException(400, "Пароль минимум 6 символов")

    if not UZ_PHONE_REGEX.match(phone):
        raise HTTPException(400, f"Неверный формат номера: {phone}")

    if phone not in ADMIN_ALLOWED_PHONES:
        raise HTTPException(403, "Регистрация преподавателя недоступна для этого номера")

    from urllib.parse import quote
    phone_encoded = quote(phone, safe='')

    async with httpx.AsyncClient(timeout=10) as client:
        existing = await client.get(
            f"{SUPABASE_URL}/rest/v1/admins?phone=eq.{phone_encoded}",
            headers=supabase_headers()
        )
        if existing.json():
            raise HTTPException(409, "Этот номер уже зарегистрирован")

        org_resp = await client.get(
            f"{SUPABASE_URL}/rest/v1/organizations?slug=eq.{org_slug}",
            headers=supabase_headers()
        )
        orgs = org_resp.json()
        if not orgs:
            raise HTTPException(404, "Организация не найдена")
        organization_id = orgs[0]["id"]

        pwd_hash, salt = hash_password(password)

        resp = await client.post(
            f"{SUPABASE_URL}/rest/v1/admins",
            headers=supabase_headers(),
            json={
                "organization_id": organization_id,
                "username": phone,
                "password_hash": pwd_hash,
                "password_salt": salt,
                "full_name": full_name,
                "phone": phone
            }
        )
    if resp.status_code not in (200, 201):
        raise HTTPException(500, "Не удалось создать аккаунт")

    created = resp.json()[0]
    return {"admin_id": created["id"], "phone": phone, "full_name": full_name, "organization_id": organization_id}


@app.post("/api/admin-login")
@limiter.limit("10/minute")
async def admin_login(request: Request):
    data = await request.json()
    password = data.get("password") or ""
    raw_phone = (data.get("phone") or "").strip()
    phone = '+' + raw_phone.replace('+', '').replace(' ', '').replace('-', '')

    from urllib.parse import quote
    phone_encoded = quote(phone, safe='')

    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(
            f"{SUPABASE_URL}/rest/v1/admins?phone=eq.{phone_encoded}&is_active=eq.true",
            headers=supabase_headers()
        )
    admins = resp.json()
    if not admins:
        raise HTTPException(401, "Неверный номер или пароль")

    admin = admins[0]
    check_hash, _ = hash_password(password, admin["password_salt"])
    if check_hash != admin["password_hash"]:
        raise HTTPException(401, "Неверный номер или пароль")

    return {
        "admin_id": admin["id"],
        "phone": admin["phone"],
        "full_name": admin.get("full_name", ""),
        "organization_id": admin["organization_id"]
    }


@app.get("/api/admin/students")
async def admin_students(organization_id: UUID):
    async with httpx.AsyncClient(timeout=10) as client:
        students_resp = await client.get(
            f"{SUPABASE_URL}/rest/v1/students?organization_id=eq.{organization_id}&select=id,first_name,last_name,phone,workplace&order=last_name",
            headers=supabase_headers()
        )
        students = students_resp.json()

        attempts_resp = await client.get(
            f"{SUPABASE_URL}/rest/v1/attempts?organization_id=eq.{organization_id}&select=student_id,grade",
            headers=supabase_headers()
        )
        attempts = attempts_resp.json()

    stats_by_student = {}
    for a in attempts:
        sid = a.get("student_id")
        if sid not in stats_by_student:
            stats_by_student[sid] = {"count": 0, "grades": []}
        stats_by_student[sid]["count"] += 1
        g = a.get("grade")
        if isinstance(g, (int, float)):
            stats_by_student[sid]["grades"].append(g)
        elif isinstance(g, str):
            try:
                stats_by_student[sid]["grades"].append(float(g))
            except ValueError:
                pass

    result = []
    for s in students:
        stat = stats_by_student.get(s["id"], {"count": 0, "grades": []})
        avg = round(sum(stat["grades"]) / len(stat["grades"])) if stat["grades"] else None
        result.append({
            "id": s["id"],
            "name": f"{s.get('last_name','')} {s.get('first_name','')}".strip(),
            "phone": s.get("phone"),
            "workplace": s.get("workplace"),
            "cases_completed": stat["count"],
            "average_grade": avg
        })

    return result


@app.get("/api/admin/student-attempts")
async def admin_student_attempts(student_id: UUID):
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(
            f"{SUPABASE_URL}/rest/v1/attempts?student_id=eq.{student_id}&order=finished_at.desc&select=id,case_title,grade,finished_at,duration_seconds",
            headers=supabase_headers()
        )
    return resp.json() if resp.status_code == 200 else []



# ── FEEDBACK (restricted submission, admin panel view) ───
@app.post("/api/feedback")
async def submit_feedback(request: Request):
    data = await request.json()
    student_id = data.get("student_id")
    phone = data.get("phone", "")
    message = (data.get("message") or "").strip()
    page = data.get("page", "")

    if not message:
        raise HTTPException(400, "Сообщение не может быть пустым")

    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(
            f"{SUPABASE_URL}/rest/v1/feedback",
            headers=supabase_headers(),
            json={
                "student_id": student_id,
                "phone": phone,
                "message": message,
                "page": page
            }
        )
    if resp.status_code not in (200, 201):
        raise HTTPException(500, "Не удалось сохранить обратную связь")
    return {"ok": True}


@app.get("/api/feedback")
async def list_feedback():
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(
            f"{SUPABASE_URL}/rest/v1/feedback?order=created_at.desc&select=*",
            headers=supabase_headers()
        )
    return resp.json() if resp.status_code == 200 else []


@app.put("/api/feedback/{feedback_id}/status")
async def update_feedback_status(feedback_id: UUID, request: Request):
    data = await request.json()
    status = data.get("status", "new")
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.patch(
            f"{SUPABASE_URL}/rest/v1/feedback?id=eq.{feedback_id}",
            headers=supabase_headers(),
            json={"status": status}
        )
    return {"ok": resp.status_code in (200, 204)}



