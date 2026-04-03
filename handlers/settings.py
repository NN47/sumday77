"""Обработчики для настроек."""
import logging
from aiogram import Router
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
from database.session import get_db_session
from states.user_states import SupportStates

logger = logging.getLogger(__name__)

router = Router()


def reset_user_state(message: Message, *, keep_supplements: bool = False):
    """Сбрасывает состояние пользователя (упрощённая версия)."""
    # TODO: Заменить на FSM состояния
    pass


def delete_user_account(user_id: str) -> bool:
    """Удаляет аккаунт пользователя и все связанные данные."""
    from database.models import (
        Workout, Weight, Measurement, Meal, KbjuSettings,
        SupplementEntry, Supplement, Procedure, WaterEntry, User
    )
    
    with get_db_session() as session:
        try:
            # Удаляем все данные пользователя из всех таблиц
            session.query(Workout).filter_by(user_id=user_id).delete()
            session.query(Weight).filter_by(user_id=user_id).delete()
            session.query(Measurement).filter_by(user_id=user_id).delete()
            session.query(Meal).filter_by(user_id=user_id).delete()
            session.query(KbjuSettings).filter_by(user_id=user_id).delete()
            session.query(SupplementEntry).filter_by(user_id=user_id).delete()
            session.query(Supplement).filter_by(user_id=user_id).delete()
            session.query(Procedure).filter_by(user_id=user_id).delete()
            session.query(WaterEntry).filter_by(user_id=user_id).delete()
            session.query(User).filter_by(user_id=user_id).delete()
            
            session.commit()
            logger.info(f"Successfully deleted account for user {user_id}")
            return True
        except Exception as e:
            logger.error(f"Error deleting account for user {user_id}: {e}")
            session.rollback()
            return False


@router.message(lambda m: m.text == "⚙️ Настройки")
async def settings(message: Message, state: FSMContext):
    """Показывает меню настроек."""
    reset_user_state(message)
    await state.clear()  # Очищаем FSM состояние
    user_id = str(message.from_user.id)
    logger.info(f"User {user_id} opened settings")
    
    push_menu_stack(message.bot, settings_menu)
    await message.answer(
        "⚙️ Настройки\n\nВыбери действие:",
        reply_markup=settings_menu,
    )


@router.message(lambda m: m.text == "🗑 Удалить аккаунт")
async def delete_account_start(message: Message):
    """Начинает процесс удаления аккаунта."""
    reset_user_state(message)
    message.bot.expecting_account_deletion_confirm = True
    user_id = str(message.from_user.id)
    logger.warning(f"User {user_id} initiated account deletion")
    
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


@router.message(lambda m: m.text == "✅ Да, удалить аккаунт")
async def delete_account_confirm(message: Message):
    """Подтверждает удаление аккаунта."""
    if not getattr(message.bot, "expecting_account_deletion_confirm", False):
        await message.answer("Что-то пошло не так. Попробуй заново через меню Настройки.")
        return
    
    user_id = str(message.from_user.id)
    message.bot.expecting_account_deletion_confirm = False
    logger.warning(f"User {user_id} confirmed account deletion")
    
    success = delete_user_account(user_id)
    
    if success:
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
        push_menu_stack(message.bot, settings_menu)
        await message.answer(
            "❌ Произошла ошибка при удалении аккаунта.\n"
            "Попробуйте позже или обратитесь в поддержку.",
            reply_markup=settings_menu,
        )


@router.message(lambda m: m.text == "❌ Отмена")
async def delete_account_cancel(message: Message):
    """Отменяет удаление аккаунта."""
    if getattr(message.bot, "expecting_account_deletion_confirm", False):
        message.bot.expecting_account_deletion_confirm = False
        push_menu_stack(message.bot, settings_menu)
        await message.answer(
            "❌ Удаление аккаунта отменено.",
            reply_markup=settings_menu,
        )


