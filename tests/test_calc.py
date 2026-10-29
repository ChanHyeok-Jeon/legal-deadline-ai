"""손으로 계산한 기대값과 비교하는 테스트. 각 케이스 옆에 계산 근거를 적어 둔다."""
from datetime import date

import pytest

from deadline import compute

CASES = [
    # (절차, 기준일, 기대 만료일, 설명)
    ("civ_appeal", "2026-10-02", "2026-10-16", "금요일 송달 + 2주 → 금요일, 평일이라 그대로"),
    ("civ_appeal", "2026-09-11", "2026-09-28", "+2주 = 9/25 추석 → 9/26 추석 다음날(토) → 9/27 일 → 9/28 월"),
    ("crim_appeal", "2026-09-28", "2026-10-06", "+7일 = 10/5 개천절 대체공휴일 → 10/6"),
    ("crim_appeal", "2026-09-29", "2026-10-06", "+7일 = 10/6 화, 평일"),
    ("civ_immediate", "2026-04-24", "2026-05-04", "+1주 = 5/1 노동절 → 5/2 토 → 5/3 일 → 5/4 월"),
    ("civ_appeal_brief", "2026-11-02", "2026-12-14", "+40일 = 12/12 토 → 12/13 일 → 12/14 월"),
    ("admin_suit", "2026-07-01", "2026-09-29", "+90일 = 9/29 화"),
    ("family_appeal", "2026-07-03", "2026-07-20", "+14일 = 7/17 제헌절(금) → 7/18 토 → 7/19 일 → 7/20 월"),
    ("inherit_renounce", "2026-01-31", "2026-04-30", "기산일 2/1 → 5/1의 전날 4/30(목)"),
    ("inherit_renounce", "2026-11-29", "2027-03-02", "기산일 11/30 → 2027년 2월에 30일 없음 → 2/28(일) → 3/1 삼일절 → 3/2"),
    ("inherit_renounce", "2026-11-30", "2027-03-02", "기산일 12/1 → 3/1의 전날 2/28(일) → 3/1 삼일절 → 3/2"),
]


@pytest.mark.parametrize("code,base,expected,why", CASES)
def test_deadline(code, base, expected, why):
    r = compute(code, date.fromisoformat(base), today=date(2026, 1, 1))
    assert r["deadline"] == expected, f"{why}\n" + "\n".join(r["steps"])


def test_unknown_year_warns():
    r = compute("civ_appeal", date(2028, 3, 2))
    assert any("공휴일 데이터가 없어" in w for w in r["warnings"])


def test_steps_explain_extension():
    r = compute("civ_appeal", date(2026, 9, 11))
    assert any("추석" in s for s in r["steps"])


def test_include_first_day():
    # 초일 산입: 9/11 당일부터 7일 → 9/17(목)
    r = compute("custom", date(2026, 9, 11), include_first=True, n=7, unit="day")
    assert r["deadline"] == "2026-09-17"


def test_no_holiday_extension():
    # 시효·구속기간처럼 연장 안 함: 9/11 + 2주 = 9/25 추석이어도 그대로
    r = compute("custom", date(2026, 9, 11), extend_holiday=False, n=2, unit="week")
    assert r["deadline"] == "2026-09-25"


def test_custom_year_unit():
    # 초일 산입 1년: 2026-03-02 당일부터 → 2027-03-01의 전날? 기산일 3/2 → 2027-03-02의 전날 2027-03-01(삼일절) → 3/2
    r = compute("custom", date(2026, 3, 2), include_first=True, n=1, unit="year")
    assert r["deadline"] == "2027-03-02"


def test_custom_requires_period():
    with pytest.raises(ValueError):
        compute("custom", date(2026, 3, 2))
