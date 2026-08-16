from services.ai_food_parser import FoodAnalysisStatus, parse_kbju_json


def test_parse_kbju_json_normalizes_synonym_keys():
    raw = """
    {
      "items": [
        {
          "name": "Яйца варёные",
          "amount_g": 100,
          "calories": 157,
          "proteins": 13,
          "fats": 11,
          "carbohydrates": 1.1
        }
      ],
      "total": {
        "ккал": 157,
        "белки": 13,
        "жиры": 11,
        "углеводы": 1.1
      }
    }
    """

    parsed = parse_kbju_json(raw)

    assert parsed is not None
    assert parsed["status"] == FoodAnalysisStatus.OK.value
    assert parsed["items"][0] == {
        "name": "Яйца варёные",
        "grams": 100,
        "kcal": 157,
        "protein": 13,
        "fat": 11,
        "carbs": 1.1,
    }
    assert parsed["total"] == {
        "kcal": 157,
        "protein": 13,
        "fat": 11,
        "carbs": 1.1,
    }


def test_parse_kbju_json_accepts_markdown_and_calculates_missing_total():
    parsed = parse_kbju_json(
        '```json\n{"items":[{"name":"Творог","grams":200,"kcal":240,'
        '"protein":32,"fat":10,"carbs":6}]}\n```'
    )

    assert parsed is not None
    assert parsed["status"] == FoodAnalysisStatus.OK.value
    assert parsed["total"] == {
        "kcal": 240,
        "protein": 32,
        "fat": 10,
        "carbs": 6,
    }


def test_parse_kbju_json_returns_none_for_plain_text_response():
    assert parse_kbju_json("Обычный текст без JSON.") is None


def test_parse_kbju_json_returns_explicit_no_food_result():
    parsed = parse_kbju_json('{"status":"no_food","items":[]}')

    assert parsed == {"status": FoodAnalysisStatus.NO_FOOD.value, "items": []}


def test_parse_kbju_json_treats_ok_with_empty_items_as_no_food():
    parsed = parse_kbju_json(
        '{"status":"ok","items":[],"total":{"kcal":100,"protein":1,"fat":1,"carbs":1}}'
    )

    assert parsed == {"status": FoodAnalysisStatus.NO_FOOD.value, "items": []}


def test_parse_kbju_json_rejects_unknown_status():
    assert parse_kbju_json('{"status":"maybe","items":[]}') is None


def test_parse_kbju_json_rejects_no_food_with_items():
    assert parse_kbju_json(
        '{"status":"no_food","items":[{"name":"Хлеб","grams":30}]}'
    ) is None


def test_parse_kbju_json_rejects_total_without_items():
    assert parse_kbju_json(
        '{"total":{"kcal":100,"protein":1,"fat":1,"carbs":1}}'
    ) is None


def test_parse_kbju_json_rejects_item_without_real_name_field():
    assert parse_kbju_json(
        '{"status":"ok","items":[{"grams":100,"kcal":0}],"total":{"kcal":0}}'
    ) is None