@router.message(lambda m: m.text == "💬 Поддержка")
async def support(message: Message, state: FSMContext):
    """Начинает процесс отправки сообщения в поддержку."""
    reset_user_state(message)
    user_id = str(message.from_user.id)
    logger.info(f"User {user_id} opened support")
    
    await state.set_state(SupportStates.waiting_for_message)
    await message.answer(
        "💬 <b>Поддержка</b>\n\n"
        "Напишите ваш вопрос или сообщение для поддержки. Я перешлю его администратору.\n\n"
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
    
    # ID администратора поддержки
    SUPPORT_USER_ID = 6065083722
    
    # Формируем сообщение для администратора
    user_info = f"👤 <b>Пользователь:</b>\n"
    user_info += f"ID: <code>{user_id}</code>\n"
    if message.from_user.username:
        user_info += f"Username: @{message.from_user.username}\n"
    if message.from_user.first_name:
        user_info += f"Имя: {message.from_user.first_name}\n"
    if message.from_user.last_name:
        user_info += f"Фамилия: {message.from_user.last_name}\n"
    user_info += f"Язык: {message.from_user.language_code or 'не указан'}\n\n"
    user_info += f"💬 <b>Сообщение:</b>\n{user_text}"
    
    try:
        # Отправляем сообщение администратору
        await message.bot.send_message(
            chat_id=SUPPORT_USER_ID,
            text=user_info,
            parse_mode="HTML"
        )
        
        # Подтверждаем пользователю
        await state.clear()
        push_menu_stack(message.bot, settings_menu)
        await message.answer(
            "✅ <b>Сообщение отправлено!</b>\n\n"
            "Ваше сообщение успешно доставлено в поддержку. Мы ответим вам в ближайшее время.",
            reply_markup=settings_menu,
            parse_mode="HTML",
        )
        logger.info(f"Support message from user {user_id} sent to admin {SUPPORT_USER_ID}")
    except Exception as e:
        logger.error(f"Error sending support message: {e}")
        await message.answer(
            "❌ Произошла ошибка при отправке сообщения. Попробуйте позже.",
            reply_markup=settings_menu,
        )


@router.message(lambda m: m.text == "🔒 Политика конфиденциальности")
async def privacy_policy(message: Message):
    """Показывает политику конфиденциальности."""
    reset_user_state(message)
    user_id = str(message.from_user.id)
    logger.info(f"User {user_id} viewed privacy policy")
    
    privacy_text = (
        "🔒 <b>Политика конфиденциальности</b>\n\n"
        "Добро пожаловать в Fitness Bot! Мы ценим вашу конфиденциальность и стремимся защищать ваши личные данные.\n\n"
        "<b>1. Сбор данных</b>\n"
        "Бот собирает и хранит следующие данные:\n"
        "• Идентификатор пользователя Telegram\n"
        "• Данные о тренировках (упражнения, количество, даты)\n"
        "• Записи веса и замеров тела\n"
        "• Записи питания (КБЖУ)\n"
        "• Информация о добавках и их приёме\n"
        "• Настройки КБЖУ и цели\n\n"
        "<b>2. Использование данных</b>\n"
        "Ваши данные используются исключительно для:\n"
        "• Предоставления функционала бота\n"
        "• Отображения статистики и прогресса\n"
        "• Расчёта калорий и КБЖУ\n"
        "• Хранения истории тренировок и питания\n\n"
        "<b>3. Хранение данных</b>\n"
        "Все данные хранятся в защищённой базе данных на сервере бота. "
        "Мы применяем стандартные меры безопасности для защиты вашей информации.\n\n"
        "<b>4. Передача данных третьим лицам</b>\n"
        "Мы не передаём ваши персональные данные третьим лицам. "
        "Данные используются только для работы бота и не продаются, не сдаются в аренду и не передаются другим компаниям.\n\n"
        "<b>5. Удаление данных</b>\n"
        "Вы можете в любой момент удалить свой аккаунт и все связанные данные через функцию "
        "\"🗑 Удалить аккаунт\" в настройках. После удаления все ваши данные будут безвозвратно удалены из базы данных.\n\n"
        "<b>6. Изменения в политике</b>\n"
        "Мы оставляем за собой право обновлять данную политику конфиденциальности. "
        "О существенных изменениях мы уведомим пользователей через бота.\n\n"
        "<b>7. Контакты</b>\n"
        "Если у вас есть вопросы о политике конфиденциальности, используйте функцию \"💬 Поддержка\" в настройках.\n\n"
        "Дата последнего обновления: 17.12.2025"
    )
    
    push_menu_stack(message.bot, settings_menu)
    await message.answer(privacy_text, reply_markup=settings_menu, parse_mode="HTML")


def register_settings_handlers(dp):
    """Регистрирует обработчики настроек."""
    dp.include_router(router)
