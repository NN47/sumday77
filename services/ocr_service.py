"""OCR-only сервис для тестового распознавания этикеток."""
from __future__ import annotations

import logging
import os
import re
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from config import OCR_MAX_SIDE_PX, OCR_MIN_TEXT_LENGTH, OCR_TIMEOUT_SECONDS

try:
    import pytesseract
except ModuleNotFoundError:  # pragma: no cover - зависит от окружения
    pytesseract = None

try:
    from PIL import Image, ImageEnhance, ImageFilter, ImageOps
except ModuleNotFoundError:  # pragma: no cover - зависит от окружения
    Image = None
    ImageEnhance = None
    ImageFilter = None
    ImageOps = None

logger = logging.getLogger(__name__)

LABEL_MARKERS = (
    "ккал",
    "белки",
    "жиры",
    "углеводы",
    "kcal",
    "protein",
    "fat",
    "carbs",
    "энергетическая ценность",
    "пищевая ценность",
)


@dataclass
class OCRResult:
    """Результат OCR-пайплайна для тестовой кнопки."""

    success: bool
    text: str
    error_type: str | None
    error_message: str | None
    processing_time_ms: int
    used_preprocessing: bool


class OCRServiceError(Exception):
    """Базовая ошибка OCR."""


class OCRTimeoutError(OCRServiceError):
    """Таймаут OCR."""


class OCRDependencyError(OCRServiceError):
    """Отсутствуют зависимости OCR."""


class OCRImageOpenError(OCRServiceError):
    """Ошибка открытия/обработки изображения."""


def _clean_ocr_text(raw_text: str, max_length: int = 4500) -> str:
    if not raw_text:
        return ""

    text = raw_text.replace("\r", "\n")
    text = re.sub(r"[\t ]+", " ", text)
    lines = []
    for line in text.split("\n"):
        compact = re.sub(r"\s+", " ", line).strip()
        if not compact:
            continue
        if re.fullmatch(r"[-_=~*|]+", compact):
            continue
        lines.append(compact)

    result = "\n".join(lines).strip()
    return result[:max_length].strip()


def preprocess_image_for_ocr(path: str | Path) -> str:
    """Открывает и подготавливает изображение под OCR, возвращает путь к temp-файлу."""
    source = Path(path)
    if not source.exists():
        raise OCRImageOpenError(f"Image does not exist: {source}")
    if Image is None or ImageEnhance is None or ImageFilter is None or ImageOps is None:
        raise OCRDependencyError("Pillow is not installed")

    preprocess_started = time.perf_counter()
    try:
        with Image.open(source) as img:
            original_size = img.size
            prepared = ImageOps.exif_transpose(img)
            prepared = prepared.convert("L")

            width, height = prepared.size
            scale = min(1.0, OCR_MAX_SIDE_PX / max(width, height))
            if scale < 1.0:
                resized = (int(width * scale), int(height * scale))
                prepared = prepared.resize(resized, Image.Resampling.LANCZOS)

            prepared = ImageEnhance.Contrast(prepared).enhance(1.8)
            prepared = prepared.point(lambda px: 255 if px > 150 else 0)
            prepared = prepared.filter(ImageFilter.SHARPEN)

            with tempfile.NamedTemporaryFile(prefix="ocr_preprocessed_", suffix=".png", delete=False) as tmp:
                prepared.save(tmp.name, format="PNG")
                processed_path = tmp.name

            logger.info(
                "OCR preprocess done: source=%s size_before=%sx%s size_after=%sx%s elapsed_ms=%s",
                source,
                original_size[0],
                original_size[1],
                prepared.size[0],
                prepared.size[1],
                int((time.perf_counter() - preprocess_started) * 1000),
            )
            return processed_path
    except OCRServiceError:
        raise
    except Exception as exc:  # pragma: no cover - зависит от файлов и PIL
        raise OCRImageOpenError(str(exc)) from exc


def extract_text_with_tesseract(path: str | Path) -> str:
    """Распознает текст через Tesseract с жёстким таймаутом."""
    if pytesseract is None:
        raise OCRDependencyError("pytesseract is not installed")
    if Image is None:
        raise OCRDependencyError("Pillow is not installed")

    ocr_started = time.perf_counter()
    try:
        with Image.open(path) as image:
            text = pytesseract.image_to_string(
                image,
                lang="rus+eng",
                config="--oem 3 --psm 6",
                timeout=OCR_TIMEOUT_SECONDS,
            )
            logger.info(
                "OCR tesseract done: path=%s elapsed_ms=%s text_len=%s",
                path,
                int((time.perf_counter() - ocr_started) * 1000),
                len(text or ""),
            )
            return (text or "").strip()
    except RuntimeError as exc:  # pragma: no cover - зависит от внешнего tesseract
        msg = str(exc).lower()
        if "timeout" in msg or "time out" in msg:
            raise OCRTimeoutError(str(exc)) from exc
        raise OCRServiceError(str(exc)) from exc
    except Exception as exc:  # pragma: no cover - зависит от внешней утилиты
        raise OCRServiceError(str(exc)) from exc


