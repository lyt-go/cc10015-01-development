"""异常检测业务规则：状态流转、字段校验与筛选口径都收在这里。"""
from __future__ import annotations

from typing import Any

from app.store import store

MODULE = "anomaly_detect"
LIST_FIELDS = ["规则编号", "监控指标", "检测算法", "基线窗口", "波动阈值", "告警级别", "静默时段", "规则版本"]
REQUIRED_FIELDS = ["规则编号", "监控指标", "检测算法"]
STATUS_ORDER = ["草稿", "待校验", "已生效", "已停用"]
ACTION_RULES = {"提交校验": "待校验", "标记生效": "已生效", "停用规则": "已停用"}
NEGATIVE_ACTIONS = ["停用规则"]


class AnomalyDetectService:
    def list_entries(
        self,
        *,
        filters: dict[str, str] | None = None,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(MODULE)
        for field, expected in (filters or {}).items():
            rows = [row for row in rows if expected.casefold() in str(row.get(field, "")).casefold()]
        if keyword:
            rows = [row for row in rows if keyword.casefold() in str(row.get("规则编号", "")).casefold()]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = (page - 1) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        return store.find(MODULE, entry_id)

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update({field: values.get(field) for field in LIST_FIELDS})
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        rows.append(entry)
        return entry, []

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"检测规则 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于异常检测可执行范围"
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"
        entry["status"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        return entry, f"检测规则已{action}"

    def run_detection(self, entry_id: int, values: list[float]) -> dict[str, Any]:
        """对历史数据执行异常检测：计算移动平均和标准差，判断最新值是否异常。"""
        entry = self.get_entry(entry_id)
        if not entry:
            return {"ok": False, "message": "规则不存在"}
        if len(values) < 3:
            return {"ok": True, "message": "数据不足", "is_anomaly": False}
        try:
            window = int(entry.get("基线窗口", "7"))
        except Exception:
            window = 7
        try:
            threshold = float(entry.get("波动阈值", "0.3"))
        except Exception:
            threshold = 0.3
        # 使用最近window个点计算基线
        baseline = values[-window-1:-1] if len(values) > window else values[:-1]
        latest = values[-1]
        mean = sum(baseline) / len(baseline)
        variance = sum((x - mean) ** 2 for x in baseline) / len(baseline)
        std = variance ** 0.5
        deviation = abs(latest - mean) / std if std > 0 else 0
        is_anomaly = deviation > threshold
        return {"ok": True, "mean": round(mean, 4), "std": round(std, 4),
                "latest": latest, "deviation": round(deviation, 4),
                "threshold": threshold, "is_anomaly": is_anomaly,
                "message": f"异常检测完成，偏差={deviation:.4f}"}
