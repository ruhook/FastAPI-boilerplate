from datetime import date
from decimal import Decimal, ROUND_HALF_UP


TWO_DECIMALS = Decimal("0.01")


def get_month_bounds(month: str | None, *, today: date | None = None) -> tuple[date, date, str]:
    fallback = today or date.today()
    normalized = (month or "").strip()
    try:
        year, month_number = [int(part) for part in normalized.split("-", 1)]
        start = date(year, month_number, 1)
    except Exception:
        start = date(fallback.year, fallback.month, 1)

    if start.month == 12:
        end = date(start.year, 12, 31)
    else:
        end = date(start.year, start.month + 1, 1).replace(day=1)
        end = date.fromordinal(end.toordinal() - 1)
    return start, end, f"{start.year:04d}-{start.month:02d}"


def get_team_leader_bonus_multiplier(monthly_team_hours: Decimal) -> Decimal:
    rounded_hours = monthly_team_hours.quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    if rounded_hours < Decimal("800"):
        return Decimal("0.3")
    if rounded_hours < Decimal("1200"):
        return Decimal("0.4")
    if rounded_hours < Decimal("2000"):
        return Decimal("0.5")
    if rounded_hours < Decimal("3000"):
        return Decimal("0.6")
    return Decimal("0.8")


def calculate_team_leader_bonus(monthly_team_hours: Decimal) -> tuple[Decimal, Decimal]:
    normalized_hours = monthly_team_hours.quantize(TWO_DECIMALS, rounding=ROUND_HALF_UP)
    multiplier = get_team_leader_bonus_multiplier(normalized_hours)
    bonus = (normalized_hours * multiplier).quantize(TWO_DECIMALS, rounding=ROUND_HALF_UP)
    return multiplier, bonus
