#!/usr/bin/env python3
import os
import subprocess
import requests
import json
from datetime import datetime
from pathlib import Path
from typing import List, Dict

GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
HF_API_TOKEN = os.getenv("HF_API_TOKEN")
MODEL_ID = os.getenv("MODEL_ID", "gpt2")  # default test model
REPO = os.getenv("GITHUB_REPOSITORY")
ISSUE_NUMBER = os.getenv("ISSUE_NUMBER")
GITHUB_API = "https://api.github.com"
HF_API = "https://api-inference.huggingface.co/models"

if not (GITHUB_TOKEN and HF_API_TOKEN and REPO and ISSUE_NUMBER):
    raise SystemExit("GITHUB_TOKEN, HF_API_TOKEN, GITHUB_REPOSITORY and ISSUE_NUMBER must be set")

owner, repo = REPO.split("/")

headers = {"Authorization": f"token {GITHUB_TOKEN}", "Accept": "application/vnd.github+json"}
hf_headers = {"Authorization": f"Bearer {HF_API_TOKEN}"}

def get_issue():
    url = f"{GITHUB_API}/repos/{owner}/{repo}/issues/{ISSUE_NUMBER}"
    r = requests.get(url, headers=headers)
    r.raise_for_status()
    return r.json()

def get_default_branch():
    url = f"{GITHUB_API}/repos/{owner}/{repo}"
    r = requests.get(url, headers=headers)
    r.raise_for_status()
    return r.json().get("default_branch", "main")

def safe_branch_name(base):
    timestamp = datetime.utcnow().strftime("%Y%m%d%H%M%S")
    return f"{base}-{timestamp}"

def run(cmd, check=True, **kwargs):
    print(">", cmd)
    res = subprocess.run(cmd, shell=True, check=check, text=True, **kwargs)
    return res

def call_hf_model(prompt: str, max_tokens=512) -> str:
    url = f"{HF_API}/{MODEL_ID}"
    payload = {"inputs": prompt, "parameters": {"max_new_tokens": max_tokens}}
    r = requests.post(url, headers=hf_headers, json=payload, timeout=120)
    r.raise_for_status()
    try:
        j = r.json()
        if isinstance(j, dict) and "generated_text" in j:
            return j["generated_text"]
        if isinstance(j, list) and len(j) > 0 and "generated_text" in j[0]:
            return j[0]["generated_text"]
        if isinstance(j, str):
            return j
        return json.dumps(j)
    except ValueError:
        return r.text

def prepare_prompt(issue_title: str, issue_body: str) -> str:
    return f"""
You are a code generation assistant. Given the following Issue title and body for a Python FastAPI project, produce a JSON object with the structure:
{{"files": [{{"path": "path/to/file.py", "content": "PLAIN_TEXT"}}, ...], "commit_message": "...", "pr_body": "..."}}

Issue title:
{issue_title}

Issue body:
{issue_body}

Rules:
- Only include files needed to implement the MVP for the issue.
- Ensure code is valid Python and include minimal tests runnable with pytest.
- Keep files small and focused.
- Response MUST be valid JSON only (no extra commentary). If you cannot, respond with {{"error":"explain reason"}}.
"""

def try_parse_json_from_text(text: str):
    text = text.strip()
    try:
        return json.loads(text)
    except Exception:
        start = text.find('{')
        if start != -1:
            try:
                return json.loads(text[start:])
            except Exception:
                pass
    return None

def write_files_from_spec(files_spec: List[Dict[str,str]]):
    for f in files_spec:
        path = Path(f["path"])
        path.parent.mkdir(parents=True, exist_ok=True)
        content = f.get("content", "")
        path.write_text(content, encoding="utf-8")

def git_commit_and_push(branch_name):
    run('git config user.name "autogen-bot"')
    run('git config user.email "autogen-bot@example.com"')
    run(f'git checkout -b {branch_name}')
    run('git add -A')
    run(f'git commit -m "Autogen: implement MVP for issue #{ISSUE_NUMBER}" || true')
    token_remote = f"https://x-access-token:{GITHUB_TOKEN}@github.com/{REPO}.git"
    run(f'git remote set-url origin {token_remote}')
    run(f'git push --set-upstream origin {branch_name}')

def create_pr(branch_name, base_branch, issue):
    title = f"Autogen: implement changes for issue #{ISSUE_NUMBER}"
    body = f"""This PR was generated automatically by agent-autogen workflow in response to issue #{ISSUE_NUMBER}.

Issue title: {issue.get('title')}

Please review the changes. CI will run automatically.
"""
    url = f"{GITHUB_API}/repos/{owner}/{repo}/pulls"
    payload = {"title": title, "head": branch_name, "base": base_branch, "body": body}
    r = requests.post(url, headers=headers, json=payload)
    # Debug output: show status and response body to help diagnose 4xx/5xx
    print("PR create status:", r.status_code)
    try:
        print("PR create response:", r.text)
    except Exception:
        print("PR create response: <unprintable>")
    r.raise_for_status()
    pr = r.json()
    print("PR created:", pr.get("html_url"))
    return pr

def fallback_write_minimal():
    Path("main.py").write_text('''from fastapi import FastAPI, HTTPException
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
''')
    Path("requirements.txt").write_text("fastapi\nuvicorn\npydantic\npytest\nrequests\n")
    Path("README.md").write_text(f"# Autogen fallback for issue #{ISSUE_NUMBER}\n")
    Path("tests/test_api.py").parent.mkdir(parents=True, exist_ok=True)
    Path("tests/test_api.py").write_text('''from fastapi.testclient import TestClient
from main import app
client = TestClient(app)
def test_health():
    r = client.get("/health")
    assert r.status_code == 200
''')

def main():
    issue = get_issue()
    title = issue.get("title","")
    body = issue.get("body","")
    prompt = prepare_prompt(title, body)
    print("Calling HF model", MODEL_ID)
    try:
        text = call_hf_model(prompt, max_tokens=512)
        spec = try_parse_json_from_text(text)
        if not spec or "files" not in spec:
            print("LLM did not return valid spec, falling back. LLM output saved to LLM_OUTPUT.txt")
            Path("LLM_OUTPUT.txt").write_text(text, encoding="utf-8")
            fallback_write_minimal()
        else:
            write_files_from_spec(spec["files"])
    except Exception as e:
        print("Error calling HF or processing output:", str(e))
        Path("LLM_ERROR.txt").write_text(str(e), encoding="utf-8")
        fallback_write_minimal()

    base_branch = get_default_branch()
    branch_base = f"autogen/issue-{ISSUE_NUMBER}"
    branch = safe_branch_name(branch_base)
    git_commit_and_push(branch)
    pr = create_pr(branch, base_branch, issue)
    print("Done. PR:", pr.get("html_url"))

if __name__ == "__main__":
    main()
