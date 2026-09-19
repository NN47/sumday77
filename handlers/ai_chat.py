"""Telegram handler изолированного режима «Спросить Sumday»."""
import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import KeyboardButton, Message, ReplyKeyboardMarkup

from database.repositories.ai_chat_repository import AIChatRepository
from services.ai_chat_safety import ChatSafetyCategory, classify_question
from services.ai_chat_service import AIChatService, ERROR_TEXT, ai_chat_service
from services.ai_quota_service import (
    AIFeature, AIAttemptLimitExceeded, AIGlobalLimitExceeded, AIOperationCooldown,
    AIOperationInProgress, AIQuotaExceeded, ai_quota_service, build_quota_request_id,
)
from states.user_states import AIChatStates
from utils.keyboards import activity_analysis_menu
from utils.log_sanitizer import safe_exception_summary

logger = logging.getLogger(__name__)
router = Router()

ASK_BUTTON = "💬 Спросить Sumday"
CLEAR_BUTTON = "🗑 Очистить диалог"
BACK_BUTTON = "⬅️ Назад в ИИ-анализ"
chat_keyboard = ReplyKeyboardMarkup(keyboard=[
    [KeyboardButton(text=CLEAR_BUTTON)], [KeyboardButton(text=BACK_BUTTON)],
], resize_keyboard=True)


@router.message(F.text == ASK_BUTTON)
async def open_ai_chat(message: Message, state: FSMContext):
    await state.set_state(AIChatStates.waiting_for_question)
    await message.answer(
        "💬 <b>Задайте вопрос о своём питании, дневнике, КБЖУ, блюдах, рецептах или активности.</b>\n\n"
        "Sumday использует данные вашего дневника для ответа.\n"
        "Ответ формируется искусственным интеллектом и носит информационный характер.\n\n"
        "Например:\n• Сколько белка я набрал сегодня?\n• Что можно съесть, чтобы добрать белок?\n"
        "• Как у меня питание за неделю?\n• Какой мой рецепт самый белковый?",
        reply_markup=chat_keyboard, parse_mode="HTML",
    )


@router.message(AIChatStates.waiting_for_question, F.text == BACK_BUTTON)
async def leave_ai_chat(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("🧠 ИИ-анализ", reply_markup=activity_analysis_menu)


@router.message(AIChatStates.waiting_for_question, F.text == CLEAR_BUTTON)
async def clear_ai_chat(message: Message):
    AIChatRepository.clear(str(message.from_user.id))
    await message.answer("Диалог очищен. Записи дневника, блюда и рецепты не изменены.", reply_markup=chat_keyboard)


@router.message(AIChatStates.waiting_for_question)
async def handle_ai_chat_question(message: Message, service: AIChatService = ai_chat_service):
    question = (message.text or "").strip()
    category = classify_question(question)
    if category != ChatSafetyCategory.ALLOWED:
        _, answer, _ = await service.answer(str(message.from_user.id), question)
        await message.answer(answer, reply_markup=chat_keyboard)
        return

    user_id = str(message.from_user.id)
    request_id = build_quota_request_id("ai_chat", user_id, message.chat.id, message.message_id)
    try:
        ai_quota_service.reserve(user_id, AIFeature.AI_CHAT, request_id)
    except AIQuotaExceeded:
        await message.answer("Лимит сообщений «Спросить Sumday» на сегодня закончился. Попробуйте после 03:00 МСК.", reply_markup=chat_keyboard)
        return
    except (AIAttemptLimitExceeded, AIGlobalLimitExceeded):
        await message.answer("ИИ-функция временно недоступна из-за защитного ограничения. Попробуйте позже.", reply_markup=chat_keyboard)
        return
    except (AIOperationInProgress, AIOperationCooldown):
        await message.answer("Предыдущий запрос ещё обрабатывается или сообщения отправлены слишком быстро.", reply_markup=chat_keyboard)
        return

    try:
        ai_quota_service.mark_provider_started(request_id)
        _, answer, _ = await service.answer(user_id, question)
        ai_quota_service.consume(request_id, outcome="success", result_ref="ai_chat_answer")
    except Exception as exc:
        ai_quota_service.release(request_id, outcome="provider_or_validation_error")
        logger.warning("AI chat request failed error_type=%s", safe_exception_summary(exc))
        answer = ERROR_TEXT
    await message.answer(answer, reply_markup=chat_keyboard)


def register_ai_chat_handlers(dp):
    dp.include_router(router)
