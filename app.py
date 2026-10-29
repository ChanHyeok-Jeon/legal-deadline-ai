"""법정기간 계산 웹 API. AI는 문서·질문 읽기만, 계산은 규칙 엔진이 한다."""
import os
import time
from collections import defaultdict, deque
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from deadline import PROCEDURES, compute
from deadline.extract import IMAGE_FORMATS, ask, extract, transcribe_images
from deadline.fileread import MAX_BYTES, FileReadError, pdf_images, read_file

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
KST = timezone(timedelta(hours=9))
app = FastAPI(title="법정기간 계산 + AI 문서 읽기")

# AI 호출 남용 방지: IP당 1시간에 20회
_calls = defaultdict(deque)


def _rate_limit(request: Request, limit=20, window=3600):
    ip = request.headers.get("x-real-ip") or (request.client.host if request.client else "")
    q, now = _calls[ip], time.time()
    while q and now - q[0] > window:
        q.popleft()
    if len(q) >= limit:
        raise HTTPException(429, "잠시 후 다시 시도해 주세요.")
    q.append(now)


def _today():
    return datetime.now(KST).date()


def _with_result(r):
    r["result"] = None
    if r["procedure"] in PROCEDURES and r["base_date"]:
        r["result"] = compute(r["procedure"], date.fromisoformat(r["base_date"]), today=_today())
    return r


class CalcIn(BaseModel):
    code: str
    base: str
    include_first: bool = False
    extend_holiday: bool = True
    n: Optional[int] = None
    unit: Optional[str] = None


class TextIn(BaseModel):
    text: str


@app.get("/")
def index():
    return FileResponse(os.path.join(BASE_DIR, "web", "index.html"))


@app.get("/api/procedures")
def procedures():
    return [{"code": c, **{k: p[k] for k in ("field", "name", "base", "law")}} for c, p in PROCEDURES.items()]


@app.get("/api/samples")
def samples():
    d = os.path.join(BASE_DIR, "samples")
    return [{"name": f[3:-4].replace("_", " "), "text": open(os.path.join(d, f), encoding="utf-8").read()}
            for f in sorted(os.listdir(d)) if f.endswith(".txt")]


@app.post("/api/calc")
def calc(body: CalcIn):
    try:
        return compute(body.code, date.fromisoformat(body.base), today=_today(),
                       include_first=body.include_first, extend_holiday=body.extend_holiday, n=body.n, unit=body.unit)
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.post("/api/ask")
def ask_question(body: TextIn, request: Request):
    """자연어 질문 → 절차·기준일 읽기 → 규칙 엔진 계산"""
    if not body.text.strip():
        raise HTTPException(400, "질문을 입력해 주세요.")
    _rate_limit(request)
    return _with_result(ask(body.text, _today()))


@app.post("/api/extract")
def read_document(body: TextIn, request: Request):
    if not body.text.strip():
        raise HTTPException(400, "문서 내용을 넣어 주세요.")
    _rate_limit(request)
    return _with_result(extract(body.text[:8000]))


@app.post("/api/extract-file")
async def read_uploaded(request: Request, file: UploadFile = File(...)):
    """첨부 파일(문서·이미지·스캔 PDF)에서 글자를 얻어 AI로 읽는다. 파일은 저장하지 않는다."""
    name = (file.filename or "").lower()
    data = await file.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise HTTPException(400, "파일은 5MB까지 올릴 수 있습니다.")
    source, images = "text", []
    ext = name.rsplit(".", 1)[-1] if "." in name else ""
    try:
        if ext in IMAGE_FORMATS:
            images = [(data, IMAGE_FORMATS[ext])]
        else:
            try:
                text = read_file(name, data)
            except FileReadError:
                if ext != "pdf":
                    raise
                images = pdf_images(data)  # 글자가 없는 스캔 PDF면 페이지 이미지를 꺼내 본다
                if not images:
                    raise
        if images:
            _rate_limit(request)
            text, source = transcribe_images(images), "image"
            if len(text) < 10:
                raise FileReadError("이미지에서 글자를 읽지 못했습니다. 더 선명한 사진으로 다시 올려 주세요.")
    except FileReadError as e:
        raise HTTPException(400, str(e))
    finally:
        del data
    _rate_limit(request)
    r = extract(text[:8000])
    if source == "image":
        r["checks"].insert(0, "사진·스캔 문서는 글자 인식(OCR) 결과를 기준으로 확인했습니다. 원본 이미지와 날짜를 직접 대조하세요.")
    r.update(text=text[:8000], source=source)
    return _with_result(r)


@app.exception_handler(RuntimeError)
async def ai_error(request, exc):
    from fastapi.responses import JSONResponse
    return JSONResponse(status_code=502, content={"detail": str(exc)})


@app.get("/health")
def health():
    return {"status": "ok", "procedures": len(PROCEDURES)}
