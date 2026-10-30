from datetime import date

import pandas as pd
import pytest

from src import db, demo_data, logic

TODAY = date(2026, 6, 15)


def make_project(**kw):
    base = dict(id=1, name="Тест", direction="Наука", owner="Иванов", status="В работе",
                start_date="2026-05-01", end_date="2026-07-30",  # ~50% времени прошло
                budget_plan=100.0, budget_fact=50.0, progress_fact=50.0,
                last_update="2026-06-14")
    base.update(kw)
    return base


EMPTY_MS = pd.DataFrame(columns=["project_id", "title", "due_date", "done"])


def ms(*items):
    return pd.DataFrame(items, columns=["project_id", "title", "due_date", "done"])


# ---------- плановый прогресс ----------
def test_planned_progress_middle():
    assert logic.planned_progress("2026-01-01", "2026-01-11", "2026-01-06") == 50.0


def test_planned_progress_before_and_after():
    assert logic.planned_progress("2026-02-01", "2026-03-01", "2026-01-01") == 0.0
    assert logic.planned_progress("2026-02-01", "2026-03-01", "2026-04-01") == 100.0


def test_planned_progress_accepts_date_and_timestamp():
    assert logic.planned_progress(date(2026, 1, 1), pd.Timestamp("2026-01-11"), date(2026, 1, 6)) == 50.0


# ---------- риск проекта ----------
def test_healthy_project_is_green():
    p = make_project()
    plan = logic.planned_progress(p["start_date"], p["end_date"], TODAY)
    p["progress_fact"], p["budget_fact"] = plan, plan  # 100 * plan%
    res = logic.assess_project(p, EMPTY_MS, TODAY)
    assert res["risk_level"] == "Зелёный" and res["risk_score"] == 0 and res["reasons"] == []


def test_schedule_gap_gives_yellow():
    p = make_project(progress_fact=10.0, budget_fact=10.0)
    res = logic.assess_project(p, EMPTY_MS, TODAY)
    assert res["risk_score"] == 40 and res["risk_level"] == "Жёлтый"


def test_schedule_and_budget_give_red():
    p = make_project(progress_fact=10.0, budget_fact=60.0)  # +40 срок, +30 бюджет
    res = logic.assess_project(p, EMPTY_MS, TODAY)
    assert res["risk_score"] == 70 and res["risk_level"] == "Красный"
    assert len(res["reasons"]) == 2


def test_overdue_milestones_capped():
    plan = logic.planned_progress("2026-05-01", "2026-07-30", TODAY)
    p = make_project(progress_fact=plan, budget_fact=plan)
    m = ms((1, "a", "2026-06-01", 0), (1, "b", "2026-06-02", 0), (1, "c", "2026-06-03", 0),
           (1, "d", "2026-06-04", 1),   # выполнена — не считается
           (2, "чужая", "2026-06-01", 0),  # чужой проект — не считается
           (1, "будущая", "2026-08-01", 0))
    res = logic.assess_project(p, m, TODAY)
    assert res["risk_score"] == logic.OVERDUE_MS_MAX


def test_past_end_date_adds_points():
    p = make_project(start_date="2026-01-01", end_date="2026-06-01",
                     progress_fact=100.0, budget_fact=100.0)
    res = logic.assess_project(p, EMPTY_MS, TODAY)
    assert res["risk_score"] == logic.PAST_END_POINTS


def test_stale_status():
    plan = logic.planned_progress("2026-05-01", "2026-07-30", TODAY)
    base = dict(progress_fact=plan, budget_fact=plan)
    warn = logic.assess_project(make_project(last_update="2026-05-30", **base), EMPTY_MS, TODAY)
    crit = logic.assess_project(make_project(last_update="2026-04-01", **base), EMPTY_MS, TODAY)
    assert warn["risk_score"] == 10 and crit["risk_score"] == 20


def test_finished_and_idea_projects_not_scored():
    for st in ("Завершён", "Идея"):
        res = logic.assess_project(make_project(status=st, progress_fact=0), EMPTY_MS, TODAY)
        assert res["risk_level"] == "—" and res["risk_score"] == 0


