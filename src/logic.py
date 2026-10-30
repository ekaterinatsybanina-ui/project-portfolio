"""Бизнес-логика «Портфель-панели». Не зависит от Streamlit и от базы данных.

Все функции принимают дату «сегодня» (as_of) параметром, чтобы результат
был воспроизводимым и легко проверялся тестами.
"""
from __future__ import annotations

from datetime import date, datetime
from io import BytesIO

import pandas as pd

# ---------- Справочники и пороги (можно менять под отдел) ----------
DIRECTIONS = ["Образование", "Наука", "Технологическое предпринимательство"]
STATUSES = ["Идея", "В работе", "Приостановлен", "Завершён"]
RISK_STATUSES = ["В работе", "Приостановлен"]  # для каких статусов считаем риск
ACTIVE_STATUS = "В работе"

STALE_WARN_DAYS = 14      # статус не обновлялся дольше -> штраф
STALE_CRIT_DAYS = 30
SCHEDULE_GAP_WARN = 15    # отставание от планового прогресса, п.п.
SCHEDULE_GAP_CRIT = 30
BUDGET_GAP_WARN = 10      # расходы опережают прогресс, п.п.
BUDGET_GAP_CRIT = 20
OVERDUE_MS_POINTS = 15    # баллов за каждую просроченную контрольную точку
OVERDUE_MS_MAX = 30
PAST_END_POINTS = 30      # срок проекта истёк, а он не завершён

LEVEL_YELLOW_FROM = 25
LEVEL_RED_FROM = 50

OVERLOAD_PCT = 100
UNDERLOAD_PCT = 40


# ---------- Вспомогательные ----------
def to_date(value) -> date:
    """Приводит строку ISO, datetime или Timestamp к date."""
    if isinstance(value, str):
        return date.fromisoformat(value[:10])
    if isinstance(value, datetime):  # включает pd.Timestamp
        return value.date()
    if isinstance(value, date):
        return value
    raise ValueError(f"Не удалось распознать дату: {value!r}")


def planned_progress(start, end, as_of) -> float:
    """Плановый прогресс (0..100) — доля прошедшего времени проекта."""
    s, e, today = to_date(start), to_date(end), to_date(as_of)
    total = (e - s).days
    if total <= 0:
        return 100.0 if today >= e else 0.0
    share = (today - s).days / total
    return round(min(max(share, 0.0), 1.0) * 100, 1)


def risk_level(score: float) -> str:
    if score >= LEVEL_RED_FROM:
        return "Красный"
    if score >= LEVEL_YELLOW_FROM:
        return "Жёлтый"
    return "Зелёный"


# ---------- Риск проекта ----------
def assess_project(project: dict, milestones: pd.DataFrame, as_of) -> dict:
    """Считает балл риска (0..100), уровень и причины для одного проекта.

    project: словарь с ключами status, start_date, end_date, progress_fact,
             budget_plan, budget_fact, last_update, id.
    milestones: таблица с колонками project_id, due_date, done.
    """
    today = to_date(as_of)
    plan = planned_progress(project["start_date"], project["end_date"], today)

    if project["status"] not in RISK_STATUSES:
        return {"planned_progress": plan, "risk_score": 0, "risk_level": "—",
                "reasons": []}

    score, reasons = 0, []

    # 1. Отставание по срокам
    gap = plan - float(project["progress_fact"])
    if gap >= SCHEDULE_GAP_CRIT:
        score += 40
        reasons.append(f"Сильное отставание от плана: {gap:.0f} п.п.")
    elif gap >= SCHEDULE_GAP_WARN:
        score += 20
        reasons.append(f"Отставание от плана: {gap:.0f} п.п.")

    # 2. Бюджет опережает прогресс
    budget_plan = float(project["budget_plan"])
    if budget_plan > 0:
        spent_pct = float(project["budget_fact"]) / budget_plan * 100
        cost_gap = spent_pct - float(project["progress_fact"])
        if cost_gap >= BUDGET_GAP_CRIT:
            score += 30
            reasons.append(f"Расходы ({spent_pct:.0f}%) сильно опережают прогресс")
        elif cost_gap >= BUDGET_GAP_WARN:
            score += 15
            reasons.append(f"Расходы ({spent_pct:.0f}%) опережают прогресс")

    # 3. Просроченные контрольные точки
    overdue = 0
    if milestones is not None and len(milestones):
        own = milestones[milestones["project_id"] == project["id"]]
        for _, m in own.iterrows():
            if not int(m["done"]) and to_date(m["due_date"]) < today:
                overdue += 1
    if overdue:
        score += min(overdue * OVERDUE_MS_POINTS, OVERDUE_MS_MAX)
        reasons.append(f"Просрочено контрольных точек: {overdue}")

    # 4. Срок проекта истёк
    if to_date(project["end_date"]) < today:
        score += PAST_END_POINTS
        reasons.append("Плановая дата окончания прошла, проект не завершён")

    # 5. Устаревший статус
    days = (today - to_date(project["last_update"])).days
    if days > STALE_CRIT_DAYS:
        score += 20
        reasons.append(f"Статус не обновлялся {days} дн.")
    elif days > STALE_WARN_DAYS:
        score += 10
        reasons.append(f"Статус не обновлялся {days} дн.")

    score = min(score, 100)
    return {"planned_progress": plan, "risk_score": score,
            "risk_level": risk_level(score), "reasons": reasons}


