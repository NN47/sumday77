import importlib.util
import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace


MODULE_PATH = Path(__file__).resolve().parents[1] / "utils" / "meal_formatters.py"
ROOT_PATH = MODULE_PATH.parents[1]
if str(ROOT_PATH) not in sys.path:
    sys.path.insert(0, str(ROOT_PATH))
spec = importlib.util.spec_from_file_location("meal_formatters", MODULE_PATH)
module = importlib.util.module_from_spec(spec)
assert spec and spec.loader
sys.modules[spec.name] = module
spec.loader.exec_module(module)
format_today_meals = module.format_today_meals
format_meal_edit_details = module.format_meal_edit_details
split_telegram_text = module.split_telegram_text


class MealFormatterTests(unittest.TestCase):
    def test_groups_meals_by_type_and_hides_empty_blocks(self):
        meals = [
            SimpleNamespace(
                meal_type="lunch",
                products_json='[{"name":"Лазанья","grams":300,"kcal":870,"protein":45,"fat":42,"carbs":81}]',
                raw_query="",
                description="",
                calories=870,
                protein=45,
                fat=42,
                carbs=81,
            ),
            SimpleNamespace(
                meal_type="breakfast",
                products_json='[{"name":"Круассан","grams":200,"kcal":560,"protein":19,"fat":38,"carbs":33}]',
                raw_query="",
                description="",
                calories=560,
                protein=19,
                fat=38,
                carbs=33,
            ),
        ]
        text = format_today_meals(
            meals=meals,
            daily_totals={"calories": 1430, "protein": 64, "fat": 80, "carbs": 114},
            day_str="08.04.2026",
        )
        self.assertLess(text.find("🍳 <b>Завтрак"), text.find("🍲 <b>Обед"))
        self.assertNotIn("🍽 Ужин", text)
        self.assertIn("🍱 Дневник питания — 08.04.2026", text)
        self.assertIn("🎯 <b>Цель:</b> Не задана", text)
        self.assertIn("<b>🔥 Калории:</b> 1430/0 ккал (0%)", text)
        self.assertIn("🍳 <b>Завтрак — 560 ккал</b>", text)
        self.assertIn("Круассан", text)
        self.assertIn("<i>Б 19.0 / Ж 38.0 / У 33.0</i>", text)
        self.assertNotIn("200 г", text)
        self.assertNotIn("<b>Итого завтрак:</b>", text)

    def test_fallback_name_replaces_none(self):
        meals = [
            SimpleNamespace(
                meal_type="snack",
                products_json='[{"name":"None","kcal":100,"protein":1,"fat":2,"carbs":3}]',
                raw_query="",
                description="",
                calories=100,
                protein=1,
                fat=2,
                carbs=3,
            ),
        ]
        text = format_today_meals(
            meals=meals,
            daily_totals={"calories": 100, "protein": 1, "fat": 2, "carbs": 3},
            day_str="08.04.2026",
        )
        self.assertIn("Продукт", text)
        self.assertIn("🍎 <b>Перекус — 100 ккал</b>", text)
        self.assertIn("<i>Б 1.0 / Ж 2.0 / У 3.0</i>", text)
        self.assertNotIn("None", text)

    def test_duplicate_product_is_compact_once_but_edit_details_keep_both(self):
        meals = [
            SimpleNamespace(
                meal_type="lunch",
                products_json=(
                    '[{"name":"Хлебцы","grams":10,"kcal":33,"protein":0.7,"fat":0.1,"carbs":7},'
                    '{"name":"хлебцы","grams":20,"kcal":66,"protein":1.3,"fat":0.2,"carbs":14}]'
                ),
                raw_query="",
                description="",
                calories=99,
                protein=2,
                fat=0.3,
                carbs=21,
            )
        ]
        summary = format_today_meals(
            meals=meals,
            daily_totals={"calories": 99, "protein": 2, "fat": 0.3, "carbs": 21},
            day_str="16.08.2026",
        )
        products = json.loads(meals[0].products_json)
        details = format_meal_edit_details(
            "lunch",
            products,
            totals={"calories": 99, "protein": 2, "fat": 0.3, "carbs": 21},
        )

        self.assertEqual(summary.casefold().count("хлебцы"), 1)
        self.assertIn("1️⃣ Хлебцы — 10 г", details)
        self.assertIn("2️⃣ хлебцы — 20 г", details)
        self.assertIn("Итого:\n99 ккал · Б 2.0 / Ж 0.3 / У 21.0", details)

    def test_long_name_is_bounded_in_summary_and_full_in_edit_details(self):
        long_name = "Очень длинное название продукта " * 12
        meal = SimpleNamespace(
            meal_type="dinner",
            products_json=json.dumps(
                [{"name": long_name, "grams": 100, "kcal": 120, "protein": 3, "fat": 4, "carbs": 5}],
                ensure_ascii=False,
            ),
            raw_query="",
            description="",
            calories=120,
            protein=3,
            fat=4,
            carbs=5,
        )

        summary = format_today_meals([meal], {"calories": 120}, "16.08.2026")
        details = format_meal_edit_details("dinner", json.loads(meal.products_json))

        self.assertIn("…", summary)
        self.assertNotIn(long_name.strip(), summary)
        self.assertIn(long_name.strip(), details)

    def test_large_edit_details_split_without_data_loss(self):
        products = [
            {"name": f"Продукт {index} " + ("длинное название " * 8), "grams": index, "kcal": index}
            for index in range(1, 81)
        ]
        details = format_meal_edit_details("lunch", products)
        chunks = split_telegram_text(details)

        self.assertGreater(len(chunks), 1)
        self.assertEqual("".join(chunks), details)
        self.assertTrue(all(len(chunk.encode("utf-16-le")) // 2 <= 4000 for chunk in chunks))


if __name__ == "__main__":
    unittest.main()
