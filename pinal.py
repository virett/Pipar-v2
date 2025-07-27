# ===================================================================================
# ФАЙЛ: pinal.py (ФИНАЛЬНАЯ ВЕРСИЯ - ПЕРЕХВАТ API-ЗАПРОСА)
# ===================================================================================
import json
import os
import re
from playwright.sync_api import sync_playwright, Page, TimeoutError
import database as db
from concurrent.futures import ThreadPoolExecutor

# --- КОНСТАНТЫ И ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ (БЕЗ ИЗМЕНЕНИЙ) ---
COOKIES_FILE = 'pinterest.json'
SESSION_FILE = 'pinterest_session.json'
MAX_WORKERS = 5 # Количество одновременных потоков

def normalize_cookies(cookies: list) -> list:
    """Приводит ключ 'sameSite' в куки к формату, который понимает Playwright."""
    normalized = []
    for cookie in cookies:
        if 'sameSite' in cookie:
            same_site_value = cookie['sameSite'].lower()
            if same_site_value == 'strict': cookie['sameSite'] = 'Strict'
            elif same_site_value == 'lax': cookie['sameSite'] = 'Lax'
            else: cookie['sameSite'] = 'None'
        normalized.append(cookie)
    return normalized

# --- ИЗМЕНЕННАЯ ФУНКЦИЯ СБОРА ДАННЫХ ---
def get_profile_data(page: Page, target_board_name: str) -> dict:
    """
    Собирает данные со страницы профиля и получает точное количество пинов
    путем перехвата фонового API-запроса.
    """
    print(f"[ИНФО] Начинаем сбор данных для доски '{target_board_name}'...")
    data = {"followers": None, "monthly_views": None, "pin_count": None}
    timeout = 15000

    # 1. ПОИСК ПОДПИСЧИКОВ (ЛОГИКА НЕ ИЗМЕНИЛАСЬ)
    try:
        followers_text = page.locator('[data-test-id="profile-following-count"]').text_content(timeout=5000)
        data['followers'] = followers_text.strip()
        print(f"  [ОК] Найдены подписчики (following): {data['followers']}")
    except Exception:
        print("  [ИНФО] Элемент 'profile-following-count' не найден.")

    # 2. ПОИСК ПРОСМОТРОВ (ЛОГИКА НЕ ИЗМЕНИЛАСЬ)
    try:
        stats_container = page.locator("div:has-text('monthly views')").first
        full_stats_text = stats_container.text_content(timeout=timeout)
        views_match = re.search(r'([\d\.,\s]+[kKmM]?)\s*monthly views', full_stats_text, re.IGNORECASE)
        if views_match:
            data['monthly_views'] = views_match.group(1).strip()
            print(f"  [ОК] Найдены просмотры: {data['monthly_views']}")
    except Exception:
        print("  [ИНФО] Элемент, содержащий 'monthly views', не найден.")
        
    # 3. ФИНАЛЬНЫЙ МЕТОД ПОИСКА ПИНОВ ЧЕРЕЗ ПЕРЕХВАТ ЗАПРОСА
    try:
        print(f"[ИНФО] Ожидаем API-запрос с данными доски '{target_board_name}'...")
        
        # Начинаем "прослушку" и одновременно выполняем действия, которые ее вызовут
        with page.expect_response("**/resource/BoardsResource/get/**", timeout=20000) as response_info:
            print("  [ДЕЙСТВИЕ] Скроллим страницу для инициации запроса...")
            # Прокручиваем страницу вниз, чтобы гарантированно вызвать запрос на получение данных о досках
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            page.wait_for_timeout(2000) # Небольшая пауза
        
        response = response_info.value
        if response.ok:
            print(f"  [УСПЕХ!] API-ответ перехвачен (Статус: {response.status}). Анализируем JSON...")
            response_json = response.json()
            
            if 'resource_response' in response_json and 'data' in response_json['resource_response']:
                boards_list = response_json['resource_response']['data']
                
                found_board = None
                for board in boards_list:
                    if board.get('name') == target_board_name:
                        found_board = board
                        break
                
                if found_board:
                    pin_count = found_board.get('pin_count')
                    if pin_count is not None:
                        data['pin_count'] = pin_count
                        print(f"  [ОК] Найдено точное количество пинов: {data['pin_count']}")
                    else:
                         print(f"  [ОШИБКА] Доска '{target_board_name}' найдена, но в ней нет ключа 'pin_count'.")
                else:
                    print(f"  [ОШИБКА] Доска с именем '{target_board_name}' не найдена в API-ответе.")
            else:
                print("  [ОШИБКА] Неверная структура JSON-ответа от API.")
        else:
            print(f"  [ОШИБКА] Перехваченный запрос завершился с ошибкой: {response.status}")
            
    except Exception as e:
        print(f"  [КРИТИЧЕСКАЯ ОШИБКА] Не удалось перехватить API-запрос с данными о досках. Ошибка: {e}")

    return data

# --- БЛОК ЗАПУСКА ПОТОКОВ (НЕМНОГО ИЗМЕНЕН) ---
def process_single_profile(profile_info):
    profile_id, profile_name, profile_url, target_board_name = profile_info
    
    print("\n" + "="*50)
    print(f"[РАБОТА] Поток для '{profile_name}' (ID: {profile_id}) запущен.")
    
    with sync_playwright() as p:
        storage_state = SESSION_FILE if os.path.exists(SESSION_FILE) else None
        browser = p.chromium.launch(headless=True, channel="chrome")
        context = browser.new_context(storage_state=storage_state)
        page = context.new_page()
        
        try:
            # Переходим только на страницу профиля
            page.goto(profile_url, timeout=60000, wait_until="domcontentloaded")
            page.locator('h1').wait_for(timeout=20000)
            print(f"[ИНФО] Страница профиля '{profile_name}' загружена.")
            
            # Вся магия происходит внутри этой функции
            parsed_data = get_profile_data(page, target_board_name)

            if any(v is not None for v in parsed_data.values()):
                db.save_daily_stat(profile_id, parsed_data)
            else:
                print(f"[ПРЕДУПРЕЖДЕНИЕ] Для '{profile_name}' не собрано данных.")

        except Exception as e:
            print(f"\n[КРИТИЧЕСКАЯ ОШИБКА] Профиль '{profile_name}': {e}")
        finally:
            page.close()
            context.close()
            browser.close()
            print(f"--- Поток для '{profile_name}' завершен. ---")

# --- ГЛАВНАЯ ФУНКЦИЯ (БЕЗ ИЗМЕНЕНИЙ) ---
def main():
    db.initialize_database()
    profiles_to_parse = db.get_active_profiles()
    if not profiles_to_parse:
        print("[ИНФО] В базе данных нет активных профилей для парсинга.")
        return
    print(f"[ИНФО] Найдено {len(profiles_to_parse)} профилей для обработки в {MAX_WORKERS} потоков.")

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        executor.map(process_single_profile, profiles_to_parse)

    print("\n[ИНФО] ВСЕ РАБОТЫ ЗАВЕРШЕНЫ.")

if __name__ == "__main__":
    main()