def test_score_capped_at_100():
    p = make_project(start_date="2026-01-01", end_date="2026-05-01", progress_fact=0.0,
                     budget_fact=100.0, last_update="2026-01-02")
    m = ms((1, "a", "2026-02-01", 0), (1, "b", "2026-02-02", 0))
    assert logic.assess_project(p, m, TODAY)["risk_score"] == 100


def test_zero_budget_does_not_crash():
    p = make_project(budget_plan=0.0, budget_fact=0.0)
    logic.assess_project(p, EMPTY_MS, TODAY)


def test_assess_portfolio_columns_and_rows():
    projects = pd.DataFrame([make_project(id=1), make_project(id=2, status="Завершён")])
    out = logic.assess_portfolio(projects, EMPTY_MS, TODAY)
    assert len(out) == 2
    assert {"risk_score", "risk_level", "reasons", "planned_progress",
            "days_since_update"} <= set(out.columns)


# ---------- нагрузка ----------
def test_employee_load_counts_only_active_projects():
    employees = pd.DataFrame([dict(id=1, name="А", role="р", capacity_hours_week=40),
                              dict(id=2, name="Б", role="р", capacity_hours_week=40),
                              dict(id=3, name="В", role="р", capacity_hours_week=40)])
    projects = pd.DataFrame([make_project(id=1), make_project(id=2, status="Завершён")])
    assignments = pd.DataFrame([
        dict(employee_id=1, project_id=1, hours_week=30),
        dict(employee_id=1, project_id=1, hours_week=15),   # итого 45 -> перегрузка
        dict(employee_id=1, project_id=2, hours_week=20),   # завершённый — не считается
        dict(employee_id=2, project_id=1, hours_week=20),   # 50% — норма
        dict(employee_id=3, project_id=2, hours_week=40),   # только завершённый -> 0%
    ])
    out = logic.employee_load(employees, assignments, projects).set_index("name")
    assert out.loc["А", "load_pct"] == 112.5 and out.loc["А", "load_status"] == "Перегружен"
    assert out.loc["Б", "load_status"] == "Норма"
    assert out.loc["В", "load_pct"] == 0 and out.loc["В", "load_status"] == "Недогружен"


def test_zero_capacity_does_not_crash():
    employees = pd.DataFrame([dict(id=1, name="А", role="р", capacity_hours_week=0)])
    projects = pd.DataFrame([make_project()])
    assignments = pd.DataFrame([dict(employee_id=1, project_id=1, hours_week=5)])
    assert logic.employee_load(employees, assignments, projects).loc[0, "load_pct"] == 0.0


# ---------- валидация ----------
def test_validate_project_ok():
    assert logic.validate_project("Проект", "Наука", "Иванов", "2026-01-01", "2026-02-01", 1000) == []


@pytest.mark.parametrize("args", [
    ("", "Наука", "И", "2026-01-01", "2026-02-01", 10),
    ("П", "Неизвестно", "И", "2026-01-01", "2026-02-01", 10),
    ("П", "Наука", " ", "2026-01-01", "2026-02-01", 10),
    ("П", "Наука", "И", "2026-02-01", "2026-01-01", 10),
    ("П", "Наука", "И", "2026-01-01", "2026-02-01", -5),
    ("П", "Наука", "И", "2026-01-01", "2026-02-01", "abc"),
    ("П", "Наука", "И", "не дата", "2026-02-01", 10),
])
def test_validate_project_errors(args):
    assert logic.validate_project(*args)


@pytest.mark.parametrize("args,ok", [
    ((50, 100, "В работе"), True), ((0, 0, "Идея"), True),
    ((101, 0, "В работе"), False), ((-1, 0, "В работе"), False),
    ((50, -10, "В работе"), False), ((50, 0, "Неизвестный"), False),
    (("x", 0, "В работе"), False),
])
def test_validate_update(args, ok):
    assert (logic.validate_update(*args) == []) is ok


# ---------- эффект ----------
def test_effect_estimate():
    r = logic.effect_estimate(10, 20, 5, 4, 600)
    assert r["hours_saved_month"] == 10.0          # 10 * 15 * 4 / 60
    assert r["hours_saved_year"] == 120.0
    assert r["money_saved_year"] == 72000