def is_ocr_text_good_enough(text: str) -> bool:
    """Возвращает True, если OCR-текст похож на этикетку продукта."""
    cleaned = _clean_ocr_text(text)
    normalized = cleaned.lower()
    if len(cleaned) < OCR_MIN_TEXT_LENGTH:
        return False

    letters_digits = re.findall(r"[A-Za-zА-Яа-яЁё0-9]", cleaned)
    garbage_chars = re.findall(r"[^\w\s.,:%()/+\-№]", cleaned, flags=re.UNICODE)
    if len(letters_digits) < 25:
        return False
    garbage_ratio = len(garbage_chars) / max(len(cleaned), 1)
    if garbage_ratio > 0.35:
        return False

    return any(marker in normalized for marker in LABEL_MARKERS)


def parse_label_via_ocr_pipeline(path: str | Path) -> OCRResult:
    """Полный OCR-only пайплайн: preprocess -> tesseract -> quality check."""
    started = time.perf_counter()
    source = Path(path)
    processed_path: str | None = None
    used_preprocessing = False

    logger.info(
        "OCR pipeline start: path=%s file_size_bytes=%s timeout=%ss max_side=%s",
        source,
        source.stat().st_size if source.exists() else "n/a",
        OCR_TIMEOUT_SECONDS,
        OCR_MAX_SIDE_PX,
    )

    try:
        processed_path = preprocess_image_for_ocr(source)
        used_preprocessing = True
        raw_text = extract_text_with_tesseract(processed_path)
        cleaned_text = _clean_ocr_text(raw_text)
        logger.info("OCR pipeline text stats: text_len=%s", len(cleaned_text))

        # Фолбэк: если агрессивная предобработка испортила символы,
        # пробуем распознавание с исходного изображения.
        if not cleaned_text or not is_ocr_text_good_enough(cleaned_text):
            logger.info(
                "OCR pipeline fallback to original image: initial_text_len=%s initial_good=%s",
                len(cleaned_text),
                is_ocr_text_good_enough(cleaned_text) if cleaned_text else False,
            )
            fallback_raw_text = extract_text_with_tesseract(source)
            fallback_cleaned_text = _clean_ocr_text(fallback_raw_text)
            if len(fallback_cleaned_text) > len(cleaned_text):
                cleaned_text = fallback_cleaned_text
            logger.info("OCR pipeline fallback stats: text_len=%s", len(fallback_cleaned_text))

        if not cleaned_text:
            return OCRResult(
                success=False,
                text="",
                error_type="empty_text",
                error_message="OCR returned empty text",
                processing_time_ms=int((time.perf_counter() - started) * 1000),
                used_preprocessing=used_preprocessing,
            )

        if not is_ocr_text_good_enough(cleaned_text):
            return OCRResult(
                success=False,
                text=cleaned_text,
                error_type="low_quality_text",
                error_message="OCR text does not match label quality heuristics",
                processing_time_ms=int((time.perf_counter() - started) * 1000),
                used_preprocessing=used_preprocessing,
            )

        return OCRResult(
            success=True,
            text=cleaned_text,
            error_type=None,
            error_message=None,
            processing_time_ms=int((time.perf_counter() - started) * 1000),
            used_preprocessing=used_preprocessing,
        )
    except OCRTimeoutError as exc:
        logger.warning("OCR timeout: path=%s error=%s", source, exc)
        return OCRResult(
            success=False,
            text="",
            error_type="timeout",
            error_message=str(exc),
            processing_time_ms=int((time.perf_counter() - started) * 1000),
            used_preprocessing=used_preprocessing,
        )
    except OCRImageOpenError as exc:
        logger.error("OCR image_open_error: path=%s error=%s", source, exc)
        return OCRResult(
            success=False,
            text="",
            error_type="image_open_error",
            error_message=str(exc),
            processing_time_ms=int((time.perf_counter() - started) * 1000),
            used_preprocessing=used_preprocessing,
        )
    except OCRDependencyError as exc:
        logger.error("OCR tesseract_error (dependency): path=%s error=%s", source, exc)
        return OCRResult(
            success=False,
            text="",
            error_type="tesseract_error",
            error_message=str(exc),
            processing_time_ms=int((time.perf_counter() - started) * 1000),
            used_preprocessing=used_preprocessing,
        )
    except OCRServiceError as exc:
        logger.error("OCR tesseract_error: path=%s error=%s", source, exc)
        return OCRResult(
            success=False,
            text="",
            error_type="tesseract_error",
            error_message=str(exc),
            processing_time_ms=int((time.perf_counter() - started) * 1000),
            used_preprocessing=used_preprocessing,
        )
    finally:
        if processed_path:
            try:
                os.remove(processed_path)
            except OSError as exc:
                logger.warning("OCR cleanup_error: path=%s error=%s", processed_path, exc)


class OCRService:
    """Backwards-compatible wrapper over OCR functions."""

    @staticmethod
    def preprocess_image_for_ocr(path: str | Path) -> str:
        return preprocess_image_for_ocr(path)

    @staticmethod
    def extract_text_with_tesseract(path: str | Path) -> str:
        return extract_text_with_tesseract(path)

    @staticmethod
    def is_ocr_text_good_enough(text: str) -> bool:
        return is_ocr_text_good_enough(text)

    @staticmethod
    def parse_label_via_ocr_pipeline(path: str | Path) -> OCRResult:
        return parse_label_via_ocr_pipeline(path)

    @staticmethod
    def clean_ocr_text(raw_text: str, max_length: int = 4500) -> str:
        return _clean_ocr_text(raw_text, max_length=max_length)


ocr_service = OCRService()
