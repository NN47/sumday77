"""Сервис для отправки запланированных уведомлений."""
import asyncio
import logging
import json
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo
from aiogram import Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from database.session import get_db_session
from database.models import ActivityAnalysisEntry, User, Supplement, KbjuSettings, EveningAnalysisNotificationState
from services.error_logging_service import log_app_error

logger = logging.getLogger(__name__)
MSK_TZ = ZoneInfo("Europe/Moscow")
MEAL_TYPE_PREPOSITIONAL = {
    "завтрак": "завтраке",
    "обед": "обеде",
    "ужин": "ужине",
}
EVENING_ANALYSIS_TIME = time(21, 45)
EVENING_ANALYSIS_REMINDER_DELAY = timedelta(minutes=45)
EVENING_ANALYSIS_MAX_REMINDERS = 2
EVENING_ANALYSIS_START_PREFIX = "evening_analysis_start"
EVENING_ANALYSIS_REMIND_PREFIX = "evening_analysis_remind"
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


class NotificationScheduler:
    """Планировщик уведомлений о приёмах пищи и добавках."""
    
    def __init__(self, bot: Bot):
        self.bot = bot
        self.running = False
        self.sent_notifications_today = set()  # Для предотвращения дублирования уведомлений
        self._last_check_date = None  # Дата последней проверки для сброса кэша
        
    async def send_notification(
        self,
        user_id: str,
        message: str,
        reply_markup: InlineKeyboardMarkup | None = None,
    ):
        """Отправляет уведомление пользователю."""
        try:
            await self.bot.send_message(
                chat_id=user_id,
                text=message,
                reply_markup=reply_markup,
            )
            logger.info(f"Уведомление отправлено пользователю {user_id}")
        except Exception as e:
            log_app_error(
                source="telegram",
                error=e,
                user_id=str(user_id),
                context="send_message",
                extra={"message_preview": message[:80]},
            )
    
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

    async def send_evening_analysis_notification(self, user_id: str, target_date, *, is_reminder: bool = False):
        """Отправляет основное или повторное уведомление анализа дня."""
        text = EVENING_ANALYSIS_REMINDER_TEXT if is_reminder else EVENING_ANALYSIS_MAIN_TEXT
        await self.send_notification(
            user_id,
            text,
            reply_markup=self.build_evening_analysis_keyboard(target_date),
        )

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
                    user_tz = self._get_user_timezone(user.timezone)
                    local_now = datetime.now(user_tz)
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
                        if state.remind_later_date == local_today and state.remind_later_count <= EVENING_ANALYSIS_MAX_REMINDERS:
                            pending_notifications.append((user.user_id, local_today, True))
                            state.reminder_due_at = None
                            state.updated_at = now_utc
                        continue

                    is_target_minute = (
                        local_now.hour == EVENING_ANALYSIS_TIME.hour
                        and local_now.minute == EVENING_ANALYSIS_TIME.minute
                    )
                    if is_target_minute and state.last_evening_notification_date != local_today:
                        state.last_evening_notification_date = local_today
                        state.remind_later_date = local_today
                        state.remind_later_count = 0
                        state.reminder_due_at = None
                        state.updated_at = now_utc
                        pending_notifications.append((user.user_id, local_today, False))

            if pending_notifications:
                logger.info("Отправка вечерних уведомлений анализа дня: %s", len(pending_notifications))
                tasks = [
                    self.send_evening_analysis_notification(user_id, target_date, is_reminder=is_reminder)
                    for user_id, target_date, is_reminder in pending_notifications
                ]
                await asyncio.gather(*tasks, return_exceptions=True)
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
                            "Нажмите кнопку после приёма:"
                        )
                        confirm_markup = InlineKeyboardMarkup(
                            inline_keyboard=[
                                [
                                    InlineKeyboardButton(
                                        text="✅ Подтвердить прием",
                                        callback_data=(
                                            f"sup_confirm:{supplement.id}:{current_time_str}"
                                        ),
                                    )
                                ]
                            ]
                        )
                        await self.send_notification(
                            supplement.user_id,
                            message,
                            reply_markup=confirm_markup,
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
