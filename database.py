# =================================================================
# ФАЙЛ: database.py (ПОТОКОБЕЗОПАСНАЯ ВЕРСИЯ)
# =================================================================
import sqlite3
from datetime import date
import threading

# Это гарантирует, что у каждого потока будет свое собственное, изолированное подключение к БД.
thread_local = threading.local()
DB_FILE = 'pinterest_stats.db'

def get_db_connection():
    """Открывает новое подключение к БД или возвращает существующее для ТЕКУЩЕГО потока."""
    conn = getattr(thread_local, 'conn', None)
    if conn is None:
        conn = sqlite3.connect(DB_FILE, check_same_thread=False)
        thread_local.conn = conn
    return conn

def initialize_database():
    """Создает таблицы в БД, если они еще не существуют."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS Profiles (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        profile_name TEXT NOT NULL UNIQUE,
        profile_url TEXT NOT NULL,
        target_board_name TEXT NOT NULL,
        email TEXT,
        landing_url TEXT,
        is_active INTEGER DEFAULT 1
    )
    ''')
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS DailyStats (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        profile_id INTEGER,
        report_date TEXT,
        followers TEXT,
        monthly_views TEXT,
        pin_count TEXT,
        FOREIGN KEY (profile_id) REFERENCES Profiles (id),
        UNIQUE (profile_id, report_date)
    )
    ''')
    conn.commit()
    print("[ОК] База данных 'pinterest_stats.db' успешно создана/проверена.")

def get_active_profiles():
    """Возвращает список активных профилей для парсинга."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id, profile_name, profile_url, target_board_name FROM Profiles WHERE is_active = 1")
    profiles = cursor.fetchall()
    return profiles

def save_daily_stat(profile_id: int, parsed_data: dict):
    """Сохраняет или обновляет статистику за СЕГОДНЯШНИЙ день."""
    today = date.today().isoformat()
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
    INSERT OR REPLACE INTO DailyStats (profile_id, report_date, followers, monthly_views, pin_count)
    VALUES (?, ?, ?, ?, ?)
    ''', (
        profile_id,
        today,
        parsed_data.get('followers'),
        parsed_data.get('monthly_views'),
        parsed_data.get('pin_count')
    ))
    conn.commit()
    print(f"[ОК] Данные для профиля ID {profile_id} за {today} сохранены в БД.")

if __name__ == '__main__':
    initialize_database()