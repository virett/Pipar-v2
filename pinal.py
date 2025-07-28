# ===================================================================================
# ФАЙЛ: pinal.py (ФИНАЛЬНАЯ ВЕРСЯ - ПОЛНАЯ ИЗОЛЯЦИЯ И РАНДОМИЗАЦИЯ)
# ===================================================================================
import json
import os
import re
from playwright.sync_api import sync_playwright, Page, TimeoutError
import database as db
from concurrent.futures import ThreadPoolExecutor
import time
import random

# --- КОНСТАНТЫ И ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИЯ (НЕ ТРОНУТЫ) ---
COOKIES_FILE = 'pinterest.json'
SESSION_FILE = 'pinterest_session.json'
# Снижаем количество потоков по умолчанию, чтобы уменьшить агрессивность парсера
MAX_WORKERS = 5

def normalize_cookies(cookies: list) -> list:
    # Эта функция не меняется
    normalized = []
    for cookie in cookies:
        if 'sameSite' in cookie:
            same_site_value = cookie['sameSite'].lower()
            if same_site_value == 'strict': cookie['sameSite'] = 'Strict'
            elif same_site_value == 'lax': cookie['sameSite'] = 'Lax'
            else: cookie['sameSite'] = 'None'
        normalized.append(cookie)
    return normalized

# --- ФУНКЦИЯ СБОРА ДАННЫХ (Упрощена, т.к. основная логика перехвата переехала) ---
def get_profile_data(page: Page, target_board_name: str, pre_captured_data: list) -> dict:
    """
    Собирает данные со страницы. Логика перехвата API теперь находится выше,
    эта функция проверяет уже полученные данные и выполняет скролл как запасной вариант.
    """
    print(f"[ИНФО] Начинаем сбор данных для доски '{target_board_name}'...")
    data = {"followers": None, "monthly_views": None, "pin_count": None}

    # ПОИСК ПОДПИСЧИКОВ И ПРОСМОТРОВ (РАБОЧАЯ ЛОГИКА НЕ ТРОНУТА)
    try:
        followers_text = page.locator('[data-test-id="profile-following-count"]').text_content(timeout=7000)
        data['followers'] = followers_text.strip()
        print(f"  [ОК] Найдены подписчики (following): {data['followers']}")
    except Exception:
        print("  [ИНФО] Элемент 'profile-following-count' не найден.")
    try:
        stats_container = page.locator("div:has-text('monthly views')").first
        full_stats_text = stats_container.text_content(timeout=15000)
        views_match = re.search(r'([\d\.,\s]+[kKmM]?)\s*monthly views', full_stats_text, re.IGNORECASE)
        if views_match:
            data['monthly_views'] = views_match.group(1).strip()
            print(f"  [ОК] Найдены просмотры: {data['monthly_views']}")
    except Exception:
        print("  [ИНФО] Элемент, содержащий 'monthly views', не найден.")

    # ПОИСК ПИНОВ (ЛОГИКА НЕ ТРОНУТА, ОНА ОСТАЛАСЬ КАК ЗАПАСНОЙ МЕХАНИЗМ)
    # Сначала проверяем, не поймал ли наш слушатель данные еще на этапе загрузки.
    if not pre_captured_data:
        print("[ДЕЙСТВИЕ] Данные не были перехвачены при загрузке. Выполняем скроллинг как запасной вариант...")
        for i in range(5):
            # Если данные появились после очередной итерации, выходим
            if pre_captured_data:
                print("  [ИНФО] Данные пойманы во время скроллинга, прекращаем.")
                break
            print(f"  [ДЕЙСТВИЕ] Попытка скролла #{i+1}: Скроллим страницу и ждем...")
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            page.wait_for_timeout(3000)

    # Обрабатываем результат (либо изначальный, либо после скролла)
    if pre_captured_data:
        pin_count = pre_captured_data[0].get('pin_count')
        if pin_count is not None:
            data['pin_count'] = pin_count
            print(f"  [ОК] Найдено точное количество пинов: {data['pin_count']}")
    else:
        print(f"  [ПРОВАЛ] После всех попыток не удалось перехватить API-запрос с данными.")

    return data

