"""문서에서 기간 계산에 필요한 값(절차, 기준일)을 AI로 읽어 낸다.

AI는 '읽기'만 하고, 만료일 계산은 calc.py의 규칙 엔진이 한다.
AI가 근거로 든 문장이 실제 문서에 있는지 코드로 다시 확인해, 지어낸 값을 걸러 낸다.
"""
import json
import os
import re
from datetime import date

import boto3

from .rules import PROCEDURES

MODEL_ID = os.environ.get("BEDROCK_MODEL_ID", "apac.amazon.nova-pro-v1:0")
# 사진·스캔 글자 옮기기는 한국어 이미지 인식이 더 정확한 Claude Haiku를 쓴다 (Nova는 한국어 이미지에서 환각이 잦았음)
OCR_MODEL_ID = os.environ.get("BEDROCK_OCR_MODEL_ID", "global.anthropic.claude-haiku-4-5-20251001-v1:0")
_client = None


def _bedrock():
    global _client
    if _client is None:
        _client = boto3.client("bedrock-runtime", region_name=os.environ.get("AWS_REGION", "ap-northeast-2"))
    return _client


def _procedure_guide():
    return "\n".join(f"- {code}: {p['field']} {p['name']} (기준일: {p['base']})" for code, p in PROCEDURES.items())


SYSTEM = f"""당신은 법원 문서에서 기간 계산에 필요한 정보만 뽑는 도우미입니다.
기간이나 만료일은 절대 계산하지 마세요. 문서에 적힌 사실만 옮기세요.

절차 코드 목록:
{_procedure_guide()}

규칙:
- procedure: 문서가 어떤 기한과 관련되는지 위 목록의 코드 하나. 판단할 수 없으면 "unknown".
- base_date: 그 절차의 기준일(송달받은 날, 선고일, 통지받은 날 등). 문서에 없으면 빈 문자열.
- evidence: procedure와 base_date를 판단한 문서 속 문장을 '원문 그대로' 한두 개 복사하세요. 바꿔 쓰지 마세요.
- missing: 계산에 필요한데 문서에 없는 정보를 한국어로 적으세요 (예: "송달받은 날짜가 없음")."""

