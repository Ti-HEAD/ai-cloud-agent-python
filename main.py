from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
app = FastAPI(title="Simple AI Cloud Agent (LLM-backed)")
class AskRequest(BaseModel):
    input: str
@app.get("/health")
def health():
    return {"status":"ok"}
@app.post("/ask")
def ask(req: AskRequest):
    text = req.input.strip()
    if not text:
        raise HTTPException(status_code=400, detail="input is empty")
    return {"response": f"受け取った: {req.input}"}
