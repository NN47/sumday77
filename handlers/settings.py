"""Обработчики для настроек."""
import asyncio
import html
import logging
from aiogram import Router
from aiogram.filters import StateFilter
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton
from aiogram.fsm.context import FSMContext
from utils.keyboards import (
    MAIN_MENU_BUTTON_ALIASES,
    MAIN_MENU_BUTTON_TEXT,
    delete_account_confirm_menu,
    main_menu_button,
    push_menu_stack,
    settings_menu,
)
from database.account_deletion import delete_user_account
from database.repositories import SupportRepository, AnalyticsRepository, ErrorLogRepository
from states.user_states import AccountDeletionStates, SupportStates
from user_operation_guard import user_operation_guard
from services.user_process_cleanup import clear_user_fsm_states, clear_user_process_caches
from config import ADMIN_ID
from utils.log_sanitizer import safe_exception_summary
from utils.sensitive_text import check_sensitive_support_text
from utils.legal_documents import SUPPORT_CONTACT

logger = logging.getLogger(__name__)

router = Router()
account_deletion_router = Router(name="account_deletion")

SUPPORT_SENSITIVE_INPUT_WARNING = (
    "Не отправляйте ФИО, телефон, адрес, email, пароли, коды доступа, реквизиты документов, банковские данные, "
    "диагнозы и подробности лечения. Для решения технической проблемы опишите действия "
    "в боте и текст ошибки без личных подробностей."
)
SUPPORT_SENSITIVE_INPUT_REJECTED_TEXT = (
    "⚠️ Сообщение не отправлено. Удалите личные и чувствительные подробности и "
    "переформулируйте вопрос, описав только действия в боте и текст ошибки."
)


def reset_user_state(message: Message, *, keep_supplements: bool = False):
    """Сбрасывает состояние пользователя (упрощённая версия)."""
    # TODO: Заменить на FSM состояния
    pass


def _is_start_command(message: Message) -> bool:
    command = (message.text or "").split(maxsplit=1)
    return bool(command and command[0].split("@")[0] == "/start")


@router.message(lambda m: m.text == "⚙️ Настройки")
async def settings(message: Message, state: FSMContext):
    """Показывает меню настроек."""
    reset_user_state(message)
    await state.clear()  # Очищаем FSM состояние
    user_id = str(message.from_user.id)
    logger.info("Settings opened")
    
    push_menu_stack(message.bot, settings_menu)
    await message.answer(
        "⚙️ Настройки\n\nВыбери действие:",
        reply_markup=settings_menu,
    )


@account_deletion_router.message(lambda m: m.text == "🗑 Удалить аккаунт")
async def delete_account_start(message: Message, state: FSMContext):
    """Начинает процесс удаления аккаунта."""
    reset_user_state(message)
    await state.clear()
    await state.set_state(AccountDeletionStates.waiting_for_button_confirmation)
    logger.warning("Account deletion initiated")
    
    push_menu_stack(message.bot, delete_account_confirm_menu)
    await message.answer(
        "⚠️ <b>ВНИМАНИЕ!</b>\n\n"
        "Вы уверены, что хотите удалить аккаунт?\n\n"
        "При удалении аккаунта будут <b>безвозвратно удалены</b> все ваши данные:\n"
        "• Все тренировки\n"
        "• Все записи веса и замеров\n"
        "• Все записи КБЖУ\n"
        "• Все добавки и их история\n"
        "• Настройки КБЖУ\n\n"
        "Это действие нельзя отменить!",
        reply_markup=delete_account_confirm_menu,
        parse_mode="HTML",
    )


@account_deletion_router.message(
    StateFilter(AccountDeletionStates.waiting_for_button_confirmation),
    lambda m: m.text == "Да, удалить аккаунт",
)
async def delete_account_confirm(message: Message, state: FSMContext):
    """Запрашивает текстовое подтверждение удаления аккаунта."""
    await state.set_state(AccountDeletionStates.waiting_for_text_confirmation)
    await message.answer(
        "Подтвердите удаление: введите текстом\n\n"
        "<b>Я удаляю аккаунт Sumday77</b>",
        parse_mode="HTML",
    )


@account_deletion_router.message(
    StateFilter(AccountDeletionStates.waiting_for_text_confirmation),
    lambda m: m.text != "❌ Отмена" and not _is_start_command(m),
)
async def delete_account_text_confirm(message: Message, state: FSMContext):
    """Удаляет аккаунт после текстового подтверждения."""
    expected_text = "Я удаляю аккаунт Sumday77"
    if (message.text or "").strip() != expected_text:
        await message.answer(
            "Текст подтверждения не совпадает.\n"
            f"Введите точно: <b>{expected_text}</b>\n"
            "Или нажмите «❌ Отмена».",
            parse_mode="HTML",
        )
        return

    user_id = str(message.from_user.id)
    logger.warning("Account deletion confirmed")

    await user_operation_guard.begin_deletion(user_id)
    try:
        success = await asyncio.to_thread(delete_user_account, user_id)
    except BaseException:
        user_operation_guard.rollback_deletion(user_id)
        raise

    if success:
        user_operation_guard.complete_deletion(user_id)
        try:
            await clear_user_fsm_states(state, user_id)
        finally:
            clear_user_process_caches(message.bot, user_id)
        await message.answer(
            "✅ Аккаунт успешно удалён.\n\n"
            "Все ваши данные были удалены из базы данных.\n\n"
            "Если захотите вернуться, просто нажмите /start",
            reply_markup=ReplyKeyboardMarkup(
                keyboard=[[KeyboardButton(text="/start")]],
                resize_keyboard=True
            )
        )
    else:
        user_operation_guard.rollback_deletion(user_id)
        push_menu_stack(message.bot, delete_account_confirm_menu)
        await message.answer(
            "❌ Произошла ошибка при удалении аккаунта.\n"
            "Можно повторить текст подтверждения или нажать «❌ Отмена».\n"
            f"Поддержка: {SUPPORT_CONTACT}.",
            reply_markup=delete_account_confirm_menu,
        )