def assess_portfolio(projects: pd.DataFrame, milestones: pd.DataFrame, as_of) -> pd.DataFrame:
    """Возвращает таблицу проектов с колонками риска."""
    rows = []
    for _, p in projects.iterrows():
        res = assess_project(p.to_dict(), milestones, as_of)
        row = p.to_dict()
        row.update(res)
        row["reasons"] = "; ".join(res["reasons"])
        row["days_since_update"] = (to_date(as_of) - to_date(p["last_update"])).days
        rows.append(row)
    cols = list(projects.columns) + ["planned_progress", "risk_score", "risk_level",
                                     "reasons", "days_since_update"]
    return pd.DataFrame(rows, columns=cols)


# ---------- Нагрузка сотрудников ----------
def employee_load(employees: pd.DataFrame, assignments: pd.DataFrame,
                  projects: pd.DataFrame) -> pd.DataFrame:
    """Загрузка = часы в неделю по проектам «В работе» / недельная ёмкость."""
    active_ids = set(projects.loc[projects["status"] == ACTIVE_STATUS, "id"])
    act = assignments[assignments["project_id"].isin(active_ids)]
    hours = act.groupby("employee_id")["hours_week"].sum()
    counts = act.groupby("employee_id")["project_id"].nunique()

    out = employees.copy()
    out["hours_assigned"] = out["id"].map(hours).fillna(0).astype(float)
    out["projects_count"] = out["id"].map(counts).fillna(0).astype(int)
    out["load_pct"] = out.apply(
        lambda r: round(r["hours_assigned"] / r["capacity_hours_week"] * 100, 1)
        if r["capacity_hours_week"] > 0 else 0.0, axis=1)

    def label(pct):
        if pct > OVERLOAD_PCT:
            return "Перегружен"
        if pct < UNDERLOAD_PCT:
            return "Недогружен"
        return "Норма"

    out["load_status"] = out["load_pct"].apply(label)
    return out


# ---------- KPI портфеля ----------
def portfolio_kpis(assessed: pd.DataFrame, load: pd.DataFrame,
                   milestones: pd.DataFrame, as_of) -> dict:
    today = to_date(as_of)
    active = assessed[assessed["status"] == ACTIVE_STATUS]
    n_active = len(active)

    fresh = int((active["days_since_update"] <= STALE_WARN_DAYS).sum()) if n_active else 0
    red = int((active["risk_level"] == "Красный").sum()) if n_active else 0
    plan_sum = float(active["budget_plan"].sum()) if n_active else 0.0
    fact_sum = float(active["budget_fact"].sum()) if n_active else 0.0

    overdue_ms = 0
    if milestones is not None and len(milestones):
        ids = set(active["id"])
        for _, m in milestones.iterrows():
            if (m["project_id"] in ids and not int(m["done"])
                    and to_date(m["due_date"]) < today):
                overdue_ms += 1

    return {
        "projects_total": int(len(assessed)),
        "projects_active": int(n_active),
        "fresh_status_pct": round(fresh / n_active * 100, 1) if n_active else 0.0,
        "red_pct": round(red / n_active * 100, 1) if n_active else 0.0,
        "red_count": red,
        "overdue_milestones": int(overdue_ms),
        "overloaded_employees": int((load["load_status"] == "Перегружен").sum()),
        "budget_execution_pct": round(fact_sum / plan_sum * 100, 1) if plan_sum else 0.0,
    }


