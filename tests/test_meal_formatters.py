import importlib.util
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
        self.assertIn("🎯 <b>Цель: Не задана</b>", text)
        self.assertIn("<b>🔥 Калории: 1430/0 ккал (0%)</b>", text)
        self.assertIn("---", text)
        self.assertIn("<b>Итого завтрак:</b>", text)

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
        self.assertIn("• <b>Продукт</b>", text)
        self.assertIn("<b>100 ккал</b> <i>(Б 1.0 / Ж 2.0 / У 3.0)</i>", text)
        self.assertNotIn("None", text)


if __name__ == "__main__":
    unittest.main()
