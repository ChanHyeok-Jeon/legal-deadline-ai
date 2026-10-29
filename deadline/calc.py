"""불변기한 만료일 계산.

계산 규칙
- 초일 불산입: 기준일 다음 날부터 센다 (민법 제157조, 형사소송법 제66조 제1항)
- 일 단위: 기준일 + n일 (n번째 날의 종료로 만료, 민법 제159조)
- 주·월 단위: 달력으로 계산해, 마지막 주·월에서 기산일에 해당하는 날의 전날 만료.
  그 날이 없으면 그 달의 말일 만료 (민법 제160조)
- 말일이 토요일·공휴일이면 다음 날로 만료 (민법 제161조, 형사소송법 제66조 제3항)
- 민사소송법 제170조, 행정소송법 제8조 제2항, 가사소송법 제12조는 민법·민사소송법을 따른다
"""
import calendar
import json
import os
from datetime import date, timedelta

from .rules import PROCEDURES

WEEKDAYS = "월화수목금토일"
_DATA = json.load(open(os.path.join(os.path.dirname(__file__), "data", "holidays_kr.json"), encoding="utf-8"))
HOLIDAYS = {date.fromisoformat(k): v for k, v in _DATA["holidays"].items()}
UNVERIFIED = {date.fromisoformat(k): v for k, v in _DATA["unverified"].items()}
COVERED_YEARS = {d.year for d in HOLIDAYS}


def fmt(d):
    return f"{d.isoformat()} ({WEEKDAYS[d.weekday()]})"


def closed_reason(d):
    """토·일·공휴일이면 이유를, 아니면 None."""
    if d in HOLIDAYS:
        return HOLIDAYS[d]
    if d.weekday() == 5:
        return "토요일"
    if d.weekday() == 6:
        return "일요일"
    return None


def _add_months(d, n):
    """d와 같은 '일'의 n개월 뒤 날짜. 그 달에 그 날이 없으면 None."""
    y, m = divmod(d.month - 1 + n, 12)
    y, m = d.year + y, m + 1
    return date(y, m, d.day) if d.day <= calendar.monthrange(y, m)[1] else None


def raw_end(base, n, unit, include_first=False):
    """휴일 연장 전의 만료일과 설명. include_first면 기준일 당일이 기산일."""
    start = base if include_first else base + timedelta(days=1)
    if unit == "day":
        return start + timedelta(days=n - 1), f"기산일 {fmt(start)}부터 {n}일째 되는 날"
    if unit == "week":
        return start + timedelta(weeks=n) - timedelta(days=1), f"기산일 {fmt(start)}부터 {n}주 (마지막 주에서 기산일에 해당하는 날의 전날)"
    if unit == "year":
        unit, n = "month", n * 12
    if unit == "month":
        same = _add_months(start, n)
        if same:
            return same - timedelta(days=1), f"기산일 {fmt(start)}부터 {n}개월 (마지막 달에서 기산일에 해당하는 날의 전날)"
        y, m = divmod(start.month - 1 + n, 12)
        y, m = start.year + y, m + 1
        last = date(y, m, calendar.monthrange(y, m)[1])
        return last, f"기산일 {fmt(start)}부터 {n}개월. 마지막 달에 {start.day}일이 없어 그 달의 말일 (민법 제160조 제3항)"
    raise ValueError(f"알 수 없는 단위: {unit}")


UNIT_KO = {"day": "일", "week": "주", "month": "개월", "year": "년"}


def compute(code, base, today=None, include_first=False, extend_holiday=True, n=None, unit=None):
    """절차 코드와 기준일로 만료일을 계산하고, 계산 과정을 단계별로 돌려준다.

    include_first: 초일 산입 (오전 0시부터 시작하는 기간 — 민법 제157조 단서,
                   형사 시효·구속기간 — 형사소송법 제66조 제1항 단서)
    extend_holiday: 말일이 토요일·공휴일이면 다음 날로 연장 (형사 시효·구속기간은 연장하지 않음 — 같은 조 제3항 단서)
    code="custom"이면 n, unit으로 기간을 직접 정한다.
    """
    if code == "custom":
        if not n or n < 1 or unit not in UNIT_KO:
            raise ValueError("직접 입력은 기간(1 이상)과 단위(일·주·개월·년)가 필요합니다")
        p = {"field": "직접 입력", "name": f"{n}{UNIT_KO[unit]} 기간", "n": n, "unit": unit,
             "base": "기준일", "law": "민법 제157조, 제159조~제161조 (기간 계산 일반 원칙)"}
    elif code in PROCEDURES:
        p = PROCEDURES[code]
    else:
        raise ValueError(f"알 수 없는 절차: {code}")
    steps = [f"기준일: {fmt(base)} ({p['base']})",
             "초일 산입: 기준일 당일부터 셉니다" if include_first else "초일 불산입: 기준일 다음 날부터 셉니다"]
    end, how = raw_end(base, p["n"], p["unit"], include_first)
    steps.append(f"단순 만료일: {fmt(end)} — {how}")

    warnings = []
    if include_first and code != "custom":
        warnings.append("이 절차는 원래 초일 불산입입니다. 초일 산입은 오전 0시부터 시작하는 기간이나 형사 시효·구속기간에 씁니다.")
    if not extend_holiday:
        steps.append("말일 휴일 연장 안 함: 토요일·공휴일이어도 그대로 만료합니다")
        if code != "custom":
            warnings.append("이 절차는 원래 말일이 휴일이면 연장됩니다. 연장하지 않는 것은 형사 시효·구속기간 등 예외입니다.")
    while extend_holiday and (reason := closed_reason(end)) is not None:
        nxt = end + timedelta(days=1)
        steps.append(f"{fmt(end)}은(는) {reason} → 다음 날 {fmt(nxt)}로 연장")
        if end in UNVERIFIED:
            warnings.append(f"{fmt(end)}: {UNVERIFIED[end]}")
        end = nxt
    if end in UNVERIFIED:
        warnings.append(f"{fmt(end)}: {UNVERIFIED[end]}")
    if end.year not in COVERED_YEARS:
        warnings.append(f"{end.year}년 공휴일 데이터가 없어 주말만 반영했습니다. 공휴일을 직접 확인하세요.")
    steps.append(f"최종 만료일: {fmt(end)} 24:00")

    today = today or date.today()
    return {
        "code": code, "field": p["field"], "name": p["name"],
        "period": f"{p['n']}{UNIT_KO[p['unit']]}",
        "options": {"include_first": include_first, "extend_holiday": extend_holiday},
        "base_label": p["base"], "base": base.isoformat(),
        "deadline": end.isoformat(), "deadline_label": fmt(end),
        "days_left": (end - today).days,
        "law": p["law"], "note": p.get("note", ""),
        "steps": steps, "warnings": warnings,
    }
