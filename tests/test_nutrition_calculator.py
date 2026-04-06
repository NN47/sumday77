import importlib.util
import sys
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "services" / "nutrition_calculator.py"
spec = importlib.util.spec_from_file_location("nutrition_calculator", MODULE_PATH)
module = importlib.util.module_from_spec(spec)
assert spec and spec.loader
sys.modules[spec.name] = module
spec.loader.exec_module(module)
calculate_nutrition_profile = module.calculate_nutrition_profile
calculate_bmr = module.calculate_bmr
calculate_tdee = module.calculate_tdee
apply_goal = module.apply_goal
get_activity_multiplier = module.get_activity_multiplier


class NutritionCalculatorTests(unittest.TestCase):
    def test_bmr_formula_for_male(self):
        bmr = calculate_bmr(gender="male", age=30, height=180, weight=80)
        self.assertAlmostEqual(bmr, 1780.0)

    def test_bmr_formula_for_female(self):
        bmr = calculate_bmr(gender="female", age=30, height=180, weight=80)
        self.assertAlmostEqual(bmr, 1614.0)

    def test_goal_applies_after_tdee(self):
        bmr = calculate_bmr(gender="male", age=28, height=171, weight=78)
        tdee = calculate_tdee(bmr=bmr, activity_multiplier=1.55)
        target = apply_goal(tdee=tdee, goal="loss", goal_percent=15)

        self.assertAlmostEqual(target, tdee * 0.85)

    def test_goal_applies_custom_percent_for_loss(self):
        bmr = calculate_bmr(gender="male", age=28, height=171, weight=78)
        tdee = calculate_tdee(bmr=bmr, activity_multiplier=1.55)
        target = apply_goal(tdee=tdee, goal="loss", goal_percent=10)

        self.assertAlmostEqual(target, tdee * 0.90)

    def test_goal_applies_custom_percent_for_gain(self):
        bmr = calculate_bmr(gender="female", age=30, height=165, weight=62)
        tdee = calculate_tdee(bmr=bmr, activity_multiplier=1.55)
        target = apply_goal(tdee=tdee, goal="gain", goal_percent=20)

        self.assertAlmostEqual(target, tdee * 1.20)

    def test_profile_for_male_loss_has_valid_order(self):
        profile = calculate_nutrition_profile(
            {
                "gender": "male",
                "age": 28,
                "height": 171,
                "weight": 78,
                "activity": "medium",
                "goal": "loss",
                "goal_percent": 10,
            }
        )

        self.assertLess(profile.target_calories, profile.tdee)
        self.assertGreater(profile.tdee, profile.bmr)
        self.assertEqual(profile.goal_explanation, "Для похудения: −10%")


    def test_activity_multipliers_match_onboarding_levels(self):
        self.assertEqual(get_activity_multiplier("sedentary"), 1.2)
        self.assertEqual(get_activity_multiplier("light"), 1.375)
        self.assertEqual(get_activity_multiplier("moderate"), 1.45)
        self.assertEqual(get_activity_multiplier("active"), 1.6)

    def test_tdee_grows_with_activity(self):
        base_data = {
            "gender": "female",
            "age": 31,
            "height": 168,
            "weight": 64,
            "goal": "maintain",
        }
        low = calculate_nutrition_profile({**base_data, "activity": "low"})
        high = calculate_nutrition_profile({**base_data, "activity": "high"})

        self.assertGreater(high.tdee, low.tdee)

    def test_loss_has_lower_calories_than_maintain(self):
        base_data = {
            "gender": "male",
            "age": 35,
            "height": 180,
            "weight": 85,
            "activity": "medium",
        }
        maintain = calculate_nutrition_profile({**base_data, "goal": "maintain"})
        loss = calculate_nutrition_profile({**base_data, "goal": "loss"})

        self.assertLess(loss.target_calories, maintain.tdee)

    def test_gain_has_higher_calories_than_maintain(self):
        base_data = {
            "gender": "male",
            "age": 35,
            "height": 180,
            "weight": 85,
            "activity": "medium",
        }
        maintain = calculate_nutrition_profile({**base_data, "goal": "maintain"})
        gain = calculate_nutrition_profile({**base_data, "goal": "gain"})

        self.assertGreater(gain.target_calories, maintain.tdee)

    def test_maintain_ignores_goal_percent(self):
        base_data = {
            "gender": "male",
            "age": 35,
            "height": 180,
            "weight": 85,
            "activity": "medium",
            "goal": "maintain",
            "goal_percent": 20,
        }
        maintain = calculate_nutrition_profile(base_data)

        self.assertEqual(maintain.target_calories, maintain.tdee)
        self.assertEqual(maintain.goal_explanation, "Для поддержания: без изменений")

    def test_macros_are_not_negative(self):
        profile = calculate_nutrition_profile(
            {
                "gender": "female",
                "age": 45,
                "height": 158,
                "weight": 50,
                "activity": "minimal",
                "goal": "loss",
            }
        )

        self.assertGreaterEqual(profile.proteins, 0)
        self.assertGreaterEqual(profile.fats, 0)
        self.assertGreaterEqual(profile.carbs, 0)


if __name__ == "__main__":
    unittest.main()
