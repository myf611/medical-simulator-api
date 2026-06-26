from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import httpx
import os

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # В продакшене заменить на ваш домен
    allow_methods=["POST"],
    allow_headers=["*"],
)

GROQ_KEY = os.environ.get("GROQ_API_KEY")
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"

@app.get("/")
def root():
    return {"status": "ok", "service": "Patient Simulator API"}

@app.post("/api/chat")
async def chat(request: Request):
    if not GROQ_KEY:
        return JSONResponse({"error": "API key not configured"}, status_code=500)
    
    body = await request.json()
    
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(
            GROQ_URL,
            headers={
                "Authorization": f"Bearer {GROQ_KEY}",
                "Content-Type": "application/json"
            },
            json=body
        )
    
    return JSONResponse(content=response.json(), status_code=response.status_code)
