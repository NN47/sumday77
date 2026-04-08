"""Репозиторий статистики и состояния Gemini-аккаунтов."""
from __future__ import annotations

from datetime import datetime, date

from sqlalchemy import func

from database.models import GeminiAccount, GeminiRequestLog
from database.session import get_db_session


class GeminiRepository:
    """Централизованное хранение состояния и статистики Gemini."""

    @staticmethod
    def mask_api_key(api_key: str) -> str:
        key = (api_key or "").strip()
        if len(key) <= 10:
            return key[:3] + "..."
        return f"{key[:6]}...{key[-4:]}"

    @staticmethod
    def sync_accounts(account_configs: list[dict]) -> None:
        """Создаёт/обновляет аккаунты из конфигурации окружения."""
        if not account_configs:
            return

        with get_db_session() as session:
            existing = {acc.account_name: acc for acc in session.query(GeminiAccount).all()}

            for config in account_configs:
                name = config["account_name"]
                masked = GeminiRepository.mask_api_key(config["api_key"])
                priority_order = int(config["priority_order"])

                if name in existing:
                    account = existing[name]
                    account.api_key_masked = masked
                    account.priority_order = priority_order
                    account.updated_at = datetime.utcnow()
                else:
                    session.add(
                        GeminiAccount(
                            account_name=name,
                            api_key_masked=masked,
                            priority_order=priority_order,
                            is_active=False,
                        )
                    )

            session.flush()
            ordered = (
                session.query(GeminiAccount)
                .order_by(GeminiAccount.priority_order.asc(), GeminiAccount.id.asc())
                .all()
            )
            if ordered and not any(acc.is_active for acc in ordered):
                ordered[0].is_active = True
                ordered[0].updated_at = datetime.utcnow()

    @staticmethod
    def get_accounts() -> list[GeminiAccount]:
        with get_db_session() as session:
            return (
                session.query(GeminiAccount)
                .order_by(GeminiAccount.priority_order.asc(), GeminiAccount.id.asc())
                .all()
            )

    @staticmethod
    def get_active_account() -> GeminiAccount | None:
        with get_db_session() as session:
            return (
                session.query(GeminiAccount)
                .filter(GeminiAccount.is_active.is_(True))
                .order_by(GeminiAccount.priority_order.asc())
                .first()
            )

    @staticmethod
    def switch_to_next_account(current_account_id: int) -> GeminiAccount | None:
        with get_db_session() as session:
            accounts = (
                session.query(GeminiAccount)
                .order_by(GeminiAccount.priority_order.asc(), GeminiAccount.id.asc())
                .all()
            )
            if not accounts:
                return None

            current_index = 0
            for index, account in enumerate(accounts):
                if account.id == current_account_id:
                    current_index = index
                    break

            next_index = (current_index + 1) % len(accounts)
            next_account = accounts[next_index]

            for account in accounts:
                account.is_active = account.id == next_account.id
                account.updated_at = datetime.utcnow()

            return next_account

    @staticmethod
    def increment_account_stats(
        account_id: int,
        *,
        status: str,
        model_name: str,
        error_message: str | None = None,
    ) -> None:
        """Обновляет счётчики аккаунта и добавляет запись в лог запроса."""
        with get_db_session() as session:
            account = session.query(GeminiAccount).filter(GeminiAccount.id == account_id).first()
            if not account:
                return

            now = datetime.utcnow()
            account.total_requests = int(account.total_requests or 0) + 1
            account.last_request_at = now
            account.updated_at = now

            if status == "success":
                account.success_requests = int(account.success_requests or 0) + 1
            else:
                account.error_requests = int(account.error_requests or 0) + 1
                account.last_error_at = now
                account.last_error_message = (error_message or "")[:1000]
                if status == "limit_exceeded":
                    account.limit_switches = int(account.limit_switches or 0) + 1

            session.add(
                GeminiRequestLog(
                    account_id=account.id,
                    status=status,
                    model_name=model_name,
                    error_message=(error_message or "")[:1000] if error_message else None,
                )
            )

    @staticmethod
    def get_metrics() -> dict:
        today_start = datetime.combine(date.today(), datetime.min.time())

        with get_db_session() as session:
            accounts = (
                session.query(GeminiAccount)
                .order_by(GeminiAccount.priority_order.asc(), GeminiAccount.id.asc())
                .all()
            )
            total_requests_all_time = int(sum(int(acc.total_requests or 0) for acc in accounts))
            total_limit_switches = int(sum(int(acc.limit_switches or 0) for acc in accounts))
            active = next((acc for acc in accounts if acc.is_active), None)

            total_requests_today = (
                session.query(func.count(GeminiRequestLog.id))
                .filter(GeminiRequestLog.created_at >= today_start)
                .scalar()
                or 0
            )

            recent_events = (
                session.query(GeminiRequestLog, GeminiAccount.account_name)
                .join(GeminiAccount, GeminiAccount.id == GeminiRequestLog.account_id)
                .order_by(GeminiRequestLog.created_at.desc())
                .limit(10)
                .all()
            )

        return {
            "active_account": active,
            "total_requests_today": int(total_requests_today),
            "total_requests_all_time": total_requests_all_time,
            "total_limit_switches": total_limit_switches,
            "accounts": accounts,
            "recent_events": [
                {
                    "account_name": account_name,
                    "status": log.status,
                    "model_name": log.model_name,
                    "error_message": log.error_message,
                    "created_at": log.created_at,
                }
                for log, account_name in recent_events
            ],
        }