# --- ИЗМЕНЕННАЯ ФУНКЦИЯ-ОБРАБОТЧИК (КЛЮЧЕВЫЕ ИЗМЕНЕНИЯ ЗДЕСЬ) ---
def process_single_profile(profile_info):
    """
    Запускает ПОЛНОСТЬЮ ИЗОЛИРОВАННЫЙ процесс браузера для ОДНОГО профиля.
    Теперь "слушатель" API-запросов устанавливается здесь, ДО перехода на страницу.
    """
    profile_id, profile_name, profile_url, target_board_name = profile_info
    print("\n" + "="*50)
    print(f"[РАБОТА] Запускаем изолированный процесс для '{profile_name}' (ID: {profile_id})")

    # Переменная для хранения данных, пойманных "слушателем".
    # Используем список, чтобы он был изменяемым внутри обработчика.
    found_board_data = []

    # Определяем функцию-обработчик здесь, чтобы она имела доступ
    # к found_board_data и target_board_name.
    def handle_response(response):
        if not found_board_data and "resource/BoardsResource/get" in response.url and response.ok:
            print(f"  [ПЕРЕХВАТ] Обнаружен подходящий API-ответ. Проверяем содержимое...")
            try:
                response_json = response.json()
                if 'resource_response' in response_json and 'data' in response_json['resource_response']:
                    boards_list = response_json['resource_response']['data']
                    if boards_list:
                        for board in boards_list:
                            if board.get('name') == target_board_name:
                                print(f"    [УСПЕХ!] Найдена целевая доска '{target_board_name}' в API-ответе.")
                                found_board_data.append(board)
                                break # Прекращаем поиск по доскам
            except Exception as e:
                print(f"    [ОШИБКА ОБРАБОТКИ] Не удалось разобрать JSON из ответа: {e}")
    
    with sync_playwright() as p:
        storage_state = SESSION_FILE if os.path.exists(SESSION_FILE) else None
        browser = p.chromium.launch(headless=True, channel="chrome")
        context = browser.new_context(storage_state=storage_state)
        page = context.new_page()

        # === КЛЮЧЕВОЕ ИЗМЕНЕНИЕ ===
        # 1. Включаем "слушателя" на пустой, еще не загруженной странице.
        print("[ИНФО] Устанавливаем фоновый перехватчик API-запросов...")
        page.on("response", handle_response)
        
        try:
            # 2. И ТОЛЬКО ТЕПЕРЬ переходим на URL. Слушатель уже активен и готов ловить.
            print(f"[ИНФО] Переходим на страницу профиля '{profile_name}'...")
            page.goto(profile_url, timeout=60000, wait_until="domcontentloaded")
            
            page.locator('h1').wait_for(timeout=20000)
            print(f"[ИНФО] Страница профиля '{profile_name}' загружена.")

            # 3. Передаем список для результатов в функцию. Он может быть уже заполнен.
            parsed_data = get_profile_data(page, target_board_name, found_board_data)

            if any(v is not None for v in parsed_data.values()):
                db.save_daily_stat(profile_id, parsed_data)
            else:
                print(f"[ПРЕДУПРЕЖДЕНИЕ] Для '{profile_name}' не собрано данных.")
        except Exception as e:
            print(f"\n[КРИТИЧЕСКАЯ ОШИБКА] Профиль '{profile_name}': {e}")
        finally:
            # 4. Гарантированно отключаем слушателя и закрываем ресурсы.
            page.remove_listener("response", handle_response)
            page.close()
            context.close()
            browser.close()
            print(f"--- Процесс для '{profile_name}' полностью завершен. ---")

# --- ГЛАВНАЯ ФУНКЦИЯ-ОРКЕСТРАТОР (НЕ ТРОНУТА) ---
def main():
    db.initialize_database()
    profiles_to_parse = db.get_active_profiles()
    if not profiles_to_parse:
        print("[ИНФО] В базе данных нет активных профилей для парсинга.")
        return
    
    num_workers = min(MAX_WORKERS, len(profiles_to_parse))
    print(f"[ИНФО] Найдено {len(profiles_to_parse)} профилей для обработки в {num_workers} потоков.")

    # Используем ThreadPoolExecutor, чтобы запускать ИЗОЛИРОВАННЫЕ функции process_single_profile
    with ThreadPoolExecutor(max_workers=num_workers) as executor:
        for profile in profiles_to_parse:
            executor.submit(process_single_profile, profile)
            # ДОБАВЛЯЕМ ДЛИННУЮ СЛУЧАЙНУЮ ПАУЗУ МЕЖДУ ЗАПУСКАМИ НОВЫХ ПРОЦЕССОВ
            # pause_duration = random.uniform(10.0, 25.0)
            # print(f"\n[ПАУЗА] Следующий профиль будет запущен через {pause_duration:.1f} секунд, чтобы сломать паттерн...")
            # time.sleep(pause_duration)

    print("\n[ИНФО] ВСЕ РАБОТЫ ЗАВЕРШЕНЫ.")

if __name__ == "__main__":
    main()