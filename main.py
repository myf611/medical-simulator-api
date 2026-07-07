from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import httpx
import os
import re
from datetime import datetime

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST", "HEAD", "OPTIONS"],
    allow_headers=["*"],
)

GROQ_KEY = os.environ.get("GROQ_API_KEY")
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_KEY")
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"

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
@app.post("/api/register")
async def register(request: Request):
    data = await request.json()
    last_name = data.get("last_name", "").strip()
    first_name = data.get("first_name", "").strip()
    raw = data.get("phone", "").strip()
    phone = '+' + raw.replace('+', '').replace(' ', '').replace('-', '')
    workplace = data.get("workplace", "").strip()
    region = data.get("region", "").strip()
    city = data.get("city", "").strip()
    org_slug = data.get("org_slug", "tashkent-endo")

    # Validate fields
    if not last_name or not first_name:
        raise HTTPException(400, "Введите фамилию и имя")
    if not UZ_PHONE_REGEX.match(phone):
        raise HTTPException(400, "Неверный формат номера. Пример: +998901234567")

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
            # Return existing student
            student = existing.json()[0]
            return {"student_id": student["id"], "name": f"{student['last_name']} {student['first_name']}", "existing": True}

        # Create student
        create_resp = await client.post(
            f"{SUPABASE_URL}/rest/v1/students",
            headers=supabase_headers(),
            json={
                "organization_id": org["id"],
                "last_name": last_name,
                "first_name": first_name,
                "phone": phone,
                "workplace": workplace,
                "region": region,
                "city": city
            }
        )
        student = create_resp.json()[0]
        return {"student_id": student["id"], "name": f"{student['last_name']} {student['first_name']}", "existing": False}


# ── LOGIN ────────────────────────────────────────────
@app.post("/api/login")
async def login(request: Request):
    data = await request.json()
    # Normalize phone - remove all spaces and non-digits except leading +
    raw_phone = data.get("phone", "").strip()
    # Remove spaces from phone number
    phone = '+' + raw_phone.replace('+', '').replace(' ', '').replace('-', '')
    org_slug = data.get("org_slug", "tashkent-endo")

    if not UZ_PHONE_REGEX.match(phone):
        raise HTTPException(400, f"Неверный формат номера: {phone}")

    print(f"LOGIN: searching phone={phone!r}")
    from urllib.parse import quote
    phone_encoded = quote(phone, safe='')
    async with httpx.AsyncClient(timeout=10) as client:
        existing = await client.get(
            f"{SUPABASE_URL}/rest/v1/students?phone=eq.{phone_encoded}&is_active=eq.true",
            headers=supabase_headers()
        )
        print(f"LOGIN: supabase response status={existing.status_code} body={existing.text[:200]}")
        students = existing.json()
        if not students:
            raise HTTPException(404, f"Номер не найден: {phone}")

        student = students[0]
        return {
            "student_id": student["id"],
            "name": f"{student['last_name']} {student['first_name']}",
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
@app.post("/api/attempt")
async def save_attempt(request: Request):
    data = await request.json()
    student_id = data.get("student_id")
    if not student_id:
        raise HTTPException(400, "student_id обязателен")

    async with httpx.AsyncClient(timeout=10) as client:
        # Look up organization_id server-side (don't trust client)
        student_resp = await client.get(
            f"{SUPABASE_URL}/rest/v1/students?id=eq.{student_id}&select=organization_id",
            headers=supabase_headers()
        )
        students = student_resp.json()
        if not students:
            raise HTTPException(404, "Студент не найден")
        organization_id = students[0]["organization_id"]

        resp = await client.post(
            f"{SUPABASE_URL}/rest/v1/attempts",
            headers=supabase_headers(),
            json={
                "student_id": student_id,
                "case_id": data.get("case_id"),
                "case_title": data.get("case_title"),
                "organization_id": organization_id,
                "finished_at": datetime.utcnow().isoformat(),
                "grade": data.get("grade"),
                "diagnosis": data.get("diagnosis"),
                "workup_plan": data.get("workup_plan"),
                "treatment_plan": data.get("treatment_plan"),
                "transcript": data.get("transcript", []),
                "duration_seconds": data.get("duration_seconds")
            }
        )
    return JSONResponse(content=resp.json(), status_code=resp.status_code)


# ── STUDENT: PROFILE ──────────────────────────────────
@app.get("/api/student-profile")
async def get_student_profile(student_id: str):
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
async def student_history(student_id: str):
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(
            f"{SUPABASE_URL}/rest/v1/attempts?student_id=eq.{student_id}&order=finished_at.desc&select=id,case_title,grade,diagnosis,finished_at,duration_seconds",
            headers=supabase_headers()
        )
        attempts = resp.json() if resp.status_code == 200 else []

    grades = [a["grade"] for a in attempts if isinstance(a.get("grade"), (int, float))]
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
@app.get("/api/admin/results")
async def get_results(org_slug: str, admin_key: str):
    if admin_key != os.environ.get("ADMIN_KEY", "admin123"):
        raise HTTPException(403, "Нет доступа")
    async with httpx.AsyncClient(timeout=10) as client:
        org_resp = await client.get(
            f"{SUPABASE_URL}/rest/v1/organizations?slug=eq.{org_slug}",
            headers=supabase_headers()
        )
        if not org_resp.json():
            raise HTTPException(404, "Организация не найдена")
        org_id = org_resp.json()[0]["id"]

        results = await client.get(
            f"{SUPABASE_URL}/rest/v1/attempts?organization_id=eq.{org_id}&order=finished_at.desc",
            headers=supabase_headers()
        )
    return results.json()
