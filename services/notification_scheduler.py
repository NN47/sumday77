"""Сервис для отправки запланированных уведомлений."""
import asyncio
import logging
import json
import random
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from enum import Enum
from zoneinfo import ZoneInfo
from aiogram import Bot
from aiogram.fsm.storage.base import BaseStorage, StorageKey
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from database.session import get_db_session
from database.models import ActivityAnalysisEntry, User, Supplement, KbjuSettings, EveningAnalysisNotificationState
from database.repositories.evening_analysis_notification_repository import EveningAnalysisNotificationRepository
from services.error_logging_service import log_app_error

logger = logging.getLogger(__name__)
MSK_TZ = ZoneInfo("Europe/Moscow")
MEAL_TYPE_PREPOSITIONAL = {
    "завтрак": "завтраке",
    "обед": "обеде",
    "ужин": "ужине",
}
EVENING_ANALYSIS_TIME = time(22, 22)
EVENING_ANALYSIS_REMINDER_DELAY = timedelta(minutes=30)
EVENING_ANALYSIS_ACTIVITY_GRACE_PERIOD = timedelta(minutes=5)
EVENING_ANALYSIS_BUSY_POSTPONE_MINUTES = (10, 15)
EVENING_ANALYSIS_REMINDER_CUTOFF_TIME = time(2, 0)
EVENING_ANALYSIS_MAX_REMINDERS = 7
EVENING_ANALYSIS_START_PREFIX = "evening_analysis_start"
EVENING_ANALYSIS_REMIND_PREFIX = "evening_analysis_remind"
SUPPLEMENT_CONFIRM_PREFIX = "sup_confirm"
SUPPLEMENT_REMIND_LATER_PREFIX = "sup_remind"
SUPPLEMENT_REMINDER_DELAY = timedelta(minutes=30)


class DayAnalysisReminderBlockReason(Enum):
    """Причины, по которым напоминание анализа дня нельзя отправить сейчас."""

    ALREADY_ANALYZED = "already_analyzed"
    ACTIVE_FSM_STATE = "active_fsm_state"
    RECENT_ACTIVITY = "recent_activity"


@dataclass(frozen=True)
class DayAnalysisReminderDecision:
    """Результат проверки возможности отправить напоминание анализа дня."""

    can_send: bool
    reason: DayAnalysisReminderBlockReason | None = None

EVENING_ANALYSIS_MAIN_TEXT = (
    "<b>🌙 Вечерний анализ дня</b>\n\n"
    "Ты уже добавил все приёмы пищи за сегодня?\n\n"
    "Если всё на месте — <b>запущу ИИ-анализ дня</b>:\n"
    "🍽 питание\n"
    "🔥 калории\n"
    "💪 белок\n"
    "🏃 активность\n"
    "⚖️ вес и заметки\n\n"
    "Готовы?"
)
EVENING_ANALYSIS_REMINDER_TEXT = (
    "⏰ Напоминаю про анализ дня\n\n"
    "Ты уже добавил все приёмы пищи за сегодня?\n"
    "Если дневник заполнен — можем подвести итоги."
)


