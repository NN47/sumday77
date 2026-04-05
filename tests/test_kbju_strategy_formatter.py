import importlib.util
import sys
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "utils" / "formatters.py"
spec = importlib.util.spec_from_file_location("formatters", MODULE_PATH)
module = importlib.util.module_from_spec(spec)
assert spec and spec.loader
sys.modules[spec.name] = module
spec.loader.exec_module(module)
format_strategy_text = module.format_strategy_text


class StrategyFormatterTests(unittest.TestCase):
    def test_loss_strategy_text(self):
        text = format_strategy_text(1800, 120, 60, 180, "loss")
        self.assertIn("мягкий дефицит", text)
        self.assertIn("терять вес постепенно и комфортно", text)

    def test_maintain_strategy_text(self):
        text = format_strategy_text(2200, 130, 70, 250, "maintain")
        self.assertIn("поддержание веса", text)
        self.assertIn("без дефицита и профицита", text)

    def test_gain_strategy_text(self):
        text = format_strategy_text(2800, 150, 80, 340, "gain")
        self.assertIn("умеренный профицит", text)
        self.assertIn("сохранять энергию для тренировок", text)


if __name__ == "__main__":
    unittest.main()
