"""Интерфейс «Портфель-панели». Запуск: streamlit run app.py"""
from datetime import date

import pandas as pd
import streamlit as st

from src import db, demo_data, logic

st.set_page_config(page_title="Портфель-панель", page_icon="📊", layout="wide")

ICONS = {"Красный": "🔴 Красный", "Жёлтый": "🟡 Жёлтый", "Зелёный": "🟢 Зелёный", "—": "—"}


@st.cache_resource
def get_conn():
    conn = db.connect()
    if db.read_table(conn, "projects").empty:
        demo_data.seed(conn)
    return conn


conn = get_conn()
today = date.today()

# ---------- Боковая панель ----------
st.sidebar.title("📊 Портфель-панель")
st.sidebar.caption(f"Сегодня: {today.isoformat()}")
st.sidebar.warning("Данные в приложении **синтетические** (демонстрационные). "
                   "Это не данные реального университета.")
if st.sidebar.button("Пересоздать демо-данные"):
    demo_data.seed(conn)
    st.sidebar.success("Демо-данные загружены.")
    st.rerun()

projects = db.read_table(conn, "projects")
milestones = db.read_table(conn, "milestones")
employees = db.read_table(conn, "employees")
assignments = db.read_table(conn, "assignments")

if projects.empty:
    st.info("В базе нет проектов. Нажмите «Пересоздать демо-данные» или добавьте проект на вкладке «Проекты».")

assessed = (logic.assess_portfolio(projects, milestones, today) if not projects.empty
            else pd.DataFrame(columns=list(projects.columns) + [
                "planned_progress", "risk_score", "risk_level", "reasons", "days_since_update"]))
load = logic.employee_load(employees, assignments, projects)
kpis = logic.portfolio_kpis(assessed, load, milestones, today)

tab_dash, tab_proj, tab_load, tab_report, tab_effect, tab_about = st.tabs(
    ["Дашборд", "Проекты", "Нагрузка", "Отчёт", "Эффект", "О проекте"])

# ---------- Дашборд ----------
with tab_dash:
    st.header("Состояние портфеля")
    c = st.columns(5)
    c[0].metric("Проектов в работе", kpis["projects_active"], help=f"Всего: {kpis['projects_total']}")
    c[1].metric("В красной зоне", kpis["red_count"], f"{kpis['red_pct']}%", delta_color="off")
    c[2].metric("Актуальный статус", f"{kpis['fresh_status_pct']}%")
    c[3].metric("Просроч. контр. точек", kpis["overdue_milestones"])
    c[4].metric("Перегружено сотрудников", kpis["overloaded_employees"])

    if not assessed.empty:
        left, right = st.columns(2)
        with left:
            st.subheader("Проекты по уровню риска")
            counts = (assessed[assessed["risk_level"] != "—"]["risk_level"]
                      .value_counts().reindex(["Зелёный", "Жёлтый", "Красный"]).fillna(0))
            st.bar_chart(counts)
        with right:
            st.subheader("Проекты по направлениям")
            st.bar_chart(assessed["direction"].value_counts())

        st.subheader("Требуют внимания")
        bad = assessed[assessed["risk_level"].isin(["Красный", "Жёлтый"])].sort_values(
            "risk_score", ascending=False).copy()
        if bad.empty:
            st.success("Проблемных проектов нет.")
        else:
            bad["risk_level"] = bad["risk_level"].map(ICONS)
            st.dataframe(
                bad[["name", "direction", "owner", "risk_level", "risk_score", "reasons"]]
                .rename(columns={"name": "Проект", "direction": "Направление",
                                 "owner": "Руководитель", "risk_level": "Риск",
                                 "risk_score": "Балл", "reasons": "Причины"}),
                use_container_width=True, hide_index=True)