@account_deletion_router.message(
    StateFilter(
        AccountDeletionStates.waiting_for_button_confirmation,
        AccountDeletionStates.waiting_for_text_confirmation,
    ),
    lambda m: m.text == "❌ Отмена",
)
async def delete_account_cancel(message: Message, state: FSMContext):
    """Отменяет удаление аккаунта."""
    await state.clear()
    push_menu_stack(message.bot, settings_menu)
    await message.answer(
        "❌ Удаление аккаунта отменено.",
        reply_markup=settings_menu,
    )


@account_deletion_router.message(
    StateFilter(AccountDeletionStates.waiting_for_button_confirmation),
    lambda m: not _is_start_command(m),
)
async def delete_account_button_required(message: Message):
    await message.answer("Выберите «Да, удалить аккаунт» или «❌ Отмена».",
                         reply_markup=delete_account_confirm_menu)


@router.message(lambda m: m.text == "💬 Поддержка")
async def support(message: Message, state: FSMContext):
    """Начинает процесс отправки сообщения в поддержку."""
    reset_user_state(message)
    user_id = str(message.from_user.id)
    logger.info("Support flow opened")
    
    await state.set_state(SupportStates.waiting_for_message)
    await message.answer(
        "💬 <b>Поддержка</b>\n\n"
        "Напишите ваш вопрос или сообщение для поддержки. Я перешлю его администратору.\n"
        f"Связаться напрямую: {SUPPORT_CONTACT}\n\n"
        f"⚠️ {SUPPORT_SENSITIVE_INPUT_WARNING}\n\n"
        f"Для отмены используйте кнопку '⬅️ Назад' или '{MAIN_MENU_BUTTON_TEXT}'.",
        reply_markup=ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text="⬅️ Назад"), main_menu_button]],
            resize_keyboard=True
        ),
        parse_mode="HTML",
    )


@router.message(SupportStates.waiting_for_message)
async def handle_support_message(message: Message, state: FSMContext):
    """Обрабатывает сообщение пользователя и пересылает его в поддержку."""
    user_id = str(message.from_user.id)
    user_text = message.text or message.caption or ""
    
    # Проверяем, не является ли это кнопкой меню
    if message.text in ["⬅️ Назад", "⚙️ Настройки"] or message.text in MAIN_MENU_BUTTON_ALIASES:
        await state.clear()
        if message.text in MAIN_MENU_BUTTON_ALIASES:
            from handlers.common import go_main_menu
            await go_main_menu(message, state)
        elif message.text == "⚙️ Настройки":
            await settings(message, state)
        else:  # "⬅️ Назад"
            push_menu_stack(message.bot, settings_menu)
            await message.answer(
                "❌ Отправка сообщения отменена.",
                reply_markup=settings_menu,
            )
        return
    
    if not user_text.strip():
        await message.answer("Пожалуйста, введите текст сообщения для поддержки.")
        return

    stripped_text = user_text.strip()
    sensitive_check = check_sensitive_support_text(stripped_text)
    if sensitive_check.is_sensitive:
        reason = sensitive_check.reason.value if sensitive_check.reason else "unknown"
        logger.info(
            "Sensitive support input rejected reason=%s message_chars=%s",
            reason,
            len(stripped_text),
        )
        await message.answer(SUPPORT_SENSITIVE_INPUT_REJECTED_TEXT)
        return

    # Формируем сообщение для администратора
    user_info = "👤 <b>Пользователь:</b>\n"
    user_info += f"ID: <code>{html.escape(user_id)}</code>\n"
    user_info += "\n"
    user_info += f"💬 <b>Сообщение:</b>\n{html.escape(stripped_text)}"
    
    try:
        # Отправляем сообщение администратору
        await message.bot.send_message(
            chat_id=ADMIN_ID,
            text=user_info,
            parse_mode="HTML"
        )
        SupportRepository.create_message(
            user_id=user_id,
            message_text=stripped_text,
        )
        AnalyticsRepository.track_event(user_id, "support_message_sent", section="support")
        
        # Подтверждаем пользователю
        await state.clear()
        push_menu_stack(message.bot, settings_menu)
        await message.answer(
            "✅ <b>Сообщение отправлено!</b>\n\n"
            "Ваше сообщение успешно доставлено в поддержку. Мы ответим вам в ближайшее время.",
            reply_markup=settings_menu,
            parse_mode="HTML",
        )
        logger.info("Support message delivered message_chars=%s", len(stripped_text))
    except Exception as e:
        logger.error("Error sending support message error_type=%s", safe_exception_summary(e), exc_info=True)
        ErrorLogRepository.log_error(
            user_id=user_id,
            error_type=type(e).__name__,
            error_message=safe_exception_summary(e),
            module=__name__,
            function_name="handle_support_message",
        )
        await message.answer(
            "❌ Произошла ошибка при отправке сообщения. Попробуйте позже.",
            reply_markup=settings_menu,
        )


@router.message(lambda m: m.text == "🔒 Политика конфиденциальности")
async def privacy_policy(message: Message):
    """Показывает политику конфиденциальности."""
    from handlers.legal import show_document
    await show_document(message, "privacy")


def register_settings_handlers(dp):
    """Регистрирует обработчики настроек."""
    dp.include_router(router)
