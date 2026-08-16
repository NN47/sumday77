import json

import pytest

from services.ai_food_parser import (
    MAX_FOOD_ITEMS,
    MAX_ITEM_CALORIES,
    MAX_ITEM_MACRO_G,
    MAX_ITEM_NAME_LENGTH,
    MAX_ITEM_WEIGHT_G,
    FoodAnalysisParseError,
    FoodAnalysisStatus,
    parse_ai_json,
    parse_kbju_json,
)


def _valid_item(**overrides):
    item = {
        "name": "Банан",
        "grams": 120,
        "kcal": 107,
        "protein": 1.3,
        "fat": 0.4,
        "carbs": 27,
    }
    item.update(overrides)
    return item


def _ok_payload(items, **extra):
    payload = {
        "status": "ok",
        "items": items,
        "total": {"kcal": 999, "protein": 999, "fat": 999, "carbs": 999},
    }
    payload.update(extra)
    return json.dumps(payload, ensure_ascii=False)


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


def test_parse_kbju_json_returns_none_for_non_string_provider_response():
    assert parse_kbju_json({"status": "ok", "items": []}) is None


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


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("grams", -100),
        ("kcal", -100),
        ("protein", -5),
        ("fat", -1),
        ("carbs", -20),
    ],
)
def test_parse_kbju_json_rejects_negative_item_numbers(field, value):
    assert parse_kbju_json(_ok_payload([_valid_item(**{field: value})])) is None


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
@pytest.mark.parametrize("field", ["grams", "kcal", "protein", "fat", "carbs"])
def test_parse_kbju_json_rejects_non_finite_item_numbers(field, value):
    assert parse_kbju_json(_ok_payload([_valid_item(**{field: value})])) is None


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("grams", MAX_ITEM_WEIGHT_G + 1),
        ("kcal", MAX_ITEM_CALORIES + 1),
        ("protein", MAX_ITEM_MACRO_G + 1),
        ("fat", MAX_ITEM_MACRO_G + 1),
        ("carbs", MAX_ITEM_MACRO_G + 1),
    ],
)
def test_parse_kbju_json_rejects_numbers_above_safety_limits(field, value):
    assert parse_kbju_json(_ok_payload([_valid_item(**{field: value})])) is None


@pytest.mark.parametrize("value", ["120", None, True])
def test_parse_kbju_json_rejects_non_numeric_weight_values(value):
    assert parse_kbju_json(_ok_payload([_valid_item(grams=value)])) is None


def test_parse_kbju_json_requires_every_numeric_item_field():
    item = _valid_item()
    del item["protein"]

    assert parse_kbju_json(_ok_payload([item])) is None


@pytest.mark.parametrize(
    "name",
    [
        "",
        "   ",
        None,
        "... --- !!!",
        "Б" * (MAX_ITEM_NAME_LENGTH + 1),
    ],
)
def test_parse_kbju_json_rejects_invalid_product_names(name):
    assert parse_kbju_json(_ok_payload([_valid_item(name=name)])) is None


def test_parse_kbju_json_rejects_too_many_items():
    items = [_valid_item(name=f"Продукт {index}") for index in range(MAX_FOOD_ITEMS + 1)]

    assert parse_kbju_json(_ok_payload(items)) is None


def test_parse_kbju_json_accepts_zero_kbju_for_water():
    parsed = parse_kbju_json(
        _ok_payload(
            [
                _valid_item(
                    name="Вода",
                    grams=500,
                    kcal=0,
                    protein=0,
                    fat=0,
                    carbs=0,
                )
            ]
        )
    )

    assert parsed is not None
    assert parsed["items"][0]["name"] == "Вода"
    assert parsed["total"] == {"kcal": 0, "protein": 0, "fat": 0, "carbs": 0}


def test_parse_kbju_json_recalculates_total_from_validated_items():
    parsed = parse_kbju_json(
        _ok_payload(
            [
                _valid_item(name="Продукт один", kcal=200, protein=10, fat=5, carbs=20),
                _valid_item(name="Продукт два", kcal=300, protein=20, fat=10, carbs=30),
            ]
        )
    )

    assert parsed is not None
    assert parsed["total"] == {
        "kcal": 500,
        "protein": 30,
        "fat": 15,
        "carbs": 50,
    }


def test_parse_kbju_json_discards_unknown_model_fields():
    parsed = parse_kbju_json(
        _ok_payload(
            [
                {
                    **_valid_item(),
                    "system_prompt": "PRIVATE_SYSTEM_PROMPT",
                    "secret_instruction": "PRIVATE_INSTRUCTION",
                }
            ],
            comment="PRIVATE_COMMENT",
        )
    )

    assert parsed is not None
    serialized = json.dumps(parsed, ensure_ascii=False)
    assert "PRIVATE_SYSTEM_PROMPT" not in serialized
    assert "PRIVATE_INSTRUCTION" not in serialized
    assert "PRIVATE_COMMENT" not in serialized
    assert set(parsed) == {"status", "items", "total"}
    assert set(parsed["items"][0]) == {"name", "grams", "kcal", "protein", "fat", "carbs"}


def test_parse_ai_json_error_does_not_embed_raw_provider_response():
    private_response = "PRIVATE_RAW_AI_RESPONSE_12345"

    with pytest.raises(FoodAnalysisParseError) as exc_info:
        parse_ai_json(private_response)

    assert private_response not in str(exc_info.value)