def build_supplement_notification_keyboard(supplement_id: int, time_text: str) -> InlineKeyboardMarkup:
    """Создаёт inline-кнопки для уведомления о приёме добавки."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Подтвердить прием",
                    callback_data=f"{SUPPLEMENT_CONFIRM_PREFIX}:{supplement_id}:{time_text}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="⏰ Напомнить позже",
                    callback_data=f"{SUPPLEMENT_REMIND_LATER_PREFIX}:{supplement_id}:{time_text}",
                )
            ],
        ]
    )


class NotificationScheduler:
    """Планировщик уведомлений о приёмах пищи и добавках."""
    
    def __init__(self, bot: Bot, storage: BaseStorage | None = None):
        self.bot = bot
        self.storage = storage
        self.running = False
        self.sent_notifications_today = set()  # Для предотвращения дублирования уведомлений
        self._last_check_date = None  # Дата последней проверки для сброса кэша
        
    async def send_notification(
        self,
        user_id: str,
        message: str,
        reply_markup: InlineKeyboardMarkup | None = None,
    ) -> bool:
        """Отправляет уведомление пользователю и возвращает успешность отправки."""
        try:
            await self.bot.send_message(
                chat_id=user_id,
                text=message,
                reply_markup=reply_markup,
            )
            logger.info(f"Уведомление отправлено пользователю {user_id}")
            return True
        except Exception as e:
            log_app_error(
                source="telegram",
                error=e,
                user_id=str(user_id),
                context="send_message",
                extra={"message_preview": message[:80]},
            )
            return False
    
    async def send_meal_notifications(self, meal_type: str, message_text: str):
        """Отправляет уведомления о приёме пищи всем пользователям."""
        try:
            with get_db_session() as session:
                users = (
                    session.query(User)
                    .join(KbjuSettings, KbjuSettings.user_id == User.user_id)
                    .all()
                )
                user_ids = [user.user_id for user in users]
            
            meal_type_prepositional = MEAL_TYPE_PREPOSITIONAL.get(meal_type, meal_type)
            logger.info(
                f"Отправка уведомлений о {meal_type_prepositional} "
                f"{len(user_ids)} пользователям"
            )
            
            # Отправляем уведомления всем пользователям
            tasks = [self.send_notification(user_id, message_text) for user_id in user_ids]
            await asyncio.gather(*tasks, return_exceptions=True)
            
            logger.info(f"Уведомления о {meal_type_prepositional} отправлены")
        except Exception as e:
            meal_type_prepositional = MEAL_TYPE_PREPOSITIONAL.get(meal_type, meal_type)
            logger.error(
                f"Ошибка при отправке уведомлений о {meal_type_prepositional}: {e}"
            )

    def build_evening_analysis_keyboard(self, target_date) -> InlineKeyboardMarkup:
        """Создаёт inline-кнопки для вечернего анализа дня."""
        date_payload = target_date.isoformat()
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="✅ Да, анализировать день",
                        callback_data=f"{EVENING_ANALYSIS_START_PREFIX}:{date_payload}",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="⏰ Напомнить позже",
                        callback_data=f"{EVENING_ANALYSIS_REMIND_PREFIX}:{date_payload}",
                    )
                ],
            ]
        )

    def _get_user_timezone(self, timezone_name: str | None) -> ZoneInfo:
        """Возвращает таймзону пользователя или московскую по умолчанию."""
        try:
            return ZoneInfo(timezone_name or "Europe/Moscow")
        except Exception:
            return MSK_TZ

    async def check_day_analysis_reminder_status(self, user_id: str, target_date) -> DayAnalysisReminderDecision:
        """Проверяет, можно ли сейчас отправить напоминание анализа дня."""
        user_id = str(user_id)
        now_utc = datetime.utcnow()

        with get_db_session() as session:
            state = (
                session.query(EveningAnalysisNotificationState)
                .filter(EveningAnalysisNotificationState.user_id == user_id)
                .first()
            )
            if state and state.last_daily_analysis_date == target_date:
                return DayAnalysisReminderDecision(False, DayAnalysisReminderBlockReason.ALREADY_ANALYZED)

            generated_today_exists = (
                session.query(ActivityAnalysisEntry.id)
                .filter(ActivityAnalysisEntry.user_id == user_id)
                .filter(ActivityAnalysisEntry.date == target_date)
                .filter(ActivityAnalysisEntry.source == "generated")
                .first()
                is not None
            )
            if generated_today_exists:
                return DayAnalysisReminderDecision(False, DayAnalysisReminderBlockReason.ALREADY_ANALYZED)

            user = session.query(User).filter(User.user_id == user_id).first()
            if (
                user
                and user.last_seen_at
                and now_utc - user.last_seen_at < EVENING_ANALYSIS_ACTIVITY_GRACE_PERIOD
            ):
                return DayAnalysisReminderDecision(False, DayAnalysisReminderBlockReason.RECENT_ACTIVITY)

        if await self._has_active_fsm_state(user_id):
            return DayAnalysisReminderDecision(False, DayAnalysisReminderBlockReason.ACTIVE_FSM_STATE)

        return DayAnalysisReminderDecision(True)

    async def can_send_day_analysis_reminder(self, user_id: str, target_date) -> bool:
        """Возвращает True, если напоминание анализа дня можно отправить прямо сейчас."""
        return (await self.check_day_analysis_reminder_status(user_id, target_date)).can_send

    async def _has_active_fsm_state(self, user_id: str) -> bool:
        """Проверяет наличие активного FSM-состояния пользователя в текущем storage."""
        if self.storage is None:
            return False
        try:
            numeric_user_id = int(user_id)
            bot_id = self.bot.id
            if bot_id is None:
                bot_info = await self.bot.get_me()
                bot_id = bot_info.id
            storage_key = StorageKey(
                bot_id=bot_id,
                chat_id=numeric_user_id,
                user_id=numeric_user_id,
            )
            return bool(await self.storage.get_state(storage_key))
        except Exception as e:
            logger.warning(
                "Не удалось проверить FSM-состояние для напоминания анализа дня: user_id=%s error=%s",
                user_id,
                e,
            )
            return False

    def _build_busy_postpone_due_at(self) -> datetime:
        """Возвращает время тихого переноса напоминания при активном сценарии пользователя."""
        delay_minutes = random.randint(*EVENING_ANALYSIS_BUSY_POSTPONE_MINUTES)
        return datetime.utcnow() + timedelta(minutes=delay_minutes)

    async def send_evening_analysis_notification(self, user_id: str, target_date, *, is_reminder: bool = False) -> bool:
        """Отправляет основное или повторное уведомление анализа дня."""
        text = EVENING_ANALYSIS_REMINDER_TEXT if is_reminder else EVENING_ANALYSIS_MAIN_TEXT
        return await self.send_notification(
            user_id,
            text,
            reply_markup=self.build_evening_analysis_keyboard(target_date),
        )

    def _is_before_evening_analysis_cutoff(self, local_now: datetime, target_date) -> bool:
        """Возвращает True, если напоминания за target_date ещё можно отправлять до 02:00 МСК."""
        cutoff_at = datetime.combine(target_date + timedelta(days=1), EVENING_ANALYSIS_REMINDER_CUTOFF_TIME, tzinfo=MSK_TZ)
        return local_now < cutoff_at

    async def check_and_send_evening_analysis_notifications(self):
        """Проверяет и отправляет вечерние уведомления ИИ-анализа дня."""
        try:
            now_utc = datetime.utcnow()
            pending_notifications: list[tuple[str, object, bool]] = []

            with get_db_session() as session:
                users = (
                    session.query(User)
                    .join(KbjuSettings, KbjuSettings.user_id == User.user_id)
                    .filter(User.notifications_enabled.is_(True))
                    .all()
                )

                for user in users:
                    # Вечерний анализ — такое же ежедневное уведомление, как приёмы пищи
                    # и добавки, поэтому ориентируемся на единый часовой пояс приложения.
                    # Иначе пользователи с пустой/ошибочной timezone в БД не получают
                    # напоминание в ожидаемые 22:22 по Москве.
                    local_now = datetime.now(MSK_TZ)
                    local_today = local_now.date()

                    state = (
                        session.query(EveningAnalysisNotificationState)
                        .filter(EveningAnalysisNotificationState.user_id == user.user_id)
                        .first()
                    )
                    if state is None:
                        state = EveningAnalysisNotificationState(user_id=user.user_id)
                        session.add(state)
                        session.flush()

                    if state.last_daily_analysis_date == local_today:
                        state.reminder_due_at = None
                        continue

                    generated_today_exists = (
                        session.query(ActivityAnalysisEntry.id)
                        .filter(ActivityAnalysisEntry.user_id == user.user_id)
                        .filter(ActivityAnalysisEntry.date == local_today)
                        .filter(ActivityAnalysisEntry.source == "generated")
                        .first()
                        is not None
                    )
                    if generated_today_exists:
                        state.last_daily_analysis_date = local_today
                        state.reminder_due_at = None
                        continue

                    if state.reminder_due_at and state.reminder_due_at <= now_utc:
                        reminder_target_date = state.remind_later_date or local_today
                        if (
                            state.remind_later_count <= EVENING_ANALYSIS_MAX_REMINDERS
                            and self._is_before_evening_analysis_cutoff(local_now, reminder_target_date)
                        ):
                            is_first_notification = state.last_evening_notification_date != reminder_target_date
                            pending_notifications.append(
                                (user.user_id, reminder_target_date, not is_first_notification)
                            )
                        else:
                            state.reminder_due_at = None
                        continue

                    is_target_time_reached = local_now.time() >= EVENING_ANALYSIS_TIME
                    if is_target_time_reached and state.last_evening_notification_date != local_today:
                        pending_notifications.append((user.user_id, local_today, False))

            notifications_to_send: list[tuple[str, object, bool]] = []
            for user_id, target_date, is_reminder in pending_notifications:
                decision = await self.check_day_analysis_reminder_status(user_id, target_date)
                if decision.can_send:
                    notifications_to_send.append((user_id, target_date, is_reminder))
                    continue

                if decision.reason == DayAnalysisReminderBlockReason.ALREADY_ANALYZED:
                    EveningAnalysisNotificationRepository.mark_analysis_started(user_id, target_date)
                    continue

                EveningAnalysisNotificationRepository.postpone_reminder(
                    user_id,
                    target_date,
                    self._build_busy_postpone_due_at(),
                )

            if notifications_to_send:
                logger.info("Отправка вечерних уведомлений анализа дня: %s", len(notifications_to_send))
                results = await asyncio.gather(
                    *(
                        self.send_evening_analysis_notification(user_id, target_date, is_reminder=is_reminder)
                        for user_id, target_date, is_reminder in notifications_to_send
                    ),
                    return_exceptions=True,
                )
                for (user_id, target_date, is_reminder), result in zip(notifications_to_send, results):
                    if result is not True:
                        logger.warning(
                            "Вечернее уведомление анализа дня не доставлено, повторим позже: user_id=%s",
                            user_id,
                        )
                        continue
                    if is_reminder:
                        EveningAnalysisNotificationRepository.mark_reminder_sent(user_id, target_date)
                    else:
                        EveningAnalysisNotificationRepository.mark_evening_notification_sent(user_id, target_date)
        except Exception as e:
            logger.error("Ошибка при проверке вечерних уведомлений анализа дня: %s", e, exc_info=True)

    async def evening_analysis_notification_loop(self):
        """Цикл проверки вечерних уведомлений ИИ-анализа дня каждую минуту."""
        while self.running:
            try:
                await self.check_and_send_evening_analysis_notifications()
                await asyncio.sleep(60)
            except asyncio.CancelledError:
                logger.info("Цикл вечерних уведомлений анализа дня остановлен")
                break
            except Exception as e:
                logger.error("Ошибка в цикле вечерних уведомлений анализа дня: %s", e, exc_info=True)
                await asyncio.sleep(60)
    
    def _get_weekday_name(self, weekday: int) -> str:
        """Преобразует номер дня недели (0=Понедельник) в русское сокращение."""
        weekday_names = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
        return weekday_names[weekday]
    
    def calculate_next_time(self, target_time: time) -> float:
        """Вычисляет время до следующего указанного времени в секундах."""
        now = datetime.now(MSK_TZ)
        target_datetime = datetime.combine(now.date(), target_time)
        target_datetime = target_datetime.replace(tzinfo=MSK_TZ)
        
        # Если время уже прошло сегодня, планируем на завтра
        if now.time() >= target_time:
            from datetime import timedelta
            target_datetime += timedelta(days=1)
        
        delta = (target_datetime - now).total_seconds()
        return delta
    
    async def schedule_daily_notification(self, target_time: time, meal_type: str, message_text: str):
        """Планирует ежедневное уведомление на указанное время."""
        meal_type_prepositional = MEAL_TYPE_PREPOSITIONAL.get(meal_type, meal_type)
        while self.running:
            try:
                # Вычисляем время до следующего указанного времени
                wait_seconds = self.calculate_next_time(target_time)
                
                logger.info(
                    f"Следующее уведомление о {meal_type_prepositional} будет отправлено через "
                    f"{wait_seconds / 3600:.2f} часов (в {target_time})"
                )
                
                # Ждём до указанного времени
                await asyncio.sleep(wait_seconds)
                
                # Отправляем уведомления
                await self.send_meal_notifications(meal_type, message_text)
                
                # Ждём 1 секунду перед следующей итерацией (чтобы не отправлять дважды)
                await asyncio.sleep(1)
                
            except asyncio.CancelledError:
                logger.info(f"Планировщик уведомлений о {meal_type_prepositional} остановлен")
                break
            except Exception as e:
                logger.error(
                    f"Ошибка в планировщике уведомлений о {meal_type_prepositional}: {e}"
                )
                # В случае ошибки ждём минуту перед повтором
                await asyncio.sleep(60)
    
    async def start(self):
        """Запускает планировщик уведомлений."""
        self.running = True
        logger.info("Запуск планировщика уведомлений о приёмах пищи и добавках")
        
        # Создаём задачи для каждого времени приёма пищи
        tasks = [
            self.schedule_daily_notification(
                time(10, 0),
                "завтрак",
                "Добавьте завтрак и Вы на один шаг приблизитесь к цели!"
            ),
            self.schedule_daily_notification(
                time(14, 0),
                "обед",
                "Добавьте обед и Вы на один шаг приблизитесь к цели!"
            ),
            self.schedule_daily_notification(
                time(20, 0),
                "ужин",
                "Добавьте ужин и Вы на один шаг приблизитесь к цели!"
            ),
            # Запускаем цикл проверки уведомлений о добавках
            self.supplement_notification_loop(),
            # Запускаем независимый цикл вечерних уведомлений анализа дня
            self.evening_analysis_notification_loop(),
        ]
        
        # Запускаем все задачи параллельно
        await asyncio.gather(*tasks, return_exceptions=True)
    
    async def check_and_send_supplement_notifications(self):
        """Проверяет добавки и отправляет уведомления, если наступило время приёма."""
        try:
            now = datetime.now(MSK_TZ)
            current_time_str = now.strftime("%H:%M")
            current_weekday = self._get_weekday_name(now.weekday())
            today_date = now.date()
            
            # Сбрасываем кэш отправленных уведомлений в начале нового дня
            if self._last_check_date is None or self._last_check_date != today_date:
                self.sent_notifications_today.clear()
                self._last_check_date = today_date
            
            with get_db_session() as session:
                # Получаем все добавки с включенными уведомлениями только у пользователей,
                # завершивших обязательный онбординг.
                supplements = session.query(Supplement).filter(
                    Supplement.notifications_enabled.is_(True),
                    session.query(KbjuSettings.id)
                    .filter(KbjuSettings.user_id == Supplement.user_id)
                    .exists(),
                ).all()
                
                for supplement in supplements:
                    try:
                        # Парсим дни и время
                        days = json.loads(supplement.days_json or "[]")
                        times = json.loads(supplement.times_json or "[]")
                        
                        # Проверяем, нужно ли отправлять уведомление
                        if not days or not times:
                            continue
                        
                        # Проверяем день недели
                        if current_weekday not in days:
                            continue
                        
                        # Проверяем время (с точностью до минуты)
                        if current_time_str not in times:
                            continue
                        
                        # Создаём уникальный ключ для уведомления
                        notification_key = f"{supplement.user_id}_{supplement.id}_{current_time_str}_{today_date}"
                        
                        # Проверяем, не отправляли ли уже это уведомление сегодня
                        if notification_key in self.sent_notifications_today:
                            continue
                        
                        # Отправляем уведомление
                        message = (
                            "🔔 Время принять добавку!\n\n"
                            f"💊 {supplement.name}\n"
                            f"⏰ {current_time_str}\n\n"
                            "Нажми «✅ Подтвердить прием», когда примешь добавку, "
                            "или «⏰ Напомнить позже»."
                        )
                        await self.send_notification(
                            supplement.user_id,
                            message,
                            reply_markup=build_supplement_notification_keyboard(
                                supplement.id,
                                current_time_str,
                            ),
                        )
                        
                        # Помечаем уведомление как отправленное
                        self.sent_notifications_today.add(notification_key)
                        logger.info(
                            f"Отправлено уведомление о добавке {supplement.name} "
                            f"пользователю {supplement.user_id} в {current_time_str}"
                        )
                    except Exception as e:
                        logger.error(
                            f"Ошибка при проверке добавки {supplement.id} "
                            f"для пользователя {supplement.user_id}: {e}",
                            exc_info=True
                        )
        except Exception as e:
            logger.error(f"Ошибка при проверке уведомлений о добавках: {e}", exc_info=True)
    
    async def supplement_notification_loop(self):
        """Цикл проверки уведомлений о добавках каждую минуту."""
        while self.running:
            try:
                await self.check_and_send_supplement_notifications()
                # Проверяем каждую минуту
                await asyncio.sleep(60)
            except asyncio.CancelledError:
                logger.info("Цикл проверки уведомлений о добавках остановлен")
                break
            except Exception as e:
                logger.error(f"Ошибка в цикле проверки уведомлений о добавках: {e}", exc_info=True)
                # В случае ошибки ждём минуту перед повтором
                await asyncio.sleep(60)
    
    def stop(self):
        """Останавливает планировщик уведомлений."""
        self.running = False
        logger.info("Планировщик уведомлений остановлен")
