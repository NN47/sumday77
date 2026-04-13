"""Репозиторий статистики и состояния Gemini-аккаунтов."""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import func, case

from database.models import GeminiAccount, GeminiRequestLog
from database.session import get_db_session
from time_utils import UTC_TZ, now_moscow, to_moscow


class GeminiRepository:
    """Централизованное хранение состояния и статистики Gemini."""

    STATUS_ACTIVE = "active"
    STATUS_COOLDOWN = "cooldown"
    STATUS_RATE_LIMITED = "rate_limited"
    STATUS_AUTH_FAILED = "auth_failed"
    STATUS_DISABLED = "disabled"
    FINAL_USER_EVENTS = {"user_request_started", "request_finished_success", "request_finished_failed"}

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
                    if not account.status:
                        account.status = GeminiRepository.STATUS_ACTIVE
                    account.updated_at = datetime.utcnow()
                else:
                    session.add(
                        GeminiAccount(
                            account_name=name,
                            api_key_masked=masked,
                            priority_order=priority_order,
                            is_active=False,
                            status=GeminiRepository.STATUS_ACTIVE,
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
    def _is_available(account: GeminiAccount, now: datetime | None = None) -> bool:
        now = now or datetime.utcnow()
        if account.status in {GeminiRepository.STATUS_AUTH_FAILED, GeminiRepository.STATUS_DISABLED}:
            return False
        if account.status == GeminiRepository.STATUS_COOLDOWN and account.temporary_unavailable_until:
            return account.temporary_unavailable_until <= now
        if account.status == GeminiRepository.STATUS_RATE_LIMITED and account.rate_limited_until:
            return account.rate_limited_until <= now
        return True

    @staticmethod
    def _activate_account(session, account_id: int) -> GeminiAccount | None:
        account = session.query(GeminiAccount).filter(GeminiAccount.id == account_id).first()
        if not account:
            return None

        for candidate in session.query(GeminiAccount).all():
            candidate.is_active = candidate.id == account_id
            candidate.updated_at = datetime.utcnow()

        if account.status in {GeminiRepository.STATUS_COOLDOWN, GeminiRepository.STATUS_RATE_LIMITED}:
            account.status = GeminiRepository.STATUS_ACTIVE
        return account

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
            active = (
                session.query(GeminiAccount)
                .filter(GeminiAccount.is_active.is_(True))
                .order_by(GeminiAccount.priority_order.asc())
                .first()
            )
            if not active:
                return None
            if GeminiRepository._is_available(active):
                if active.status in {GeminiRepository.STATUS_COOLDOWN, GeminiRepository.STATUS_RATE_LIMITED}:
                    active.status = GeminiRepository.STATUS_ACTIVE
                    active.updated_at = datetime.utcnow()
                return active
            return None

    @staticmethod
    def select_next_available_account(
        *,
        current_account_id: int | None = None,
        excluded_account_ids: set[int] | None = None,
    ) -> GeminiAccount | None:
        excluded = excluded_account_ids or set()
        now = datetime.utcnow()

        with get_db_session() as session:
            accounts = (
                session.query(GeminiAccount)
                .order_by(GeminiAccount.priority_order.asc(), GeminiAccount.id.asc())
                .all()
            )
            if not accounts:
                return None

            start_index = 0
            if current_account_id is not None:
                for index, account in enumerate(accounts):
                    if account.id == current_account_id:
                        start_index = index
                        break

            ordered_candidates = accounts[start_index:] + accounts[:start_index]
            for candidate in ordered_candidates:
                if candidate.id in excluded:
                    continue
                if GeminiRepository._is_available(candidate, now=now):
                    if candidate.status in {GeminiRepository.STATUS_COOLDOWN, GeminiRepository.STATUS_RATE_LIMITED}:
                        candidate.status = GeminiRepository.STATUS_ACTIVE
                    return GeminiRepository._activate_account(session, candidate.id)
            return None

    @staticmethod
    def switch_to_next_available_account(
        current_account_id: int,
        *,
        reason: str,
        model_name: str,
        error_message: str | None = None,
        excluded_account_ids: set[int] | None = None,
    ) -> GeminiAccount | None:
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

            now = datetime.utcnow()
            excluded = excluded_account_ids or set()
            ordered_candidates = accounts[current_index + 1 :] + accounts[: current_index + 1]

            for candidate in ordered_candidates:
                if candidate.id in excluded:
                    continue
                if not GeminiRepository._is_available(candidate, now=now):
                    continue

                for account in accounts:
                    account.is_active = account.id == candidate.id
                    account.updated_at = now

                session.add(
                    GeminiRequestLog(
                        account_id=candidate.id,
                        status=reason,
                        event_type=reason,
                        reason=reason,
                        model_name=model_name,
                        error_message=(error_message or "")[:1000] if error_message else None,
                    )
                )
                return candidate

            return None

    @staticmethod
    def record_request_success(account_id: int, *, model_name: str) -> None:
        with get_db_session() as session:
            account = session.query(GeminiAccount).filter(GeminiAccount.id == account_id).first()
            if not account:
                return

            now = datetime.utcnow()
            account.total_requests = int(account.total_requests or 0) + 1
            account.success_requests = int(account.success_requests or 0) + 1
            account.last_request_at = now
            account.updated_at = now
            if account.status in {GeminiRepository.STATUS_COOLDOWN, GeminiRepository.STATUS_RATE_LIMITED}:
                account.status = GeminiRepository.STATUS_ACTIVE

            session.add(
                GeminiRequestLog(
                    account_id=account.id,
                    status="request_success",
                    event_type="request_success",
                    reason="success",
                    model_name=model_name,
                )
            )

    @staticmethod
    def _get_fallback_account_id(session) -> int | None:
        fallback = (
            session.query(GeminiAccount)
            .order_by(GeminiAccount.priority_order.asc(), GeminiAccount.id.asc())
            .first()
        )
        return fallback.id if fallback else None

    @staticmethod
    def _log_without_account(
        session,
        *,
        event_type: str,
        status: str,
        model_name: str | None = None,
        reason: str | None = None,
        error_message: str | None = None,
    ) -> None:
        account_id = GeminiRepository._get_fallback_account_id(session)
        if not account_id:
            return
        session.add(
            GeminiRequestLog(
                account_id=account_id,
                status=status,
                event_type=event_type,
                reason=reason,
                model_name=model_name,
                error_message=(error_message or "")[:1000] if error_message else None,
            )
        )

    @staticmethod
    def log_user_request_started(*, model_name: str) -> None:
        with get_db_session() as session:
            GeminiRepository._log_without_account(
                session,
                event_type="user_request_started",
                status="user_request_started",
                model_name=model_name,
                reason="user_request",
            )

    @staticmethod
    def log_api_attempt(
        *,
        account_id: int,
        model_name: str,
        api_attempt_number: int,
        key_attempt_number: int,
    ) -> None:
        with get_db_session() as session:
            session.add(
                GeminiRequestLog(
                    account_id=account_id,
                    status="api_attempt",
                    event_type="api_attempt",
                    reason=f"attempt#{api_attempt_number}/key_attempt#{key_attempt_number}",
                    model_name=model_name,
                )
            )

    @staticmethod
    def log_retry_scheduled(
        *,
        account_id: int,
        model_name: str,
        retry_number: int,
        delay_seconds: float,
        error_message: str | None = None,
    ) -> None:
        with get_db_session() as session:
            session.add(
                GeminiRequestLog(
                    account_id=account_id,
                    status="retry_temporary_error",
                    event_type="retry_temporary_error",
                    reason=f"retry#{retry_number} delay={delay_seconds:.2f}s",
                    model_name=model_name,
                    error_message=(error_message or "")[:1000] if error_message else None,
                )
            )

    @staticmethod
    def log_request_failed(
        *,
        account_id: int,
        model_name: str,
        error_type: str,
        error_message: str,
    ) -> None:
        with get_db_session() as session:
            session.add(
                GeminiRequestLog(
                    account_id=account_id,
                    status="request_failed",
                    event_type="request_failed",
                    reason=error_type,
                    model_name=model_name,
                    error_message=(error_message or "")[:1000],
                )
            )

    @staticmethod
    def log_user_request_finished(
        *,
        status: str,
        model_name: str,
        attempts: int,
        retries: int,
        error_type: str | None = None,
        error_message: str | None = None,
    ) -> None:
        with get_db_session() as session:
            GeminiRepository._log_without_account(
                session,
                event_type=status,
                status=status,
                model_name=model_name,
                reason=f"attempts={attempts};retries={retries};error={error_type or 'none'}",
                error_message=error_message,
            )

    @staticmethod
    def record_key_error(
        account_id: int,
        *,
        error_type: str,
        model_name: str,
        error_message: str,
    ) -> None:
        with get_db_session() as session:
            account = session.query(GeminiAccount).filter(GeminiAccount.id == account_id).first()
            if not account:
                return

            now = datetime.utcnow()
            account.total_requests = int(account.total_requests or 0) + 1
            account.error_requests = int(account.error_requests or 0) + 1
            account.last_request_at = now
            account.last_error_at = now
            account.last_error_message = (error_message or "")[:1000]
            account.last_error_type = error_type
            account.updated_at = now

            if error_type == "temporary":
                account.temporary_errors_count = int(account.temporary_errors_count or 0) + 1
                status = "retry_temporary_error"
            elif error_type == "quota":
                account.quota_errors_count = int(account.quota_errors_count or 0) + 1
                status = "quota_error"
            elif error_type == "auth":
                account.auth_errors_count = int(account.auth_errors_count or 0) + 1
                status = "auth_error"
            else:
                account.unknown_errors_count = int(account.unknown_errors_count or 0) + 1
                status = "unknown_error"

            session.add(
                GeminiRequestLog(
                    account_id=account.id,
                    status=status,
                    event_type=status,
                    reason=error_type,
                    model_name=model_name,
                    error_message=(error_message or "")[:1000],
                )
            )

    @staticmethod
    def mark_key_temporary_unavailable(account_id: int, *, cooldown_seconds: int, reason: str) -> None:
        with get_db_session() as session:
            account = session.query(GeminiAccount).filter(GeminiAccount.id == account_id).first()
            if not account:
                return

            until = datetime.utcnow() + timedelta(seconds=max(cooldown_seconds, 1))
            account.status = GeminiRepository.STATUS_COOLDOWN
            account.temporary_unavailable_until = until
            account.updated_at = datetime.utcnow()

            session.add(
                GeminiRequestLog(
                    account_id=account.id,
                    status="key_put_on_cooldown",
                    event_type="key_put_on_cooldown",
                    reason="temporary",
                    error_message=reason[:1000],
                )
            )

    @staticmethod
    def mark_key_rate_limited(account_id: int, *, cooldown_seconds: int, reason: str) -> None:
        with get_db_session() as session:
            account = session.query(GeminiAccount).filter(GeminiAccount.id == account_id).first()
            if not account:
                return

            until = datetime.utcnow() + timedelta(seconds=max(cooldown_seconds, 1))
            account.status = GeminiRepository.STATUS_RATE_LIMITED
            account.rate_limited_until = until
            account.limit_switches = int(account.limit_switches or 0) + 1
            account.updated_at = datetime.utcnow()

            session.add(
                GeminiRequestLog(
                    account_id=account.id,
                    status="key_rate_limited",
                    event_type="key_rate_limited",
                    reason="quota",
                    error_message=reason[:1000],
                )
            )

    @staticmethod
    def mark_key_auth_failed(account_id: int, *, reason: str) -> None:
        with get_db_session() as session:
            account = session.query(GeminiAccount).filter(GeminiAccount.id == account_id).first()
            if not account:
                return

            account.status = GeminiRepository.STATUS_AUTH_FAILED
            account.disabled_reason = reason[:500]
            account.is_active = False
            account.updated_at = datetime.utcnow()

            session.add(
                GeminiRequestLog(
                    account_id=account.id,
                    status="key_disabled_auth_error",
                    event_type="key_disabled_auth_error",
                    reason="auth",
                    error_message=reason[:1000],
                )
            )

    @staticmethod
    def increment_temporary_failover(account_id: int) -> None:
        with get_db_session() as session:
            account = session.query(GeminiAccount).filter(GeminiAccount.id == account_id).first()
            if not account:
                return
            account.temporary_failover_count = int(account.temporary_failover_count or 0) + 1
            account.updated_at = datetime.utcnow()

    @staticmethod
    def get_metrics() -> dict:
        now_msk = now_moscow()
        today_start_msk = now_msk.replace(hour=0, minute=0, second=0, microsecond=0)
        today_start_utc = today_start_msk.astimezone(UTC_TZ).replace(tzinfo=None)

        with get_db_session() as session:
            accounts = (
                session.query(GeminiAccount)
                .order_by(GeminiAccount.priority_order.asc(), GeminiAccount.id.asc())
                .all()
            )
            total_requests_all_time = int(sum(int(acc.total_requests or 0) for acc in accounts))
            total_limit_switches = int(sum(int(acc.limit_switches or 0) for acc in accounts))
            total_temporary_failovers = int(sum(int(acc.temporary_failover_count or 0) for acc in accounts))
            active = next((acc for acc in accounts if acc.is_active), None)

            total_user_requests_today = (
                session.query(func.count(GeminiRequestLog.id))
                .filter(GeminiRequestLog.created_at >= today_start_utc)
                .filter(GeminiRequestLog.event_type == "user_request_started")
                .scalar()
                or 0
            )
            total_api_attempts_today = (
                session.query(func.count(GeminiRequestLog.id))
                .filter(GeminiRequestLog.created_at >= today_start_utc)
                .filter(GeminiRequestLog.event_type == "api_attempt")
                .scalar()
                or 0
            )
            retries_today = (
                session.query(func.count(GeminiRequestLog.id))
                .filter(GeminiRequestLog.created_at >= today_start_utc)
                .filter(GeminiRequestLog.event_type == "retry_temporary_error")
                .scalar()
                or 0
            )
            successful_requests_today = (
                session.query(func.count(GeminiRequestLog.id))
                .filter(GeminiRequestLog.created_at >= today_start_utc)
                .filter(GeminiRequestLog.event_type == "request_success")
                .scalar()
                or 0
            )
            failed_requests_today = (
                session.query(func.count(GeminiRequestLog.id))
                .filter(GeminiRequestLog.created_at >= today_start_utc)
                .filter(GeminiRequestLog.event_type == "request_failed")
                .scalar()
                or 0
            )
            failovers_due_to_quota_today = (
                session.query(func.count(GeminiRequestLog.id))
                .filter(GeminiRequestLog.created_at >= today_start_utc)
                .filter(GeminiRequestLog.event_type == "switch_due_to_quota")
                .scalar()
                or 0
            )
            failovers_due_to_temporary_today = (
                session.query(func.count(GeminiRequestLog.id))
                .filter(GeminiRequestLog.created_at >= today_start_utc)
                .filter(GeminiRequestLog.event_type == "switch_due_to_temporary_failure")
                .scalar()
                or 0
            )
            failovers_due_to_auth_today = (
                session.query(func.count(GeminiRequestLog.id))
                .filter(GeminiRequestLog.created_at >= today_start_utc)
                .filter(GeminiRequestLog.event_type == "switch_due_to_auth_error")
                .scalar()
                or 0
            )
            account_today_rows = (
                session.query(
                    GeminiRequestLog.account_id,
                    func.sum(case((GeminiRequestLog.event_type == "api_attempt", 1), else_=0)).label("api_attempts"),
                    func.sum(case((GeminiRequestLog.event_type == "request_success", 1), else_=0)).label("success"),
                    func.sum(case((GeminiRequestLog.event_type == "request_failed", 1), else_=0)).label("failed"),
                    func.sum(case((GeminiRequestLog.event_type == "retry_temporary_error", 1), else_=0)).label("retries"),
                    func.count(GeminiRequestLog.id).label("total_events"),
                )
                .filter(GeminiRequestLog.created_at >= today_start_utc)
                .group_by(GeminiRequestLog.account_id)
                .all()
            )
            account_today_metrics = {
                int(row.account_id): {
                    "api_attempts_today": int(row.api_attempts or 0),
                    "success_today": int(row.success or 0),
                    "errors_today": int(row.failed or 0),
                    "retries_today": int(row.retries or 0),
                    "has_data_today": int(row.total_events or 0) > 0,
                }
                for row in account_today_rows
            }
            for account in accounts:
                snapshot = account_today_metrics.get(account.id)
                if not snapshot:
                    account.today_metrics = None
                    continue
                account.today_metrics = snapshot

                last_error_at = getattr(account, "last_error_at", None)
                cooldown_until = getattr(account, "temporary_unavailable_until", None)
                rate_limited_until = getattr(account, "rate_limited_until", None)
                if last_error_at and cooldown_until and cooldown_until < last_error_at:
                    account.temporary_unavailable_until = last_error_at
                if last_error_at and rate_limited_until and rate_limited_until < last_error_at:
                    account.rate_limited_until = last_error_at

            recent_events = (
                session.query(GeminiRequestLog, GeminiAccount.account_name)
                .join(GeminiAccount, GeminiAccount.id == GeminiRequestLog.account_id)
                .order_by(GeminiRequestLog.created_at.desc())
                .limit(10)
                .all()
            )
            last_switch_event = (
                session.query(GeminiRequestLog)
                .filter(
                    GeminiRequestLog.event_type.in_(
                        [
                            "switch_due_to_quota",
                            "switch_due_to_temporary_failure",
                            "switch_due_to_auth_error",
                        ]
                    )
                )
                .order_by(GeminiRequestLog.created_at.desc())
                .first()
            )

        return {
            "active_account": active,
            "user_requests_today": int(total_user_requests_today),
            "api_attempts_today": int(total_api_attempts_today),
            "retries_today": int(retries_today),
            "successful_requests_today": int(successful_requests_today),
            "failed_requests_today": int(failed_requests_today),
            "failovers_due_to_quota_today": int(failovers_due_to_quota_today),
            "failovers_due_to_temporary_today": int(failovers_due_to_temporary_today),
            "failovers_due_to_auth_today": int(failovers_due_to_auth_today),
            "total_requests_all_time": total_requests_all_time,
            "total_limit_switches": total_limit_switches,
            "total_temporary_failovers": total_temporary_failovers,
            "last_switch_reason": (last_switch_event.event_type if last_switch_event else "—"),
            "accounts": accounts,
            "recent_events": [
                {
                    "account_name": account_name,
                    "status": log.status,
                    "event_type": log.event_type,
                    "reason": log.reason,
                    "model_name": log.model_name,
                    "error_message": log.error_message,
                    "created_at": to_moscow(log.created_at),
                }
                for log, account_name in recent_events
            ],
        }