def test_effect_no_negative_saving_and_rejects_negative_input():
    assert logic.effect_estimate(10, 3, 5, 4, 600)["hours_saved_month"] == 0
    with pytest.raises(ValueError):
        logic.effect_estimate(-1, 3, 5, 4, 600)


# ---------- база + демо-данные + отчёт ----------
@pytest.fixture
def conn():
    c = db.connect(":memory:")
    demo_data.seed(c, TODAY)
    yield c
    c.close()


def load_all(conn):
    return (db.read_table(conn, "projects"), db.read_table(conn, "milestones"),
            db.read_table(conn, "employees"), db.read_table(conn, "assignments"))


def test_seed_counts(conn):
    p, m, e, a = load_all(conn)
    assert len(p) == len(demo_data.PROJECTS) and len(m) == len(demo_data.MILESTONES)
    assert len(e) == len(demo_data.EMPLOYEES) and len(a) == len(demo_data.ASSIGNMENTS)


def test_demo_data_shows_all_risk_levels_and_overload(conn):
    p, m, e, a = load_all(conn)
    assessed = logic.assess_portfolio(p, m, TODAY)
    levels = set(assessed["risk_level"])
    assert {"Зелёный", "Жёлтый", "Красный"} <= levels
    load = logic.employee_load(e, a, p)
    assert (load["load_status"] == "Перегружен").any()


def test_kpis_consistent(conn):
    p, m, e, a = load_all(conn)
    assessed = logic.assess_portfolio(p, m, TODAY)
    load = logic.employee_load(e, a, p)
    k = logic.portfolio_kpis(assessed, load, m, TODAY)
    assert k["projects_total"] == len(p)
    assert k["projects_active"] == int((p["status"] == "В работе").sum())
    assert 0 <= k["fresh_status_pct"] <= 100 and 0 <= k["red_pct"] <= 100
    assert k["red_count"] >= 1 and k["overdue_milestones"] >= 1


def test_kpis_empty_portfolio():
    empty_p = pd.DataFrame(columns=["id", "name", "status", "days_since_update", "risk_level",
                                    "budget_plan", "budget_fact"])
    empty_load = pd.DataFrame(columns=["load_status"])
    k = logic.portfolio_kpis(empty_p, empty_load, EMPTY_MS, TODAY)
    assert k["projects_active"] == 0 and k["red_pct"] == 0.0 and k["budget_execution_pct"] == 0.0


def test_update_project_and_milestone(conn):
    db.update_project_status(conn, 1, 80, 500000, "В работе", today=TODAY)
    row = db.read_table(conn, "projects").iloc[0]
    assert row["progress_fact"] == 80 and row["last_update"] == "2026-06-15"
    mid = int(db.read_table(conn, "milestones").query("done == 0").iloc[0]["id"])
    db.set_milestone_done(conn, mid, True)
    assert db.read_table(conn, "milestones").set_index("id").loc[mid, "done"] == 1


def test_add_project(conn):
    pid = db.add_project(conn, "  Новый  ", "Наука", " Кто-то ", "2026-07-01", "2026-12-01",
                         1000, today=TODAY)
    row = db.read_table(conn, "projects").set_index("id").loc[pid]
    assert row["name"] == "Новый" and row["status"] == "Идея" and row["progress_fact"] == 0


def test_read_table_rejects_unknown_table(conn):
    with pytest.raises(ValueError):
        db.read_table(conn, "sqlite_master; DROP TABLE projects")


def test_report_and_excel(conn):
    p, m, e, a = load_all(conn)
    assessed = logic.assess_portfolio(p, m, TODAY)
    load = logic.employee_load(e, a, p)
    k = logic.portfolio_kpis(assessed, load, m, TODAY)
    md = logic.weekly_report_md(assessed, load, k, TODAY)
    assert "2026-06-15" in md and "Проблемные проекты" in md
    worst = assessed.sort_values("risk_score", ascending=False).iloc[0]["name"]
    assert worst in md
    xlsx = logic.export_excel(assessed, load, k)
    assert xlsx[:2] == b"PK"
    sheets = pd.read_excel(__import__("io").BytesIO(xlsx), sheet_name=None)
    assert set(sheets) == {"KPI", "Проекты", "Нагрузка"}
