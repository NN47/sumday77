from services.ocr_openrouter_parser import parse_ocr_label_json, OCRLabelParseError
from services.ocr_service import OCRService


def test_parse_ocr_label_json_extracts_embedded_json():
    raw = "Ответ модели:\n```json\n{\"product_name\":\"Творог\",\"weight_grams\":\"180\",\"nutrition_per_100g\":{\"calories\":\"120,5\",\"protein\":\"16\",\"fat\":\"5\",\"carbs\":\"3\"},\"nutrition_total\":{\"calories\":null,\"protein\":null,\"fat\":null,\"carbs\":null},\"confidence\":\"high\",\"notes\":\"Ок\"}\n```"
    parsed = parse_ocr_label_json(raw)

    assert parsed["product_name"] == "Творог"
    assert parsed["weight_grams"] == 180.0
    assert parsed["nutrition_per_100g"]["calories"] == 120.5
    assert parsed["confidence"] == "high"


def test_parse_ocr_label_json_fails_on_non_json():
    try:
        parse_ocr_label_json("nonsense")
    except OCRLabelParseError:
        assert True
    else:
        assert False


def test_clean_ocr_text_removes_empty_lines_and_spaces():
    service = OCRService()
    cleaned = service.clean_ocr_text("  Энергия   250 ккал  \n\n\n  Белки 10 г\t\n---\n")
    assert cleaned == "Энергия 250 ккал\nБелки 10 г"
