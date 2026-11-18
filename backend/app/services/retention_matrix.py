"""留存矩阵数据表 业务规则：按首次行为周期计算留存率，V001 留档核对后发布。

核心约定：
- 读取同期群定义（cohort_track）与成员事件（member_event），按首次行为周期展开留存矩阵；
- 留存周期必须与成员事件口径同时登记：口径不一致的成员事件不计入对应周期，
  避免后续周期把未达标用户提前计入；
- 发布前必须通过 V001 留档核对（矩阵显示完整、每列留存率可回溯到同期群定义），
  事务存在残留时整体回滚并按 V001 留档；
- 同期群停用后保留已发布矩阵，重试只能补写缺失周期，不能覆盖完整历史；
- 停用与新周期重算形成版本冲突时，保留停用前口径并延后新周期，
  矩阵显示完整历史周期并附停用原因；
- 列表页与详情页只读取 V001 核对通过的确认结果，且只暴露一套完整周期列。
"""
from __future__ import annotations

from datetime import date
from typing import Any

from app.store import store

MODULE = "retention_matrix"
EVENT_MODULE = "member_event"
COHORT_MODULE = "cohort_track"
ARCHIVE_MODULE = "retention_archive"

# 一套完整周期列：任一时点停用群组后，留存矩阵都只暴露这一套周期列。
PERIOD_COLUMNS = ["第0期", "第1期", "第2期", "第3期"]
LIST_FIELDS = ["矩阵编号", "群组编号", "指标编号", "周期口径", "首次行为周期", "分母样本"]
REQUIRED_FIELDS = ["群组编号", "指标编号", "周期口径"]
STATUS_ORDER = ["草稿", "已确认", "已停用"]
ARCHIVE_VERSION = "V001"


def _period_members(cohort_code: str, first_period: str, period: str, caliber: str) -> set[str]:
    """某首次行为周期下、指定留存周期的达标成员集合。

    只统计周期口径与矩阵口径同时登记（一致）的成员事件；
    口径未同时登记的事件不计入，防止未达标用户被提前计入后续周期。
    """
    members: set[str] = set()
    for event in store.rows(EVENT_MODULE):
        if event.get("群组编号") != cohort_code:
            continue
        if event.get("首次行为周期") != first_period:
            continue
        if event.get("事件周期") != period:
            continue
        if event.get("周期口径") != caliber:
            continue
        members.add(str(event.get("成员ID", "")))
    members.discard("")
    return members


def _first_periods(cohort_code: str) -> list[str]:
    periods = {
        str(event.get("首次行为周期", ""))
        for event in store.rows(EVENT_MODULE)
        if event.get("群组编号") == cohort_code and event.get("首次行为周期")
    }
    return sorted(periods)


def _build_columns(cohort_code: str, first_period: str, caliber: str) -> tuple[list[dict[str, Any]], int]:
    """计算一套完整周期列；返回（留存明细， 分母样本）。分母为 0 时明细为空。"""
    base = _period_members(cohort_code, first_period, PERIOD_COLUMNS[0], caliber)
    if not base:
        return [], 0
    denominator = len(base)
    details: list[dict[str, Any]] = []
    for period in PERIOD_COLUMNS:
        retained = _period_members(cohort_code, first_period, period, caliber)
        # 留存分子必须是分母样本的子集，保证每列都能从分母样本回溯到同一同期群定义。
        retained &= base
        details.append({
            "周期": period,
            "留存人数": len(retained),
            "分母样本": denominator,
            "留存率": round(len(retained) / denominator, 4),
            "群组编号": cohort_code,
        })
    return details, denominator


def _serialize(row: dict[str, Any]) -> dict[str, Any]:
    """对外只暴露一套完整周期列；每列留存率都能回溯到同期群定义。"""
    item = {field: row.get(field) for field in ["id", *LIST_FIELDS]}
    details = {d["周期"]: d for d in row.get("留存明细", [])}
    for period in PERIOD_COLUMNS:
        item[period] = details.get(period, {}).get("留存率")
    item["status"] = row.get("status")
    item["核对留档"] = row.get("核对留档")
    item["停用时点"] = row.get("停用时点")
    item["停用原因"] = row.get("停用原因")
    item["重算状态"] = row.get("重算状态")
    return item