# ---------- Проекты ----------
with tab_proj:
    st.header("Проекты")
    if not assessed.empty:
        f1, f2 = st.columns(2)
        dirs = f1.multiselect("Направление", logic.DIRECTIONS, default=logic.DIRECTIONS)
        stats = f2.multiselect("Статус", logic.STATUSES, default=logic.STATUSES)
        view = assessed[assessed["direction"].isin(dirs) & assessed["status"].isin(stats)].copy()
        view["risk_level"] = view["risk_level"].map(ICONS)
        st.dataframe(
            view[["name", "direction", "owner", "status", "start_date", "end_date",
                  "planned_progress", "progress_fact", "budget_plan", "budget_fact",
                  "risk_level", "risk_score", "days_since_update"]]
            .rename(columns={
                "name": "Проект", "direction": "Направление", "owner": "Руководитель",
                "status": "Статус", "start_date": "Начало", "end_date": "Окончание",
                "planned_progress": "План, %", "progress_fact": "Факт, %",
                "budget_plan": "Бюджет план", "budget_fact": "Бюджет факт",
                "risk_level": "Риск", "risk_score": "Балл",
                "days_since_update": "Дней без обновления"}),
            use_container_width=True, hide_index=True)

        st.subheader("Обновить статус проекта")
        names = dict(zip(projects["id"], projects["name"]))
        pid = st.selectbox("Проект", list(names), format_func=lambda i: names[i])
        cur = projects[projects["id"] == pid].iloc[0]
        with st.form("update_form"):
            u1, u2, u3 = st.columns(3)
            prog = u1.number_input("Прогресс, %", 0.0, 1000.0, float(cur["progress_fact"]), step=1.0)
            spent = u2.number_input("Фактические расходы", 0.0, value=float(cur["budget_fact"]), step=1000.0)
            stat = u3.selectbox("Статус", logic.STATUSES, index=logic.STATUSES.index(cur["status"]))
            if st.form_submit_button("Сохранить"):
                errs = logic.validate_update(prog, spent, stat)
                if errs:
                    for e in errs:
                        st.error(e)
                else:
                    db.update_project_status(conn, pid, prog, spent, stat)
                    st.success("Сохранено.")
                    st.rerun()

        st.subheader("Контрольные точки проекта")
        ms = milestones[milestones["project_id"] == pid].sort_values("due_date")
        if ms.empty:
            st.caption("Контрольных точек нет.")
        for _, m in ms.iterrows():
            overdue = (not m["done"]) and logic.to_date(m["due_date"]) < today
            label = f"{m['title']} — до {m['due_date']}" + ("  ⚠️ просрочено" if overdue else "")
            new_val = st.checkbox(label, value=bool(m["done"]), key=f"ms_{m['id']}")
            if new_val != bool(m["done"]):
                db.set_milestone_done(conn, m["id"], new_val)
                st.rerun()
        with st.form("ms_form", clear_on_submit=True):
            t1, t2 = st.columns(2)
            ms_title = t1.text_input("Новая контрольная точка")
            ms_due = t2.date_input("Срок", value=today)
            if st.form_submit_button("Добавить точку"):
                if not ms_title.strip():
                    st.error("Укажите название контрольной точки.")
                else:
                    db.add_milestone(conn, pid, ms_title, ms_due)
                    st.rerun()

    st.subheader("Добавить проект")
    with st.form("add_form", clear_on_submit=True):
        a1, a2 = st.columns(2)
        n = a1.text_input("Название")
        d = a2.selectbox("Направление", logic.DIRECTIONS)
        o = a1.text_input("Руководитель")
        b = a2.number_input("Плановый бюджет", 0.0, value=0.0, step=10000.0)
        s = a1.date_input("Начало", value=today)
        e = a2.date_input("Окончание", value=today)
        if st.form_submit_button("Добавить"):
            errs = logic.validate_project(n, d, o, s, e, b)
            if errs:
                for er in errs:
                    st.error(er)
            else:
                db.add_project(conn, n, d, o, s, e, b)
                st.success("Проект добавлен со статусом «Идея».")
                st.rerun()

