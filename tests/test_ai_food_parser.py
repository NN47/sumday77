from services.ai_food_parser import parse_kbju_json


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
    assert parsed["total"] == {
        "kcal": 240,
        "protein": 32,
        "fat": 10,
        "carbs": 6,
    }


def test_parse_kbju_json_returns_none_for_plain_text_response():
    assert parse_kbju_json("Обычный текст без JSON.") is None