class RetentionMatrixService:
    # ---------- 计算 ----------

    def compute_matrix(self, values: dict[str, Any]) -> tuple[list[dict[str, Any]] | None, str]:
        """读取同期群定义和成员事件，按首次行为周期计算留存率，生成留存矩阵草稿。"""
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, f"缺少必填字段：{'、'.join(missing)}"
        cohort_code = str(values["群组编号"]).strip()
        metric_code = str(values["指标编号"]).strip()
        caliber = str(values["周期口径"]).strip()
        cohort = store.find_by_field(COHORT_MODULE, "群组编号", cohort_code)
        if cohort is None:
            return None, f"同期群定义 {cohort_code} 不存在，无法回溯"
        periods = _first_periods(cohort_code)
        if not periods:
            return None, f"同期群 {cohort_code} 暂无成员事件，无法展开留存矩阵"
        existing_confirmed = [
            row for row in store.rows(MODULE)
            if row.get("群组编号") == cohort_code and row.get("status") in ("已确认", "已停用")
        ]
        created: list[dict[str, Any]] = []
        skipped: list[str] = []
        for first_period in periods:
            details, denominator = _build_columns(cohort_code, first_period, caliber)
            if not details:
                # 留存周期未与成员事件口径同时登记，该周期不生成，避免提前计入未达标用户。
                skipped.append(first_period)
                continue
            entry: dict[str, Any] = {
                "id": store.next_id(MODULE),
                "矩阵编号": f"RETM-{store.next_id(MODULE):04d}",
                "群组编号": cohort_code,
                "指标编号": metric_code,
                "周期口径": caliber,
                "首次行为周期": first_period,
                "分母样本": denominator,
                "留存明细": details,
                "status": STATUS_ORDER[0],
                "pending": True,
                "abnormal": False,
                "核对留档": None,
                "停用时点": None,
                "停用原因": None,
                "重算状态": "待重算" if existing_confirmed else None,
                "created_at": date.today().isoformat(),
            }
            store.rows(MODULE).append(entry)
            created.append(entry)
            if str(values.get("模拟残留") or "").strip() in ("1", "true", "是"):
                # 演示/测试钩子：模拟事务部分写入留下的残留行，V001 核对时应整体回滚。
                residue = dict(entry)
                residue["id"] = store.next_id(MODULE)
                residue["留存明细"] = details[:1]
                residue["残留"] = True
                store.rows(MODULE).append(residue)
        if not created:
            return None, f"成员事件未与周期口径「{caliber}」同时登记，已跳过周期：{'、'.join(skipped)}"
        store.bump_version(MODULE)
        message = f"已按首次行为周期生成 {len(created)} 行留存矩阵草稿"
        if skipped:
            message += f"；口径未同时登记的周期已跳过：{'、'.join(skipped)}"
        if existing_confirmed:
            message += "；该群组存在已发布矩阵，本次新周期重算已登记为待重算"
        return created, message

    # ---------- V001 留档核对 ----------

    def _verify_complete(self, entry: dict[str, Any]) -> list[str]:
        """V001 核对：矩阵显示完整，且每列留存率可回溯到同期群定义。"""
        problems: list[str] = []
        details = {d.get("周期"): d for d in entry.get("留存明细", [])}
        for period in PERIOD_COLUMNS:
            detail = details.get(period)
            if detail is None:
                problems.append(f"{period}缺失，矩阵显示不完整")
                continue
            if detail.get("群组编号") != entry.get("群组编号"):
                problems.append(f"{period}留存率无法回溯到同一同期群定义")
            if int(detail.get("分母样本", 0)) <= 0:
                problems.append(f"{period}分母样本为空，无法回溯")
            rate = detail.get("留存率")
            if rate is None or not (0 <= float(rate) <= 1):
                problems.append(f"{period}留存率越界")
        cohort = store.find_by_field(COHORT_MODULE, "群组编号", entry.get("群组编号"))
        if cohort is None:
            problems.append(f"同期群定义 {entry.get('群组编号')} 不存在，矩阵无法回溯")
        return problems

    def _archive(self, entry: dict[str, Any], result: str, note: str) -> dict[str, Any]:
        archive = {
            "id": store.next_id(ARCHIVE_MODULE),
            "留档编号": f"{ARCHIVE_VERSION}-{store.next_id(ARCHIVE_MODULE):04d}",
            "矩阵编号": entry.get("矩阵编号"),
            "群组编号": entry.get("群组编号"),
            "核对环节": "矩阵显示完整",
            "核对结果": result,
            "核对说明": note,
            "留档时间": date.today().isoformat(),
        }
        store.rows(ARCHIVE_MODULE).append(archive)
        store.bump_version(ARCHIVE_MODULE)
        return archive

    def publish_matrix(self, entry_id: int) -> tuple[dict[str, Any] | None, str]:
        """V001 留档核对：核对矩阵显示完整；事务存在残留时整体回滚。"""
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"留存矩阵 {entry_id} 不存在或已归档"
        if entry.get("status") != STATUS_ORDER[0]:
            return None, f"留存矩阵 {entry.get('矩阵编号')} 当前状态不允许重复核对"
        # 事务残留检测：同一矩阵编号出现多条记录即为部分写入残留，整体回滚。
        siblings = [
            row for row in store.rows(MODULE)
            if row.get("矩阵编号") == entry.get("矩阵编号")
        ]
        if len(siblings) > 1 or any(row.get("残留") for row in siblings):
            for row in siblings:
                store.rows(MODULE).remove(row)
            self._archive(entry, "回滚", "事务存在残留，已整体回滚")
            store.bump_version(MODULE)
            return None, f"留存矩阵 {entry.get('矩阵编号')} 事务存在残留，已整体回滚并按 {ARCHIVE_VERSION} 留档"
        problems = self._verify_complete(entry)
        if problems:
            self._archive(entry, "回滚", "；".join(problems))
            store.rows(MODULE).remove(entry)
            store.bump_version(MODULE)
            return None, f"V001 核对未通过：{'；'.join(problems)}，已整体回滚"
        archive = self._archive(entry, "通过", "矩阵显示完整，每列留存率可回溯到同期群定义")
        entry["status"] = "已确认"
        entry["pending"] = False
        entry["核对留档"] = archive["留档编号"]
        store.bump_version(MODULE)
        return _serialize(entry), f"留存矩阵 {entry.get('矩阵编号')} 已通过 {ARCHIVE_VERSION} 留档核对并发布"

    # ---------- 停用与重试 ----------

    def deactivate(self, entry_id: int, values: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
        """任一时点停用群组：保留已发布矩阵，只暴露一套完整周期列。

        停用与新周期重算同时发生时形成版本冲突，判定为保留停用前口径并延后新周期：
        矩阵显示完整历史周期并附停用原因，待重算的新周期一并延后。
        """
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"留存矩阵 {entry_id} 不存在或已归档"
        if entry.get("status") != "已确认":
            return None, "只有已发布的留存矩阵才能停用；未确认矩阵请先做 V001 核对"
        time_point = str(values.get("停用时点") or date.today().isoformat()).strip()
        reason = str(values.get("停用原因") or "").strip() or "未填写停用原因"
        entry["status"] = "已停用"
        entry["pending"] = False
        entry["停用时点"] = time_point
        entry["停用原因"] = reason
        # 版本冲突：同群组存在待重算的新周期 → 保留停用前口径并延后新周期。
        deferred = [
            row for row in store.rows(MODULE)
            if row.get("群组编号") == entry.get("群组编号")
            and row.get("重算状态") == "待重算"
            and row.get("status") == STATUS_ORDER[0]
        ]
        for row in deferred:
            row["重算状态"] = "已延后"
        self._archive(entry, "留档", f"群组于 {time_point} 停用：{reason}；已发布矩阵保留完整历史周期")
        store.bump_version(MODULE)
        message = f"群组已停用，已发布矩阵保留完整历史周期并附停用原因"
        if deferred:
            message += f"；{len(deferred)} 个新周期重算已延后，保留停用前口径"
        return _serialize(entry), message

    def retry(self, entry_id: int) -> tuple[dict[str, Any] | None, str]:
        """重试只能补写缺失周期，不能覆盖完整历史。"""
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"留存矩阵 {entry_id} 不存在或已归档"
        details = {d.get("周期"): d for d in entry.get("留存明细", [])}
        missing = [period for period in PERIOD_COLUMNS if period not in details]
        if not missing:
            return None, f"留存矩阵 {entry.get('矩阵编号')} 历史周期完整，重试不会覆盖已发布内容"
        fresh, denominator = _build_columns(
            str(entry.get("群组编号")), str(entry.get("首次行为周期")), str(entry.get("周期口径")))
        if not fresh:
            return None, "成员事件口径仍未同时登记，缺失周期无法补写"
        filled: list[str] = []
        for detail in fresh:
            if detail["周期"] in missing:
                entry["留存明细"].append(detail)
                filled.append(detail["周期"])
        entry["留存明细"].sort(key=lambda d: PERIOD_COLUMNS.index(d["周期"]))
        entry["分母样本"] = denominator
        store.bump_version(MODULE)
        return _serialize(entry), f"已补写缺失周期：{'、'.join(filled)}；完整历史周期未被覆盖"

    # ---------- 查询（只读确认结果） ----------

    def _confirmed_rows(self) -> list[dict[str, Any]]:
        """列表页与详情页都只读取 V001 核对通过的确认结果（含已停用的已发布矩阵）。"""
        return [
            row for row in store.rows(MODULE)
            if row.get("status") in ("已确认", "已停用") and not row.get("残留")
        ]

    def list_entries(
        self,
        *,
        filters: dict[str, str] | None = None,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = self._confirmed_rows()
        for field, expected in (filters or {}).items():
            rows = [row for row in rows if expected.casefold() in str(row.get(field, "")).casefold()]
        if keyword:
            rows = [row for row in rows if keyword.casefold() in str(row.get("矩阵编号", "")).casefold()]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = (page - 1) * size
        return [_serialize(row) for row in rows[start:start + size]], total

    def list_drafts(self) -> list[dict[str, Any]]:
        """待 V001 核对的草稿：仅供核对操作入口使用，消费端列表不读取。"""
        return [
            _serialize(row) for row in store.rows(MODULE)
            if row.get("status") == STATUS_ORDER[0] and not row.get("残留")
        ]

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        row = store.find(MODULE, entry_id)
        if row is None or row.get("status") not in ("已确认", "已停用") or row.get("残留"):
            return None
        item = _serialize(row)
        item["留存明细"] = row.get("留存明细", [])
        return item

    def trace_column(self, entry_id: int, period: str) -> tuple[dict[str, Any] | None, str]:
        """从分母样本追到同一同期群定义：每列留存率可回溯。"""
        row = store.find(MODULE, entry_id)
        if row is None or row.get("status") not in ("已确认", "已停用"):
            return None, f"留存矩阵 {entry_id} 不存在或尚未通过 {ARCHIVE_VERSION} 核对"
        if period not in PERIOD_COLUMNS:
            return None, f"周期列「{period}」不在完整周期列 {'、'.join(PERIOD_COLUMNS)} 内"
        detail = next((d for d in row.get("留存明细", []) if d.get("周期") == period), None)
        if detail is None:
            return None, f"留存矩阵 {row.get('矩阵编号')} 缺少 {period}，矩阵显示不完整"
        cohort = store.find_by_field(COHORT_MODULE, "群组编号", row.get("群组编号"))
        if cohort is None:
            return None, f"同期群定义 {row.get('群组编号')} 不存在，无法回溯"
        members = sorted(_period_members(
            str(row.get("群组编号")), str(row.get("首次行为周期")), period, str(row.get("周期口径"))))
        return {
            "矩阵编号": row.get("矩阵编号"),
            "周期": period,
            "留存率": detail.get("留存率"),
            "留存人数": detail.get("留存人数"),
            "分母样本": detail.get("分母样本"),
            "群组编号": row.get("群组编号"),
            "同期群定义": cohort,
            "成员样本": members[:20],
            "核对留档": row.get("核对留档"),
        }, "ok"

    # ---------- 视图：时间线与业务概览 ----------

    def timeline(self) -> list[dict[str, Any]]:
        """按时间线展示已确认矩阵：关键状态直接呈现。"""
        items: list[dict[str, Any]] = []
        for row in sorted(self._confirmed_rows(), key=lambda r: str(r.get("首次行为周期", ""))):
            item = _serialize(row)
            item["群组名称"] = (store.find_by_field(COHORT_MODULE, "群组编号", row.get("群组编号")) or {}).get("群组名称", "")
            items.append(item)
        return items

    def overview(self) -> dict[str, Any]:
        """业务概览：关键状态直接呈现。"""
        rows = self._confirmed_rows()
        confirmed = [row for row in rows if row.get("status") == "已确认"]
        deactivated = [row for row in rows if row.get("status") == "已停用"]
        first_period_rates = [
            float(d["留存率"])
            for row in rows for d in row.get("留存明细", [])
            if d.get("周期") == PERIOD_COLUMNS[1]
        ]
        return {
            "cards": [
                {"label": "已发布矩阵", "value": len(rows)},
                {"label": "生效中", "value": len(confirmed)},
                {"label": "已停用", "value": len(deactivated)},
                {"label": "覆盖同期群", "value": len({row.get("群组编号") for row in rows})},
                {"label": f"{PERIOD_COLUMNS[1]}平均留存率", "value": round(sum(first_period_rates) / len(first_period_rates), 4) if first_period_rates else 0},
                {"label": f"{ARCHIVE_VERSION}留档", "value": len(store.rows(ARCHIVE_MODULE))},
            ]
        }