# ---------- Проверка ввода ----------
def validate_project(name, direction, owner, start, end, budget_plan) -> list[str]:
    errors = []
    if not str(name or "").strip():
        errors.append("Укажите название проекта.")
    if direction not in DIRECTIONS:
        errors.append("Выберите направление из списка.")
    if not str(owner or "").strip():
        errors.append("Укажите руководителя проекта.")
    try:
        s, e = to_date(start), to_date(end)
        if e <= s:
            errors.append("Дата окончания должна быть позже даты начала.")
    except ValueError:
        errors.append("Некорректные даты.")
    try:
        if float(budget_plan) < 0:
            errors.append("Бюджет не может быть отрицательным.")
    except (TypeError, ValueError):
        errors.append("Бюджет должен быть числом.")
    return errors


def validate_update(progress_fact, budget_fact, status) -> list[str]:
    errors = []
    try:
        if not 0 <= float(progress_fact) <= 100:
            errors.append("Прогресс должен быть от 0 до 100%.")
    except (TypeError, ValueError):
        errors.append("Прогресс должен быть числом.")
    try:
        if float(budget_fact) < 0:
            errors.append("Фактические расходы не могут быть отрицательными.")
    except (TypeError, ValueError):
        errors.append("Расходы должны быть числом.")
    if status not in STATUSES:
        errors.append("Выберите статус из списка.")
    return errors


# ---------- Эффект ----------
def effect_estimate(projects_count: int, minutes_manual_per_project: float,
                    minutes_app_per_project: float, reports_per_month: float,
                    hourly_cost: float) -> dict:
    """Оценка экономии времени и денег. Все входные значения вводит пользователь."""
    if min(projects_count, minutes_manual_per_project, minutes_app_per_project,
           reports_per_month, hourly_cost) < 0:
        raise ValueError("Значения не могут быть отрицательными.")
    saved_min = max(minutes_manual_per_project - minutes_app_per_project, 0)
    hours_month = projects_count * saved_min * reports_per_month / 60
    return {
        "hours_saved_month": round(hours_month, 1),
        "hours_saved_year": round(hours_month * 12, 1),
        "money_saved_year": round(hours_month * 12 * hourly_cost, 0),
    }


# ---------- Отчёт ----------
def weekly_report_md(assessed: pd.DataFrame, load: pd.DataFrame, kpis: dict, as_of) -> str:
    today = to_date(as_of).isoformat()
    lines = [
        f"# Отчёт по портфелю проектов на {today}",
        "",
        "_Данные демонстрационные (синтетические), если не загружены собственные._",
        "",
        "## KPI",
        f"- Проектов всего: {kpis['projects_total']}, в работе: {kpis['projects_active']}",
        f"- Актуальный статус (≤{STALE_WARN_DAYS} дн.): {kpis['fresh_status_pct']}%",
        f"- Проектов в красной зоне: {kpis['red_count']} ({kpis['red_pct']}%)",
        f"- Просроченных контрольных точек: {kpis['overdue_milestones']}",
        f"- Перегруженных сотрудников: {kpis['overloaded_employees']}",
        f"- Исполнение бюджета: {kpis['budget_execution_pct']}%",
        "",
        "## Проблемные проекты",
    ]
    bad = assessed[assessed["risk_level"].isin(["Красный", "Жёлтый"])].sort_values(
        "risk_score", ascending=False)
    if bad.empty:
        lines.append("Проблемных проектов нет.")
    for _, r in bad.iterrows():
        lines.append(f"- **{r['name']}** ({r['direction']}, рук.: {r['owner']}) — "
                     f"{r['risk_level']}, балл {r['risk_score']}. {r['reasons']}")
    lines += ["", "## Нагрузка сотрудников"]
    flagged = load[load["load_status"] != "Норма"]
    if flagged.empty:
        lines.append("Все сотрудники в пределах нормы.")
    for _, r in flagged.iterrows():
        lines.append(f"- {r['name']}: {r['load_pct']}% ({r['load_status']})")
    return "\n".join(lines) + "\n"


def export_excel(assessed: pd.DataFrame, load: pd.DataFrame, kpis: dict) -> bytes:
    """Excel-файл с тремя листами: KPI, Проекты, Нагрузка."""
    buf = BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        pd.DataFrame(list(kpis.items()), columns=["Показатель", "Значение"]).to_excel(
            writer, sheet_name="KPI", index=False)
        assessed.drop(columns=["id"], errors="ignore").to_excel(
            writer, sheet_name="Проекты", index=False)
        load.drop(columns=["id"], errors="ignore").to_excel(
            writer, sheet_name="Нагрузка", index=False)
    return buf.getvalue()