TOOL = {
    "toolSpec": {
        "name": "report_deadline_inputs",
        "description": "문서에서 읽은 절차와 기준일을 보고한다",
        "inputSchema": {"json": {
            "type": "object",
            "properties": {
                "procedure": {"type": "string", "enum": list(PROCEDURES) + ["unknown"]},
                "base_date": {"type": "string", "description": "YYYY-MM-DD 또는 빈 문자열"},
                "case_number": {"type": "string", "description": "사건번호, 없으면 빈 문자열"},
                "evidence": {"type": "array", "items": {"type": "string"}},
                "missing": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["procedure", "base_date", "evidence", "missing"],
        }},
    }
}


def _converse_tool(**kw):
    """모델이 가끔 도구 호출 형식을 어기는 경우가 있어 두 번까지 다시 시도한다."""
    last = None
    for _ in range(3):
        try:
            res = _bedrock().converse(**kw)
            return next(c["toolUse"]["input"] for c in res["output"]["message"]["content"] if "toolUse" in c)
        except (StopIteration, _bedrock().exceptions.ModelErrorException) as e:
            last = e
    raise RuntimeError("AI가 응답을 만들지 못했습니다. 잠시 후 다시 시도해 주세요.") from last


def _norm(s):
    return re.sub(r"\s+", "", s)


def extract(text):
    """문서 텍스트 → {procedure, base_date, case_number, evidence:[{text, found}], missing, checks}"""
    out = _converse_tool(
        modelId=MODEL_ID,
        system=[{"text": SYSTEM}],
        messages=[{"role": "user", "content": [{"text": "다음 문서를 읽고 보고하세요.\n\n<문서>\n" + text[:8000] + "\n</문서>"}]}],
        toolConfig={"tools": [TOOL], "toolChoice": {"tool": {"name": "report_deadline_inputs"}}},
        inferenceConfig={"maxTokens": 600, "temperature": 0},
    )

    checks = []
    # 1) 근거 문장이 실제 문서에 있는지 (공백 차이는 무시)
    doc = _norm(text)
    evidence = [{"text": e, "found": _norm(e) in doc} for e in out.get("evidence", []) if e.strip()]
    if not evidence or not any(e["found"] for e in evidence):
        checks.append("AI가 든 근거 문장을 문서에서 찾지 못했습니다. 값을 직접 확인하세요.")
    # 2) 날짜 형식과 문서 속 등장 여부
    base = out.get("base_date", "")
    try:
        d = date.fromisoformat(base) if base else None
    except ValueError:
        d, base = None, ""
        checks.append("기준일 형식이 올바르지 않습니다.")
    if d:
        variants = {f"{d.year}. {d.month}. {d.day}.", f"{d.year}.{d.month}.{d.day}", f"{d.year}년 {d.month}월 {d.day}일",
                    d.isoformat(), f"{d.year}.{d.month:02d}.{d.day:02d}"}
        if not any(_norm(v) in doc for v in variants):
            checks.append(f"기준일 {base}이(가) 문서에 그대로 적혀 있지 않습니다. 날짜를 직접 확인하세요.")
    if not base:
        checks.append("문서에 기준일(송달일·선고일 등)이 없습니다. 날짜를 직접 입력하세요.")
    if out.get("procedure") == "unknown":
        checks.append("어떤 기한인지 판단하지 못했습니다. 절차를 직접 고르세요.")
    return {
        "procedure": out.get("procedure", "unknown"),
        "base_date": base,
        "case_number": out.get("case_number", ""),
        "evidence": evidence,
        "missing": out.get("missing", []),
        "checks": checks,
    }


# ── 자연어 질문 ─────────────────────────────────────
ASK_TOOL = {
    "toolSpec": {
        "name": "report_question_inputs",
        "description": "질문에서 읽은 절차와 기준일을 보고한다",
        "inputSchema": {"json": {
            "type": "object",
            "properties": {
                "procedure": {"type": "string", "enum": list(PROCEDURES) + ["unknown"]},
                "date_expression": {"type": "string", "description": "질문 속 날짜 표현을 원문 그대로 (예: '어제', '11월 3일'). 없으면 빈 문자열"},
                "base_date": {"type": "string", "description": "date_expression을 오늘 날짜 기준으로 해석한 YYYY-MM-DD. 없으면 빈 문자열"},
                "missing": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["procedure", "date_expression", "base_date", "missing"],
        }},
    }
}


def ask(question, today):
    """자연어 질문 → {procedure, date_expression, base_date, missing, checks}. 계산은 하지 않는다."""
    WEEK = "월화수목금토일"
    system = (SYSTEM.replace("문서에서", "질문에서") +
              f"\n\n오늘은 {today.isoformat()} ({WEEK[today.weekday()]}요일)입니다. '어제', '지난주 금요일' 같은 표현은 오늘 기준으로 날짜로 바꾸세요. "
              "연도가 없으면 오늘과 가장 가까운 과거 날짜로 보세요. "
              "민사·형사·가사·행정 같은 분야가 질문에 없으면 민사 절차로 고르세요.")
    out = _converse_tool(
        modelId=MODEL_ID,
        system=[{"text": system}],
        messages=[{"role": "user", "content": [{"text": question[:1000]}]}],
        toolConfig={"tools": [ASK_TOOL], "toolChoice": {"tool": {"name": "report_question_inputs"}}},
        inferenceConfig={"maxTokens": 400, "temperature": 0},
    )
    checks = []
    expr, base = out.get("date_expression", ""), out.get("base_date", "")
    try:
        d = date.fromisoformat(base) if base else None
    except ValueError:
        d, base = None, ""
    if expr and _norm(expr) not in _norm(question):
        checks.append("AI가 든 날짜 표현이 질문에 없습니다. 날짜를 직접 확인하세요.")
    if not d:
        checks.append("질문에 기준일이 없습니다. 날짜를 직접 입력하세요.")
    elif d > today:
        checks.append(f"기준일 {base}이(가) 오늘보다 뒤입니다. 날짜를 확인하세요.")
    proc = out.get("procedure", "unknown")
    if proc == "unknown":
        checks.append("어떤 기한인지 판단하지 못했습니다. 절차를 직접 고르세요.")
    else:
        # 질문에 분야를 짐작할 단어가 없으면, AI가 고른 분야를 사람이 확인하게 한다
        hints = {"민사": ("민사", "손해배상", "대여금", "지급명령"), "형사": ("형사", "선고", "피고인", "징역", "벌금"),
                 "가사": ("가사", "이혼", "양육"), "행정": ("행정", "처분", "취소소송", "행정심판"), "상속": ("상속",)}
        field = PROCEDURES[proc]["field"]
        if not any(w in question for w in hints.get(field, ())):
            checks.append(f"질문에 분야가 적혀 있지 않아 {field} 절차({PROCEDURES[proc]['name']})로 보았습니다. 맞는지 확인하세요.")
    return {"procedure": proc, "date_expression": expr, "base_date": base,
            "missing": out.get("missing", []), "checks": checks, "evidence": [], "case_number": ""}


# ── 이미지(사진·스캔) 글자 옮기기 ──────────────────────
IMAGE_FORMATS = {"png": "png", "jpg": "jpeg", "jpeg": "jpeg", "webp": "webp", "gif": "gif"}


def transcribe_images(images):
    """[(bytes, format)] → AI가 옮겨 적은 텍스트. 읽을 수 없는 부분은 [판독 불가]로 표시하게 한다."""
    content = [{"image": {"format": fmt_, "source": {"bytes": b}}} for b, fmt_ in images[:3]]
    content.append({"text": "이미지 속 문서의 글자를 보이는 그대로 옮겨 적으세요. 요약하거나 고치지 말고, "
                            "날짜와 숫자는 특히 정확히 옮기세요. 읽을 수 없는 부분은 [판독 불가]라고 적으세요. 옮긴 글자만 답하세요."})
    res = _bedrock().converse(
        modelId=OCR_MODEL_ID,
        messages=[{"role": "user", "content": content}],
        inferenceConfig={"maxTokens": 2000, "temperature": 0},
    )
    return "".join(c.get("text", "") for c in res["output"]["message"]["content"]).strip()
