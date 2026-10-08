# SPDX-License-Identifier: Apache-2.0
"""Project spend to completion and warn before a budget breach.

Hours always project. Tokens and USD project only when the assignee has a
cost model on the roster. A forecast warning does not halt dispatch. Halt
stays on actual hours already spent.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from planner.model import Plan, Roster, Task
from planner.schedule import ScheduleResult

WARN_RATIO = Decimal("0.8")


@dataclass
class SpendForecast:
    actual_hours: Decimal
    remaining_hours: Decimal
    forecast_hours: Decimal
    budget_hours: Decimal
    hour_ratio: Decimal | None
    hour_warning: bool
    hour_breach: bool
    forecast_tokens: Decimal | None
    token_ratio: Decimal | None
    token_warning: bool
    token_breach: bool
    forecast_usd: Decimal | None
    usd_ratio: Decimal | None
    usd_warning: bool
    usd_breach: bool

    @property
    def warnings(self) -> list[str]:
        names = []
        if self.hour_warning or self.hour_breach:
            names.append("hours")
        if self.token_warning or self.token_breach:
            names.append("tokens")
        if self.usd_warning or self.usd_breach:
            names.append("usd")
        return names

    @property
    def hold(self) -> bool:
        """True when the forecast is already over a budget, not merely near it."""
        return self.hour_breach or self.token_breach or self.usd_breach


def _duration(task: Task, result: ScheduleResult | None) -> Decimal:
    if result is not None:
        timing = result.by_id.get(task.id)
        if timing is not None:
            return timing.expected
    return task.estimate_hours.scheduling


def _remaining(task: Task, duration: Decimal) -> Decimal:
    if task.status in {"done", "cancelled"}:
        return Decimal(0)
    spent = task.actuals.hours if task.actuals.hours is not None else Decimal(0)
    left = duration - spent
    if left < 0:
        return Decimal(0)
    return left


def _flag(forecast: Decimal | None, budget: Decimal) -> tuple[Decimal | None, bool, bool]:
    if forecast is None or budget <= 0:
        return None, False, False
    ratio = forecast / budget
    return ratio, forecast >= budget * WARN_RATIO, forecast > budget


def project(plan: Plan, result: ScheduleResult | None = None, roster: Roster | None = None) -> SpendForecast:
    actual = Decimal(0)
    remaining = Decimal(0)
    token_known = False
    usd_known = False
    tokens = Decimal(0)
    usd = Decimal(0)
    for task in plan.tasks:
        spent = task.actuals.hours if task.actuals.hours is not None else Decimal(0)
        actual += spent
        duration = _duration(task, result)
        left = _remaining(task, duration)
        remaining += left
        if roster is None:
            continue
        bot = roster.get(task.assignee)
        if bot is None or bot.cost is None:
            continue
        weight = spent + left
        if task.status == "cancelled":
            weight = spent
        token_known = True
        usd_known = True
        tokens += weight * bot.cost.tokens_per_hour
        usd += weight * bot.cost.usd_per_hour
    forecast_hours = actual + remaining
    hour_ratio, hour_warning, hour_breach = _flag(forecast_hours, plan.budget.hours)
    token_ratio, token_warning, token_breach = _flag(
        tokens if token_known else None, Decimal(plan.budget.tokens)
    )
    usd_ratio, usd_warning, usd_breach = _flag(usd if usd_known else None, plan.budget.usd)
    return SpendForecast(
        actual_hours=actual,
        remaining_hours=remaining,
        forecast_hours=forecast_hours,
        budget_hours=plan.budget.hours,
        hour_ratio=hour_ratio,
        hour_warning=hour_warning,
        hour_breach=hour_breach,
        forecast_tokens=tokens if token_known else None,
        token_ratio=token_ratio,
        token_warning=token_warning,
        token_breach=token_breach,
        forecast_usd=usd if usd_known else None,
        usd_ratio=usd_ratio,
        usd_warning=usd_warning,
        usd_breach=usd_breach,
    )


def _band(warning: bool, breach: bool) -> str:
    if breach:
        return "breach"
    if warning:
        return "warn"
    return "ok"


def format_forecast(forecast: SpendForecast) -> list[str]:
    from planner.util import fmt_hours, fmt_ratio

    if forecast.hour_ratio is None:
        hours = f"{fmt_hours(forecast.forecast_hours)}/{fmt_hours(forecast.budget_hours)} (n/a)"
    else:
        hours = (
            f"{fmt_hours(forecast.forecast_hours)}/{fmt_hours(forecast.budget_hours)} "
            f"({fmt_ratio(forecast.hour_ratio)}) {_band(forecast.hour_warning, forecast.hour_breach)}"
        )
    lines = [f"  hours: {hours}"]
    if forecast.forecast_tokens is None:
        lines.append("  tokens: n/a")
    else:
        ratio = "n/a" if forecast.token_ratio is None else fmt_ratio(forecast.token_ratio)
        lines.append(
            f"  tokens: {fmt_hours(forecast.forecast_tokens)} "
            f"({ratio}) {_band(forecast.token_warning, forecast.token_breach)}"
        )
    if forecast.forecast_usd is None:
        lines.append("  usd: n/a")
    else:
        ratio = "n/a" if forecast.usd_ratio is None else fmt_ratio(forecast.usd_ratio)
        lines.append(
            f"  usd: {fmt_hours(forecast.forecast_usd)} "
            f"({ratio}) {_band(forecast.usd_warning, forecast.usd_breach)}"
        )
    return lines
