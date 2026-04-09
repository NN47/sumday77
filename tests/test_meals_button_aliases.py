import importlib.util
import sys
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "utils" / "keyboards.py"
ROOT_PATH = MODULE_PATH.parents[1]
if str(ROOT_PATH) not in sys.path:
    sys.path.insert(0, str(ROOT_PATH))
spec = importlib.util.spec_from_file_location("keyboards", MODULE_PATH)
module = importlib.util.module_from_spec(spec)
assert spec and spec.loader
sys.modules[spec.name] = module
spec.loader.exec_module(module)


class MealsButtonAliasesTests(unittest.TestCase):
    def test_meals_button_aliases_include_plain_text_variants(self):
        aliases = module.MEALS_BUTTON_ALIASES
        self.assertIn("🍱 Дневник питания", aliases)
        self.assertIn("🍱 КБЖУ", aliases)
        self.assertIn("Дневник питания", aliases)
        self.assertIn("КБЖУ", aliases)


if __name__ == "__main__":
    unittest.main()
