"""Сервис для работы с API питания."""
import logging
import re
import requests
from typing import Optional, Tuple
from config import NUTRITION_API_KEY

logger = logging.getLogger(__name__)


class NutritionService:
    """Сервис для работы с CalorieNinjas API и Open Food Facts."""
    
    def __init__(self):
        if not NUTRITION_API_KEY:
            logger.warning("NUTRITION_API_KEY не задан. CalorieNinjas работать не будет.")
        self.api_key = NUTRITION_API_KEY
    
    def get_nutrition_from_api(self, query: str) -> Tuple[list, dict]:
        """
        Вызывает CalorieNinjas /v1/nutrition и возвращает (items, totals).
        
        Args:
            query: Текст запроса с описанием еды
            
        Returns:
            Tuple[list, dict]: (items, totals) где items — список продуктов,
                              totals — суммарные калории и БЖУ
        """
        if not self.api_key:
            raise RuntimeError("NUTRITION_API_KEY не задан в переменных окружения")
        
        url = "https://api.calorieninjas.com/v1/nutrition"
        headers = {"X-Api-Key": self.api_key}
        params = {"query": query}
        
        try:
            resp = requests.get(url, headers=headers, params=params, timeout=10)
        except Exception as e:
            logger.error(f"Ошибка сети при запросе к CalorieNinjas: {e}", exc_info=True)
            raise
        
        logger.debug(f"CalorieNinjas status: {resp.status_code}")
        logger.debug(f"CalorieNinjas raw response: {resp.text[:500]}")
        
        if resp.status_code != 200:
            logger.error(f"Ответ от CalorieNinjas (non-200): {resp.text[:500]}")
            raise RuntimeError(f"CalorieNinjas error: HTTP {resp.status_code}")
        
        try:
            data = resp.json()
        except Exception as e:
            logger.error(f"Не получилось распарсить JSON от CalorieNinjas: {resp.text[:500]}", exc_info=True)
            raise
        
        # формат: {"items": [ {...}, {...}, ... ]}
        if not isinstance(data, dict) or "items" not in data:
            logger.error(f"Неожиданный формат ответа от CalorieNinjas: {data}")
            raise RuntimeError("Unexpected response format from CalorieNinjas")
        
        items = data.get("items") or []
        
        def safe_float(v) -> float:
            try:
                if v is None:
                    return 0.0
                return float(v)
            except (TypeError, ValueError):
                return 0.0
        
        totals = {
            "calories": 0.0,
            "protein_g": 0.0,
            "fat_total_g": 0.0,
            "carbohydrates_total_g": 0.0,
        }
        
        for item in items:
            cal = safe_float(item.get("calories"))
            p = safe_float(item.get("protein_g"))
            f = safe_float(item.get("fat_total_g"))
            c = safe_float(item.get("carbohydrates_total_g"))
            
            # Кладём приведённые значения обратно для удобства
            item["_calories"] = cal
            item["_protein_g"] = p
            item["_fat_total_g"] = f
            item["_carbohydrates_total_g"] = c
            
            totals["calories"] += cal
            totals["protein_g"] += p
            totals["fat_total_g"] += f
            totals["carbohydrates_total_g"] += c
        
        return items, totals
    
    def get_product_from_openfoodfacts(self, barcode: str) -> Optional[dict]:
        """
        Получает информацию о продукте из Open Food Facts API по штрих-коду.
        
        Args:
            barcode: Штрих-код продукта
            
        Returns:
            dict с информацией о продукте или None при ошибке
        """
        url = f"https://world.openfoodfacts.org/api/v0/product/{barcode}.json"
        
        try:
            resp = requests.get(url, timeout=10)
            
            if resp.status_code != 200:
                logger.warning(f"Open Food Facts API error: HTTP {resp.status_code}")
                return None
            
            data = resp.json()
            
            if data.get("status") != 1:
                logger.info(f"Product not found in Open Food Facts: {barcode}")
                return None
            
            product = data.get("product", {})
            
            # Извлекаем основную информацию
            result = {
                "name": product.get("product_name") or product.get("product_name_ru") or product.get("product_name_en") or "Неизвестный продукт",
                "brand": product.get("brands") or "",
                "barcode": barcode,
                "nutriments": {}
            }
            
            # Извлекаем КБЖУ (на 100г)
            nutriments = product.get("nutriments", {})
            
            logger.debug(f"Open Food Facts barcode {barcode}, product: {result['name']}")
            
            def safe_float(value):
                if value is None:
                    return None
                try:
                    if isinstance(value, (int, float)):
                        return float(value)
                    if isinstance(value, str):
                        cleaned = value.strip().replace(',', '.')
                        return float(cleaned)
                    return None
                except (ValueError, TypeError):
                    return None
            
            # Калории
            kcal = None
            for key in ["energy-kcal_100g", "energy-kcal", "energy_100g", "energy-kcal_value", 
                        "energy-kcal_serving", "energy_serving", "energy"]:
                if key in nutriments:
                    kcal = safe_float(nutriments[key])
                    if kcal is not None and kcal > 0:
                        break
            
            # Конвертируем из кДж если нужно
            if not kcal or kcal <= 0:
                energy_kj = None
                for key in ["energy-kj_100g", "energy-kj", "energy-kj_value", "energy-kj_serving"]:
                    if key in nutriments:
                        energy_kj = safe_float(nutriments[key])
                        if energy_kj is not None and energy_kj > 0:
                            break
                
                if energy_kj and energy_kj > 0:
                    try:
                        kcal = energy_kj / 4.184
                    except (ValueError, TypeError):
                        pass
            
            if kcal and kcal > 0:
                result["nutriments"]["kcal"] = kcal
            
            # Белки
            protein = None
            for key in ["proteins_100g", "proteins", "protein_100g", "protein", 
                        "proteins_value", "proteins_serving", "protein_serving"]:
                if key in nutriments:
                    protein = safe_float(nutriments[key])
                    if protein is not None and protein >= 0:
                        break
            
            if protein is not None and protein >= 0:
                result["nutriments"]["protein"] = protein
            
            # Жиры
            fat = None
            for key in ["fat_100g", "fat", "fats_100g", "fats", 
                        "fat_value", "fat_serving", "fats_serving"]:
                if key in nutriments:
                    fat = safe_float(nutriments[key])
                    if fat is not None and fat >= 0:
                        break
            
            if fat is not None and fat >= 0:
                result["nutriments"]["fat"] = fat
            
            # Углеводы
            carbs = None
            for key in ["carbohydrates_100g", "carbohydrates", "carbohydrate_100g", "carbohydrate",
                        "carbohydrates_value", "carbohydrates_serving", "carbohydrate_serving", "carbs_100g", "carbs"]:
                if key in nutriments:
                    carbs = safe_float(nutriments[key])
                    if carbs is not None and carbs >= 0:
                        break
            
            if carbs is not None and carbs >= 0:
                result["nutriments"]["carbs"] = carbs
            
            # Вес продукта
            weight = product.get("quantity") or product.get("product_quantity") or product.get("net_weight") or product.get("weight")
            if weight:
                weight_match = re.search(r'(\d+)', str(weight))
                if weight_match:
                    result["weight"] = int(weight_match.group(1))
            
            # Дополнительная информация
            result["ingredients"] = product.get("ingredients_text") or product.get("ingredients_text_ru") or product.get("ingredients_text_en") or ""
            result["categories"] = product.get("categories") or ""
            result["image_url"] = product.get("image_url") or product.get("image_front_url") or ""
            
            return result
            
        except Exception as e:
            logger.error(f"Ошибка при запросе к Open Food Facts: {e}", exc_info=True)
            return None


# Глобальный экземпляр сервиса
nutrition_service = NutritionService()

