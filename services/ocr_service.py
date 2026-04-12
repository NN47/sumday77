"""Сервис OCR для распознавания текста с этикеток."""
from __future__ import annotations

import logging
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path

try:
    import pytesseract
except ModuleNotFoundError:  # pragma: no cover - зависит от окружения
    pytesseract = None

try:
    from PIL import Image, ImageEnhance, ImageFilter
except ModuleNotFoundError:  # pragma: no cover - зависит от окружения
    Image = None
    ImageEnhance = None
    ImageFilter = None

logger = logging.getLogger(__name__)


class OCRServiceError(Exception):
    """Базовая ошибка OCR."""


@dataclass
class OCRResult:
    """Результат OCR-обработки изображения."""

    raw_ocr_text: str
    cleaned_ocr_text: str
    metadata: dict


class OCRService:
    """Распознавание текста через Tesseract + базовая очистка."""

    def preprocess_image(self, image_path: str | Path) -> str:
        """Подготавливает изображение для OCR и возвращает путь к временному файлу."""
        source = Path(image_path)
        if not source.exists():
            raise OCRServiceError(f"Image does not exist: {source}")
        if Image is None or ImageEnhance is None or ImageFilter is None:
            raise OCRServiceError("Pillow is not installed")

        with Image.open(source) as image:
            prepared = image.convert("L")
            prepared = ImageEnhance.Contrast(prepared).enhance(1.6)
            width, height = prepared.size
            if max(width, height) < 1400:
                prepared = prepared.resize((int(width * 1.7), int(height * 1.7)))
            prepared = prepared.filter(ImageFilter.SHARPEN)

            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
                prepared.save(tmp.name, format="PNG")
                return tmp.name

    def extract_text_with_tesseract(self, image_path: str | Path) -> str:
        """Извлекает текст из изображения через Tesseract."""
        if pytesseract is None:
            raise OCRServiceError("pytesseract is not installed")
        if Image is None:
            raise OCRServiceError("Pillow is not installed")
        try:
            with Image.open(image_path) as image:
                text = pytesseract.image_to_string(
                    image,
                    lang="rus+eng",
                    config="--oem 3 --psm 6",
                )
        except Exception as exc:  # pragma: no cover - зависит от внешней утилиты
            raise OCRServiceError(str(exc)) from exc
        return (text or "").strip()

    @staticmethod
    def clean_ocr_text(raw_text: str, max_length: int = 4500) -> str:
        """Чистит OCR-текст от шума и пустых строк."""
        if not raw_text:
            return ""

        text = raw_text.replace("\r", "\n")
        text = re.sub(r"[\t ]+", " ", text)

        cleaned_lines: list[str] = []
        for line in text.split("\n"):
            compact = re.sub(r"\s+", " ", line).strip()
            if not compact:
                continue
            if re.fullmatch(r"[-_=~*|]+", compact):
                continue
            cleaned_lines.append(compact)

        merged_lines: list[str] = []
        for line in cleaned_lines:
            if merged_lines and len(line) <= 3 and line.isalpha():
                merged_lines[-1] = f"{merged_lines[-1]} {line}"
            else:
                merged_lines.append(line)

        result = "\n".join(merged_lines)
        if len(result) > max_length:
            result = result[:max_length]
        return result.strip()

    @staticmethod
    def is_text_quality_sufficient(text: str) -> tuple[bool, dict]:
        """Эвристика качества OCR-текста."""
        candidate = (text or "").strip()
        letters_digits = re.findall(r"[A-Za-zА-Яа-яЁё0-9]", candidate)
        garbage = re.findall(r"[^\w\s.,:%()/+-]", candidate, flags=re.UNICODE)

        stats = {
            "text_len": len(candidate),
            "letters_digits": len(letters_digits),
            "garbage_chars": len(garbage),
        }

        if stats["text_len"] < 35:
            return False, stats
        if stats["letters_digits"] < 20:
            return False, stats

        garbage_ratio = (stats["garbage_chars"] / max(stats["text_len"], 1))
        if garbage_ratio > 0.35:
            return False, stats

        return True, stats

    def extract_from_image(self, image_path: str | Path) -> OCRResult:
        """Полный пайплайн OCR: preprocess -> tesseract -> clean."""
        preprocessed_path = self.preprocess_image(image_path)
        raw_text = self.extract_text_with_tesseract(preprocessed_path)
        cleaned_text = self.clean_ocr_text(raw_text)
        quality_ok, quality_stats = self.is_text_quality_sufficient(cleaned_text)

        metadata = {
            "preprocessed_path": preprocessed_path,
            "quality_ok": quality_ok,
            **quality_stats,
        }

        logger.info(
            "OCR extracted text: raw_len=%s cleaned_len=%s quality_ok=%s",
            len(raw_text),
            len(cleaned_text),
            quality_ok,
        )
        return OCRResult(raw_ocr_text=raw_text, cleaned_ocr_text=cleaned_text, metadata=metadata)


ocr_service = OCRService()
