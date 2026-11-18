"""指标定义业务规则：状态流转、字段校验与筛选口径都收在这里。"""
from __future__ import annotations

from typing import Any

from app.store import store

MODULE = "metric_def"
LIST_FIELDS = ["指标编号", "指标名称", "计算公式", "统计粒度", "时间窗口", "基准取值", "精度位数", "口径版本"]
REQUIRED_FIELDS = ["指标编号", "指标名称", "计算公式"]
STATUS_ORDER = ["草稿", "待校验", "已生效", "已废弃"]
ACTION_RULES = {"提交校验": "待校验", "标记生效": "已生效", "标记废弃": "已废弃"}
NEGATIVE_ACTIONS = []


class MetricDefService:
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
            rows = [row for row in rows if keyword.casefold() in str(row.get("指标编号", "")).casefold()]
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
            return None, f"指标定义 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于指标定义可执行范围"
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"
        entry["status"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        return entry, f"指标定义已{action}"

    def validate_retention_columns(self, entry_id: int) -> tuple[dict[str, Any] | None, str]:
        """跨模块联动（V001）：读取留存矩阵数据表的指标编号和周期口径，校验每列留存率。

        只读取 V001 核对通过的确认结果；每列留存率必须在 [0,1] 且能回溯到同期群定义。
        """
        from app.services.retention_matrix import ARCHIVE_VERSION, COHORT_MODULE, PERIOD_COLUMNS

        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"指标定义 {entry_id} 不存在或已归档"
        metric_code = str(entry.get("指标编号", ""))
        matrices = [
            row for row in store.rows("retention_matrix")
            if row.get("指标编号") == metric_code
            and row.get("status") in ("已确认", "已停用")
            and not row.get("残留")
        ]
        if not matrices:
            return None, f"指标 {metric_code} 暂无 {ARCHIVE_VERSION} 核对通过的留存矩阵"
        problems: list[str] = []
        checked = 0
        for matrix in matrices:
            caliber = str(matrix.get("周期口径") or "").strip()
            if not caliber:
                problems.append(f"{matrix.get('矩阵编号')} 周期口径未登记")
            cohort = store.find_by_field(COHORT_MODULE, "群组编号", matrix.get("群组编号"))
            if cohort is None:
                problems.append(f"{matrix.get('矩阵编号')} 无法回溯同期群定义 {matrix.get('群组编号')}")
            details = {d.get("周期"): d for d in matrix.get("留存明细", [])}
            for period in PERIOD_COLUMNS:
                detail = details.get(period)
                if detail is None:
                    problems.append(f"{matrix.get('矩阵编号')} {period}缺失，矩阵显示不完整")
                    continue
                checked += 1
                rate = detail.get("留存率")
                if rate is None or not (0 <= float(rate) <= 1):
                    problems.append(f"{matrix.get('矩阵编号')} {period}留存率越界：{rate}")
                if int(detail.get("分母样本", 0)) <= 0:
                    problems.append(f"{matrix.get('矩阵编号')} {period}分母样本为空")
        result = {
            "指标编号": metric_code,
            "矩阵数": len(matrices),
            "校验列数": checked,
            "问题": problems,
            "核对版本": ARCHIVE_VERSION,
        }
        if problems:
            return result, f"指标 {metric_code} 留存矩阵校验未通过：{'；'.join(problems)}"
        return result, f"指标 {metric_code} 的 {checked} 列留存率全部通过 {ARCHIVE_VERSION} 校验"
