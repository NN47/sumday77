"""Сервис для работы с Gemini API."""
import json
import logging
from typing import Optional
from google import genai
from google.genai import errors as genai_errors
from config import GEMINI_API_KEY, GEMINI_API_KEY2, GEMINI_API_KEY3
from database.repositories import GeminiRepository

logger = logging.getLogger(__name__)


class GeminiService:
    """Сервис для работы с Gemini API с поддержкой fallback ключей."""
    
    def __init__(self):
        if not GEMINI_API_KEY:
            raise RuntimeError("GEMINI_API_KEY не задан в конфигурации")
        
        # Список ключей для переключения (имена стабильны для БД)
        self.account_configs = [
            {"account_name": "GEMINI_API_KEY", "api_key": GEMINI_API_KEY, "priority_order": 1}
        ]
        if GEMINI_API_KEY2:
            self.account_configs.append(
                {"account_name": "GEMINI_API_KEY2", "api_key": GEMINI_API_KEY2, "priority_order": 2}
            )
            logger.info("✅ Резервный ключ Gemini API (GEMINI_API_KEY2) найден")
        if GEMINI_API_KEY3:
            self.account_configs.append(
                {"account_name": "GEMINI_API_KEY3", "api_key": GEMINI_API_KEY3, "priority_order": 3}
            )
            logger.info("✅ Третий резервный ключ Gemini API (GEMINI_API_KEY3) найден")

        self.api_key_by_account_name = {cfg["account_name"]: cfg["api_key"] for cfg in self.account_configs}
        self._accounts_synced = False

        self.model = "gemini-2.5-flash"
        self.client = genai.Client(api_key=self.account_configs[0]["api_key"])

    def _ensure_accounts_synced(self):
        if self._accounts_synced:
            return
        GeminiRepository.sync_accounts(self.account_configs)
        self._accounts_synced = True

    def _build_client_for_active_account(self):
        self._ensure_accounts_synced()
        active_account = GeminiRepository.get_active_account()
        if not active_account:
            GeminiRepository.sync_accounts(self.account_configs)
            active_account = GeminiRepository.get_active_account()
        if not active_account:
            raise RuntimeError("Не удалось определить активный Gemini-аккаунт")
        api_key = self.api_key_by_account_name.get(active_account.account_name)
        if not api_key:
            raise RuntimeError(f"API ключ для аккаунта {active_account.account_name} не найден в окружении")
        return genai.Client(api_key=api_key)
    
    def _is_quota_error(self, error: Exception) -> bool:
        """Проверяет, является ли ошибка ошибкой квоты/лимита."""
        error_str = str(error).lower()
        error_type = type(error).__name__
        
        # Проверяем различные типы ошибок квоты
        quota_indicators = [
            "quota",
            "rate limit",
            "429",
            "resource exhausted",
            "too many requests",
            "billing",
            "permission denied",
            "forbidden",
            "403",
        ]
        
        return any(indicator in error_str for indicator in quota_indicators) or \
               error_type in ["ResourceExhausted", "RateLimitError", "QuotaExceeded"]
    
    def _switch_to_next_account(self, current_account_id: int) -> bool:
        """Переключается на следующий аккаунт и сохраняет активный в БД."""
        if len(self.account_configs) <= 1:
            logger.warning("⚠️ Нет резервных Gemini-аккаунтов для переключения")
            return False
        next_account = GeminiRepository.switch_to_next_account(current_account_id)
        if not next_account:
            return False
        self.client = self._build_client_for_active_account()
        logger.warning("🔄 Переключился на резервный Gemini-аккаунт: %s", next_account.account_name)
        return True
    
    def _make_request(self, func, *args, **kwargs):
        """Выполняет запрос с автоматическим переключением ключей при ошибках квоты."""
        max_attempts = len(self.account_configs)
        last_error = None
        
        for attempt in range(max_attempts):
            self._ensure_accounts_synced()
            active_account = GeminiRepository.get_active_account()
            if not active_account:
                raise RuntimeError("Нет активного Gemini-аккаунта в БД")
            self.client = self._build_client_for_active_account()
            try:
                response = func(*args, **kwargs)
                GeminiRepository.increment_account_stats(
                    active_account.id,
                    status="success",
                    model_name=self.model,
                )
                return response
            except Exception as e:
                last_error = e
                is_quota = self._is_quota_error(e)
                status = "limit_exceeded" if is_quota else "error"
                GeminiRepository.increment_account_stats(
                    active_account.id,
                    status=status,
                    model_name=self.model,
                    error_message=str(e),
                )
                
                # Если это ошибка квоты и есть резервные ключи
                if is_quota and len(self.account_configs) > 1:
                    logger.warning(f"⚠️ Ошибка квоты на аккаунте {active_account.account_name}: {e}")
                    
                    # Переключаемся на следующий аккаунт
                    if self._switch_to_next_account(active_account.id):
                        continue  # Пробуем снова с новым ключом
                
                # Если это не ошибка квоты или нет резервных ключей - пробрасываем ошибку
                raise
        
        # Если все попытки исчерпаны
        raise last_error
    
    def analyze(self, text: str) -> str:
        """Анализирует текст через Gemini."""
        try:
            response = self._make_request(
                lambda **request_kwargs: self.client.models.generate_content(**request_kwargs),
                model=self.model,
                contents=text
            )
            return response.text
        except genai_errors.ServerError as e:
            logger.warning(f"Gemini временно недоступен (ServerError): {e}")
            raise
        except Exception as e:
            if "503" in str(e):
                logger.warning(f"Gemini временно недоступен (503): {e}")
                raise
            logger.exception("Ошибка Gemini при анализе")
            raise
    
    def estimate_kbju(self, food_text: str) -> Optional[dict]:
        """
        Оценивает КБЖУ через Gemini по текстовому описанию.
        
        Возвращает dict вида:
        {
          "items": [
            {"name": "курица", "grams": 100, "kcal": 165, "protein": 31, "fat": 4, "carbs": 0}
          ],
          "total": {"kcal": 165, "protein": 31, "fat": 4, "carbs": 0}
        }
        или None при ошибке.
        """
        prompt = f"""
Ты нутрициолог. Твоя задача — ОЦЕНИТЬ калории, белки, жиры и углеводы для списка продуктов.

Пользователь вводит на русском, например:
"200 г курицы, 100 г йогурта, 30 г орехов".

Требования:

1. Если вес не указан, оцени примерный (но лучше всегда использовать граммы из запроса).
2. Используй типичные значения для обычных продуктов (не бренд-специфично).
3. Ответь СТРОГО в формате JSON, БЕЗ объяснений, комментариев и оформления.

ФОРМАТ ОТВЕТА (пример):
{{
  "items": [
    {{
      "name": "курица",
      "grams": 200,
      "kcal": 330,
      "protein": 40,
      "fat": 15,
      "carbs": 0
    }},
    {{
      "name": "йогурт",
      "grams": 100,
      "kcal": 60,
      "protein": 5,
      "fat": 2,
      "carbs": 7
    }}
  ],
  "total": {{
    "kcal": 390,
    "protein": 45,
    "fat": 17,
    "carbs": 7
  }}
}}

Вот данные пользователя: "{food_text}"
"""
        try:
            response = self._make_request(
                lambda **request_kwargs: self.client.models.generate_content(**request_kwargs),
                model=self.model,
                contents=prompt,
            )
            raw = response.text.strip()
            logger.debug(f"Gemini raw KBJU response: {raw[:200]}...")
            
            # Парсим JSON
            try:
                parsed = json.loads(raw)
                return self._normalize_kbju_payload(parsed)
            except json.JSONDecodeError:
                # Если Gemini добавил лишний текст — вырежем JSON
                start = raw.find("{")
                end = raw.rfind("}")
                if start != -1 and end != -1 and end > start:
                    snippet = raw[start : end + 1]
                    parsed = json.loads(snippet)
                    return self._normalize_kbju_payload(parsed)
                raise
        except Exception as e:
            logger.error(f"Ошибка Gemini (КБЖУ): {e}", exc_info=True)
            return None

    def _normalize_kbju_payload(self, payload: dict) -> Optional[dict]:
        """Нормализует ответ Gemini к единому формату с total и items."""
        if not isinstance(payload, dict):
            return None

        def safe_float(value) -> float:
            try:
                if value is None:
                    return 0.0
                return float(value)
            except (TypeError, ValueError):
                return 0.0

        def get_num(source: dict, *keys: str) -> float:
            for key in keys:
                if key in source:
                    return safe_float(source.get(key))
            return 0.0

        raw_items = payload.get("items")
        items = raw_items if isinstance(raw_items, list) else []
        normalized_items = []
        for item in items:
            if not isinstance(item, dict):
                continue
            normalized_items.append(
                {
                    "name": item.get("name") or item.get("title") or "продукт",
                    "grams": get_num(item, "grams", "weight_g", "weight"),
                    "kcal": get_num(item, "kcal", "calories"),
                    "protein": get_num(item, "protein", "protein_g"),
                    "fat": get_num(item, "fat", "fat_g"),
                    "carbs": get_num(item, "carbs", "carbohydrates", "carbohydrates_g"),
                }
            )

        raw_total = payload.get("total")
        total_dict = raw_total if isinstance(raw_total, dict) else {}
        total = {
            "kcal": get_num(total_dict, "kcal", "calories"),
            "protein": get_num(total_dict, "protein", "protein_g"),
            "fat": get_num(total_dict, "fat", "fat_g"),
            "carbs": get_num(total_dict, "carbs", "carbohydrates", "carbohydrates_g"),
        }

        # Если total не пришел или пустой — считаем из items
        if not any(total.values()) and normalized_items:
            total = {
                "kcal": sum(i["kcal"] for i in normalized_items),
                "protein": sum(i["protein"] for i in normalized_items),
                "fat": sum(i["fat"] for i in normalized_items),
                "carbs": sum(i["carbs"] for i in normalized_items),
            }

        if not normalized_items and not any(total.values()):
            return None

        return {"items": normalized_items, "total": total}
    
    def estimate_kbju_from_photo(self, image_bytes: bytes) -> Optional[dict]:
        """
        Оценивает КБЖУ через Gemini Vision API по фото еды.
        
        Возвращает dict вида:
        {
          "items": [
            {"name": "курица", "grams": 100, "kcal": 165, "protein": 31, "fat": 4, "carbs": 0}
          ],
          "total": {"kcal": 165, "protein": 31, "fat": 4, "carbs": 0}
        }
        или None при ошибке.
        """
        prompt = """
Ты нутрициолог. Твоя задача — ОЦЕНИТЬ калории, белки, жиры и углеводы для еды на фотографии.

Проанализируй изображение и определи:
1. Какие продукты/блюда видны на фото
2. Примерный вес каждого продукта (в граммах)
3. КБЖУ для каждого продукта

Требования:
1. Оценивай вес продуктов визуально, исходя из типичных размеров порций
2. Используй типичные значения КБЖУ для обычных продуктов (не бренд-специфично)
3. Ответь СТРОГО в формате JSON, БЕЗ объяснений, комментариев и оформления

ФОРМАТ ОТВЕТА (пример):
{
  "items": [
    {
      "name": "курица",
      "grams": 200,
      "kcal": 330,
      "protein": 40,
      "fat": 15,
      "carbs": 0
    },
    {
      "name": "рис",
      "grams": 150,
      "kcal": 195,
      "protein": 4,
      "fat": 1,
      "carbs": 42
    }
  ],
  "total": {
    "kcal": 525,
    "protein": 44,
    "fat": 16,
    "carbs": 42
  }
}
"""
        try:
            from google.genai import types
            
            # Определяем MIME тип
            mime_type = "image/jpeg"
            if image_bytes.startswith(b'\x89PNG'):
                mime_type = "image/png"
            elif image_bytes.startswith(b'GIF'):
                mime_type = "image/gif"
            elif image_bytes.startswith(b'WEBP'):
                mime_type = "image/webp"
            
            response = self._make_request(
                lambda **request_kwargs: self.client.models.generate_content(**request_kwargs),
                model=self.model,
                contents=[
                    types.Part.from_bytes(
                        data=image_bytes,
                        mime_type=mime_type
                    ),
                    prompt
                ]
            )
            
            raw = response.text.strip()
            logger.debug(f"Gemini raw KBJU response from photo: {raw[:200]}...")
            
            # Парсим JSON
            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                start = raw.find("{")
                end = raw.rfind("}")
                if start != -1 and end != -1 and end > start:
                    snippet = raw[start : end + 1]
                    return json.loads(snippet)
                raise
        except Exception as e:
            logger.error(f"Ошибка Gemini (КБЖУ по фото): {e}", exc_info=True)
            return None
    
    def extract_kbju_from_label(self, image_bytes: bytes) -> Optional[dict]:
        """
        Извлекает КБЖУ из текста на этикетке/упаковке через Gemini Vision API.
        
        Возвращает dict вида:
        {
          "product_name": "название продукта",
          "kbju_per_100g": {
            "kcal": 200,
            "protein": 10,
            "fat": 5,
            "carbs": 30
          },
          "package_weight": 50,
          "found_weight": true
        }
        или None при ошибке.
        """
        prompt = """
Ты анализируешь фото этикетки или упаковки продукта. Твоя задача — найти в тексте информацию о КБЖУ (калориях, белках, жирах, углеводах).

ВАЖНО:
1. Прочитай весь текст на этикетке/упаковке
2. Найди таблицу пищевой ценности или информацию о КБЖУ
3. Обычно КБЖУ указывается на 100 грамм продукта
4. Также попробуй найти вес упаковки/порции (может быть указан как "масса нетто", "вес", "порция" и т.д.)

Ответь СТРОГО в формате JSON, БЕЗ объяснений, комментариев и оформления:

{
  "product_name": "название продукта (если видно)",
  "kbju_per_100g": {
    "kcal": число_калорий_на_100г,
    "protein": число_белков_на_100г,
    "fat": число_жиров_на_100г,
    "carbs": число_углеводов_на_100г
  },
  "package_weight": число_грамм_упаковки_или_null,
  "found_weight": true_если_найден_вес_иначе_false
}

Если не нашёл КБЖУ в тексте, верни null для всех значений.
Если нашёл КБЖУ, но не нашёл вес упаковки, установи "package_weight": null и "found_weight": false.
"""
        try:
            from google.genai import types
            
            mime_type = "image/jpeg"
            if image_bytes.startswith(b'\x89PNG'):
                mime_type = "image/png"
            elif image_bytes.startswith(b'GIF'):
                mime_type = "image/gif"
            elif image_bytes.startswith(b'WEBP'):
                mime_type = "image/webp"
            
            response = self._make_request(
                lambda **request_kwargs: self.client.models.generate_content(**request_kwargs),
                model=self.model,
                contents=[
                    types.Part.from_bytes(
                        data=image_bytes,
                        mime_type=mime_type
                    ),
                    prompt
                ]
            )
            
            raw = response.text.strip()
            logger.debug(f"Gemini raw label KBJU response: {raw[:200]}...")
            
            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                start = raw.find("{")
                end = raw.rfind("}")
                if start != -1 and end != -1 and end > start:
                    snippet = raw[start : end + 1]
                    return json.loads(snippet)
                raise
        except Exception as e:
            logger.error(f"Ошибка Gemini (КБЖУ с этикетки): {e}", exc_info=True)
            return None
    
    def scan_barcode(self, image_bytes: bytes) -> Optional[str]:
        """
        Распознаёт штрих-код на фото через Gemini Vision API.
        
        Возвращает строку с номером штрих-кода (EAN-13, UPC и т.д.) или None при ошибке.
        """
        prompt = """
Ты видишь фото со штрих-кодом. Твоя задача — прочитать номер штрих-кода.

ВАЖНО:
1. Найди штрих-код на изображении (обычно это вертикальные полоски с цифрами под ними)
2. Прочитай все цифры, которые видны под штрих-кодом
3. Верни ТОЛЬКО номер штрих-кода (цифры), БЕЗ пробелов, дефисов и других символов
4. Если штрих-код не виден или нечитаем, верни "NOT_FOUND"

Примеры правильных ответов:
- 4607025392134
- 3017620422003
- 5449000000996

Ответь ТОЛЬКО номером штрих-кода, без дополнительных объяснений.
"""
        try:
            from google.genai import types
            
            mime_type = "image/jpeg"
            if image_bytes.startswith(b'\x89PNG'):
                mime_type = "image/png"
            elif image_bytes.startswith(b'GIF'):
                mime_type = "image/gif"
            elif image_bytes.startswith(b'WEBP'):
                mime_type = "image/webp"
            
            response = self._make_request(
                lambda **request_kwargs: self.client.models.generate_content(**request_kwargs),
                model=self.model,
                contents=[
                    types.Part.from_bytes(
                        data=image_bytes,
                        mime_type=mime_type
                    ),
                    prompt
                ]
            )
            
            raw = response.text.strip()
            logger.debug(f"Gemini raw barcode response: {raw}")
            
            # Очищаем ответ от лишних символов
            barcode = raw.replace(" ", "").replace("-", "").replace("_", "")
            
            # Проверяем, что это похоже на штрих-код (обычно 8-13 цифр)
            if barcode.isdigit() and 8 <= len(barcode) <= 14:
                return barcode
            elif barcode.upper() == "NOT_FOUND":
                return None
            else:
                # Пробуем извлечь только цифры
                digits = ''.join(filter(str.isdigit, barcode))
                if 8 <= len(digits) <= 14:
                    return digits
                return None
        except Exception as e:
            logger.error(f"Ошибка Gemini (распознавание штрих-кода): {e}", exc_info=True)
            return None


# Глобальный экземпляр сервиса
gemini_service = GeminiService()
