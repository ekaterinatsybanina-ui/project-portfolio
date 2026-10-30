"""Работа с SQLite: создание схемы и простые операции."""
from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path

import pandas as pd

DEFAULT_DB = Path(__file__).resolve().parent.parent / "data" / "portfolio.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    direction TEXT NOT NULL,
    owner TEXT NOT NULL,
    status TEXT NOT NULL,
    start_date TEXT NOT NULL,
    end_date TEXT NOT NULL,
    budget_plan REAL NOT NULL DEFAULT 0,
    budget_fact REAL NOT NULL DEFAULT 0,
    progress_fact REAL NOT NULL DEFAULT 0,
    last_update TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS milestones (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    due_date TEXT NOT NULL,
    done INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS employees (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    role TEXT NOT NULL,
    capacity_hours_week REAL NOT NULL DEFAULT 40
);
CREATE TABLE IF NOT EXISTS assignments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    employee_id INTEGER NOT NULL REFERENCES employees(id) ON DELETE CASCADE,
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    hours_week REAL NOT NULL
);
"""


def connect(path=DEFAULT_DB) -> sqlite3.Connection:
    if str(path) != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    return conn


def read_table(conn, table: str) -> pd.DataFrame:
    if table not in {"projects", "milestones", "employees", "assignments"}:
        raise ValueError("Неизвестная таблица")
    return pd.read_sql_query(f"SELECT * FROM {table}", conn)


def add_project(conn, name, direction, owner, start, end, budget_plan,
                status="Идея", today=None) -> int:
    today = (today or date.today()).isoformat()
    cur = conn.execute(
        "INSERT INTO projects (name, direction, owner, status, start_date, end_date,"
        " budget_plan, budget_fact, progress_fact, last_update)"
        " VALUES (?,?,?,?,?,?,?,0,0,?)",
        (name.strip(), direction, owner.strip(), status, str(start), str(end),
         float(budget_plan), today))
    conn.commit()
    return cur.lastrowid


def update_project_status(conn, project_id, progress_fact, budget_fact, status,
                          today=None) -> None:
    today = (today or date.today()).isoformat()
    conn.execute(
        "UPDATE projects SET progress_fact=?, budget_fact=?, status=?, last_update=?"
        " WHERE id=?",
        (float(progress_fact), float(budget_fact), status, today, int(project_id)))
    conn.commit()


def set_milestone_done(conn, milestone_id, done: bool) -> None:
    conn.execute("UPDATE milestones SET done=? WHERE id=?", (int(done), int(milestone_id)))
    conn.commit()


def add_milestone(conn, project_id, title, due_date) -> None:
    conn.execute("INSERT INTO milestones (project_id, title, due_date, done) VALUES (?,?,?,0)",
                 (int(project_id), title.strip(), str(due_date)))
    conn.commit()


def clear_all(conn) -> None:
    for t in ("assignments", "milestones", "employees", "projects"):
        conn.execute(f"DELETE FROM {t}")
    conn.commit()