# ---------- Нагрузка ----------
with tab_load:
    st.header("Нагрузка сотрудников")
    st.caption(f"Считаются проекты в статусе «{logic.ACTIVE_STATUS}». "
               f"Перегрузка > {logic.OVERLOAD_PCT}%, недогрузка < {logic.UNDERLOAD_PCT}%.")
    if load.empty:
        st.info("Сотрудников нет.")
    else:
        st.bar_chart(load.set_index("name")["load_pct"])
        st.dataframe(
            load[["name", "role", "capacity_hours_week", "hours_assigned",
                  "projects_count", "load_pct", "load_status"]]
            .rename(columns={"name": "Сотрудник", "role": "Роль",
                             "capacity_hours_week": "Ёмкость, ч/нед",
                             "hours_assigned": "Назначено, ч/нед",
                             "projects_count": "Проектов", "load_pct": "Загрузка, %",
                             "load_status": "Статус"}),
            use_container_width=True, hide_index=True)

# ---------- Отчёт ----------
with tab_report:
    st.header("Еженедельный отчёт")
    md = logic.weekly_report_md(assessed, load, kpis, today)
    st.markdown(md)
    r1, r2 = st.columns(2)
    r1.download_button("Скачать Markdown", md, file_name=f"report_{today}.md", mime="text/markdown")
    r2.download_button("Скачать Excel", logic.export_excel(assessed, load, kpis),
                       file_name=f"report_{today}.xlsx",
                       mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

# ---------- Эффект ----------
with tab_effect:
    st.header("Оценка эффекта")
    st.info("Введите **свои** значения. Числа по умолчанию — гипотезы для демонстрации, "
            "а не данные реального отдела.")
    e1, e2 = st.columns(2)
    n_proj = e1.number_input("Проектов в отчёте", 0, 1000, max(kpis["projects_active"], 1))
    m_man = e2.number_input("Минут на проект при ручном своде", 0.0, 600.0, 20.0)
    m_app = e1.number_input("Минут на проект с приложением", 0.0, 600.0, 3.0)
    reps = e2.number_input("Отчётов в месяц", 0.0, 100.0, 4.0)
    cost = e1.number_input("Стоимость часа работы, ₽", 0.0, 100000.0, 500.0)
    res = logic.effect_estimate(n_proj, m_man, m_app, reps, cost)
    k = st.columns(3)
    k[0].metric("Часов экономии в месяц", res["hours_saved_month"])
    k[1].metric("Часов экономии в год", res["hours_saved_year"])
    k[2].metric("Экономия в год, ₽", f"{res['money_saved_year']:,.0f}".replace(",", " "))

# ---------- О проекте ----------
with tab_about:
    st.header("О проекте")
    st.markdown(f"""
**Проблема.** В университетском отделе, реализующем проекты в образовании, науке и
технологическом предпринимательстве, нет единой актуальной картины портфеля: статусы
собираются вручную, риски и перегрузка видны поздно.

**Решение.** Единая панель: план/факт по срокам и бюджету, автоматический балл риска
с причинами, загрузка сотрудников, отчёт в один клик.

**Правила риска** (баллы суммируются, максимум 100; жёлтый ≥ {logic.LEVEL_YELLOW_FROM},
красный ≥ {logic.LEVEL_RED_FROM}):
- отставание от планового прогресса ≥ {logic.SCHEDULE_GAP_WARN} / {logic.SCHEDULE_GAP_CRIT} п.п. → +20 / +40;
- расходы опережают прогресс ≥ {logic.BUDGET_GAP_WARN} / {logic.BUDGET_GAP_CRIT} п.п. → +15 / +30;
- просроченная контрольная точка → +{logic.OVERDUE_MS_POINTS} за каждую (максимум {logic.OVERDUE_MS_MAX});
- срок проекта истёк → +{logic.PAST_END_POINTS};
- статус не обновлялся > {logic.STALE_WARN_DAYS} / {logic.STALE_CRIT_DAYS} дней → +10 / +20.
""")
