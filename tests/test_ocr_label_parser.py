from services.ocr_openrouter_parser import parse_ocr_label_json, OCRLabelParseError
from services.ocr_service import OCRService, is_ocr_text_good_enough
import services.ocr_service as ocr_service_module


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


def test_is_ocr_text_good_enough_accepts_label_markers_and_length():
    text = (
        "Пищевая ценность на 100 г: энергетическая ценность 250 ккал. "
        "Белки 10 г, жиры 5 г, углеводы 30 г."
    )
    assert is_ocr_text_good_enough(text) is True


def test_is_ocr_text_good_enough_rejects_noise_without_markers():
    text = "asdf qwer zxcv 1234 !!! ??? random words without nutrition labels long enough"
    assert is_ocr_text_good_enough(text) is False


def test_parse_label_via_ocr_pipeline_fallback_to_original(monkeypatch, tmp_path):
    image_path = tmp_path / "label.jpg"
    image_path.write_bytes(b"fake")

    monkeypatch.setattr(ocr_service_module, "preprocess_image_for_ocr", lambda path: str(image_path))
    calls = {"count": 0}

    def fake_extract(path):
        calls["count"] += 1
        if calls["count"] == 1:
            return ""
        return "Пищевая ценность 250 ккал, белки 10 г, жиры 5 г, углеводы 30 г"

    monkeypatch.setattr(ocr_service_module, "extract_text_with_tesseract", fake_extract)
    monkeypatch.setattr(ocr_service_module.os, "remove", lambda path: None)

    result = ocr_service_module.parse_label_via_ocr_pipeline(image_path)

    assert result.success is True
    assert calls["count"] == 2
