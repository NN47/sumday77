import importlib.util
import sys
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "utils" / "meal_types.py"
ROOT_PATH = MODULE_PATH.parents[1]
if str(ROOT_PATH) not in sys.path:
    sys.path.insert(0, str(ROOT_PATH))
spec = importlib.util.spec_from_file_location("meal_types", MODULE_PATH)
module = importlib.util.module_from_spec(spec)
assert spec and spec.loader
sys.modules[spec.name] = module
spec.loader.exec_module(module)

display_meal_type_with_bold_name = module.display_meal_type_with_bold_name


class MealTypeDisplayTests(unittest.TestCase):
    def test_bolds_textual_meal_name_for_each_supported_meal_type(self):
        self.assertEqual(display_meal_type_with_bold_name("breakfast"), "🍳 <b>Завтрак</b>")
        self.assertEqual(display_meal_type_with_bold_name("lunch"), "🍲 <b>Обед</b>")
        self.assertEqual(display_meal_type_with_bold_name("dinner"), "🍽 <b>Ужин</b>")
        self.assertEqual(display_meal_type_with_bold_name("snack"), "🍎 <b>Перекус</b>")

    def test_unknown_meal_type_falls_back_to_bold_snack_name(self):
        self.assertEqual(display_meal_type_with_bold_name("unexpected"), "🍎 <b>Перекус</b>")


if __name__ == "__main__":
    unittest.main()
