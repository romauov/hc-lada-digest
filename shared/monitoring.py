"""
monitoring.py — метрики и алерты пайплайна.

Собирает статистику каждого запуска и отправляет:
  - краткий отчёт админу после каждого прогона
  - алерт при критических ошибках (граф не найден, LLM недоступен и т.д.)
"""
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)


# ── Модель метрик ──────────────────────────────────────────────────────────────

@dataclass
class PipelineMetrics:
    started_at:        str = ""
    finished_at:       str = ""
    status:            str = "ok"       # ok | warn | error
    news_total:        int = 0
    news_fresh:        int = 0
    news_deferred_in:  int = 0          # взято из deferred
    news_deferred_out: int = 0          # отложено на завтра
    entities_before:   int = 0
    entities_after:    int = 0
    graph_version:     int = 0
    digest_length:     int = 0
    llm_used:          bool = False
    source_stats:      dict = field(default_factory=dict)
    errors:            list[str] = field(default_factory=list)
    warnings:          list[str] = field(default_factory=list)

    def add_error(self, msg: str) -> None:
        self.errors.append(msg)
        self.status = "error"
        logger.error("[metrics] %s", msg)

    def add_warning(self, msg: str) -> None:
        self.warnings.append(msg)
        if self.status == "ok":
            self.status = "warn"
        logger.warning("[metrics] %s", msg)

    @property
    def duration_seconds(self) -> float:
        if not self.started_at or not self.finished_at:
            return 0.0
        try:
            fmt = "%Y-%m-%dT%H:%M:%S.%f+00:00"
            t0  = datetime.fromisoformat(self.started_at)
            t1  = datetime.fromisoformat(self.finished_at)
            return (t1 - t0).total_seconds()
        except Exception:
            return 0.0

    def new_entities_count(self) -> int:
        return max(0, self.entities_after - self.entities_before)

    def to_tg_report(self) -> str:
        """Форматирует краткий отчёт для Telegram."""
        icons = {"ok": "✅", "warn": "⚠️", "error": "❌"}
        icon  = icons.get(self.status, "❓")

        lines = [f"{icon} <b>Пайплайн завершён</b>"]

        # основные цифры
        lines.append(
            f"📰 Новостей: <b>{self.news_total}</b>"
            + (f" (+{self.news_deferred_in} из отложенных)" if self.news_deferred_in else "")
            + (f" · отложено: {self.news_deferred_out}" if self.news_deferred_out else "")
        )

        if self.new_entities_count():
            lines.append(f"🔍 Новых сущностей в графе: <b>{self.new_entities_count()}</b>")

        lines.append(f"📊 Граф: v{self.graph_version} · {self.entities_after} сущностей")
        lines.append(f"⏱ Время: {self.duration_seconds:.1f}с")

        # источники
        active = {
            name: s for name, s in self.source_stats.items()
            if s.get("requests", 0) > 0 or s.get("errors", 0) > 0
        }
        if active:
            src_parts = []
            for name, s in active.items():
                tag = "🔴" if s.get("disabled") else ("🟡" if s.get("errors") else "🟢")
                src_parts.append(f"{tag} {name}: {s.get('found', 0)} новостей")
            lines.append("Источники:\n" + "\n".join(f"  {p}" for p in src_parts))

        # предупреждения и ошибки
        if self.warnings:
            lines.append("⚠️ " + "; ".join(self.warnings[:3]))
        if self.errors:
            lines.append("❌ " + "; ".join(self.errors[:3]))

        return "\n".join(lines)


# ── Контекст-менеджер для замера времени ──────────────────────────────────────

class PipelineRun:
    """
    Контекст-менеджер: автоматически фиксирует start/finish,
    отлавливает необработанные исключения.

    Использование:
        metrics = PipelineMetrics()
        with PipelineRun(metrics):
            ... пайплайн ...
    """

    def __init__(self, metrics: PipelineMetrics):
        self.metrics = metrics

    def __enter__(self) -> PipelineMetrics:
        self.metrics.started_at = datetime.now(timezone.utc).isoformat()
        return self.metrics

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        self.metrics.finished_at = datetime.now(timezone.utc).isoformat()
        if exc_type is not None:
            self.metrics.add_error(f"{exc_type.__name__}: {exc_val}")
        return False   # не подавляем исключение


# ── Отправка отчёта ───────────────────────────────────────────────────────────

def send_monitoring_report(metrics: PipelineMetrics) -> None:
    """
    Отправляет отчёт о прогоне пайплайна админу в Telegram.
    Не бросает исключений — мониторинг не должен ронять основной процесс.
    """
    try:
        import requests
        token    = os.environ.get("TG_BOT_TOKEN", "")
        admin_id = os.environ.get("TG_ADMIN_ID", "")
        if not token or not admin_id:
            logger.debug("Monitoring report skipped: TG_ADMIN_ID not set")
            return

        report = metrics.to_tg_report()
        resp   = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={
                "chat_id":                  admin_id,
                "text":                     report,
                "parse_mode":               "HTML",
                "disable_web_page_preview": True,
            },
            timeout=10,
        )
        if not resp.ok:
            logger.warning("Monitoring report send failed: %s", resp.status_code)
    except Exception as e:
        logger.warning("Monitoring report error: %s", e)


def send_critical_alert(message: str) -> None:
    """Немедленный алерт админу при критической ошибке."""
    try:
        import requests
        token    = os.environ.get("TG_BOT_TOKEN", "")
        admin_id = os.environ.get("TG_ADMIN_ID", "")
        if not token or not admin_id:
            return
        requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={
                "chat_id":    admin_id,
                "text":       f"🚨 <b>Критическая ошибка</b>\n{message}",
                "parse_mode": "HTML",
            },
            timeout=10,
        )
    except Exception as e:
        logger.warning("Critical alert send failed: %s", e)
