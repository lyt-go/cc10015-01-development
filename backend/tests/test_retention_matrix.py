"""留存矩阵数据表核心链路测试：计算、V001 留档核对、停用、重试、回溯与跨模块联动。"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from app.store import store
from app.services.retention_matrix import (
    ARCHIVE_MODULE,
    COHORT_MODULE,
    EVENT_MODULE,
    MODULE,
    PERIOD_COLUMNS,
    RetentionMatrixService,
)
from app.services.metric_def import MetricDefService


def _clear_all():
    for k in list(store._tables.keys()):
        store._tables[k] = []


def _seed_cohort(code="COHO-T001"):
    store.rows(COHORT_MODULE).append({
        "id": 1, "群组编号": code, "群组名称": "测试同期群", "首次行为": "注册",
        "时间基准": "2026-09-01", "追踪周期": "4周", "留存窗口": "第3期",
        "分析指标": "留存率", "对比群组": "", "status": "已生效",
    })


def _seed_events(cohort="COHO-T001", first_period="2026-W36", base=100,
                 counts=(10, 8, 6, 5), caliber="按周"):
    seq = len(store.rows(EVENT_MODULE))
    members = [f"U{base + i + 1}" for i in range(counts[0])]
    for offset, count in enumerate(counts):
        for member in members[:count]:
            seq += 1
            store.rows(EVENT_MODULE).append({
                "id": seq, "事件编号": f"EV-{seq:04d}", "群组编号": cohort,
                "成员ID": member, "首次行为周期": first_period,
                "事件周期": f"第{offset}期", "周期口径": caliber, "登记时间": "2026-09-01",
            })
    return members


def _compute(svc, **overrides):
    values = {"群组编号": "COHO-T001", "指标编号": "METR-T1", "周期口径": "按周"}
    values.update(overrides)
    created, message = svc.compute_matrix(values)
    assert created is not None, message
    return created


def _published(svc, **overrides):
    created = _compute(svc, **overrides)
    entry, message = svc.publish_matrix(created[0]["id"])
    assert entry is not None, message
    return entry


def test_compute_matrix_by_first_period():
    """按首次行为周期计算留存率，每列都可回溯到同期群定义。"""
    _clear_all()
    _seed_cohort()
    _seed_events(counts=(10, 8, 6, 5))
    svc = RetentionMatrixService()
    created = _compute(svc)
    assert len(created) == 1
    entry = created[0]
    assert entry["分母样本"] == 10
    rates = {d["周期"]: d["留存率"] for d in entry["留存明细"]}
    assert rates == {"第0期": 1.0, "第1期": 0.8, "第2期": 0.6, "第3期": 0.5}
    for detail in entry["留存明细"]:
        assert detail["群组编号"] == "COHO-T001", "每列留存率必须能回溯到同期群定义"
        assert detail["分母样本"] == 10


def test_caliber_must_register_with_member_events():
    """留存周期必须与成员事件口径同时登记，否则未达标用户会被提前计入。"""
    _clear_all()
    _seed_cohort()
    _seed_events(counts=(10, 8, 6, 5), caliber="按周")
    # 口径未同时登记（按日）的事件：不得计入按周矩阵。
    _seed_events(base=200, counts=(3, 3, 3, 3), caliber="按日")
    svc = RetentionMatrixService()
    created = _compute(svc, 周期口径="按周")
    rates = {d["周期"]: d["留存率"] for d in created[0]["留存明细"]}
    assert created[0]["分母样本"] == 10
    assert rates["第1期"] == 0.8, "按日口径的事件不应计入按周矩阵"
    # 按月口径完全没有同时登记的成员事件，整个周期不生成。
    created2, message = svc.compute_matrix({"群组编号": "COHO-T001", "指标编号": "METR-T1", "周期口径": "按月"})
    assert created2 is None
    assert "同时登记" in message


def test_publish_v001_archive():
    """V001 核对矩阵显示完整后发布，并留下核对留档。"""
    _clear_all()
    _seed_cohort()
    _seed_events()
    svc = RetentionMatrixService()
    entry = _published(svc)
    assert entry["status"] == "已确认"
    assert str(entry["核对留档"]).startswith("V001-")
    archives = store.rows(ARCHIVE_MODULE)
    assert len(archives) == 1
    assert archives[0]["核对环节"] == "矩阵显示完整"
    assert archives[0]["核对结果"] == "通过"


def test_publish_rollback_on_transaction_residue():
    """事务存在残留时整体回滚，并按 V001 留档。"""
    _clear_all()
    _seed_cohort()
    _seed_events()
    svc = RetentionMatrixService()
    created = _compute(svc, 模拟残留="1")
    assert len(store.rows(MODULE)) == 2, "模拟残留应产生部分写入的重复行"
    entry, message = svc.publish_matrix(created[0]["id"])
    assert entry is None
    assert "残留" in message and "回滚" in message
    assert store.rows(MODULE) == [], "整体回滚后不应留下任何该矩阵的行"
    archives = store.rows(ARCHIVE_MODULE)
    assert len(archives) == 1
    assert archives[0]["核对结果"] == "回滚"


def test_list_and_detail_only_read_confirmed():
    """列表页与详情页都只读取 V001 核对通过的确认结果。"""
    _clear_all()
    _seed_cohort()
    _seed_events()
    svc = RetentionMatrixService()
    created = _compute(svc)
    items, total = svc.list_entries()
    assert total == 0, "草稿不得出现在列表页"
    assert svc.get_entry(created[0]["id"]) is None, "草稿不得出现在详情页"
    entry, _ = svc.publish_matrix(created[0]["id"])
    items, total = svc.list_entries()
    assert total == 1
    detail = svc.get_entry(created[0]["id"])
    assert detail is not None and detail["留存明细"]
    # 只暴露一套完整周期列
    for period in PERIOD_COLUMNS:
        assert period in items[0] and items[0][period] is not None


def test_deactivate_keeps_published_matrix():
    """任一时点停用群组后，已发布矩阵保留，只暴露一套完整周期列并附停用原因。"""
    _clear_all()
    _seed_cohort()
    _seed_events()
    svc = RetentionMatrixService()
    entry = _published(svc)
    deactivated, message = svc.deactivate(entry["id"], {"停用时点": "2026-11-18", "停用原因": "口径调整"})
    assert deactivated is not None, message
    assert deactivated["status"] == "已停用"
    assert deactivated["停用原因"] == "口径调整"
    assert deactivated["停用时点"] == "2026-11-18"
    items, total = svc.list_entries()
    assert total == 1, "停用后已发布矩阵必须保留在确认结果里"
    for period in PERIOD_COLUMNS:
        assert items[0][period] is not None, "停用后仍只暴露一套完整周期列"


def test_conflict_defers_new_period_recalc():
    """停用与新周期重算冲突：保留停用前口径并延后新周期，矩阵显示完整历史周期并附停用原因。"""
    _clear_all()
    _seed_cohort()
    _seed_events()
    svc = RetentionMatrixService()
    confirmed = _published(svc)
    # 同一群组发起新周期重算 → 待重算草稿
    recalc = _compute(svc)
    assert recalc[0]["重算状态"] == "待重算"
    deactivated, message = svc.deactivate(confirmed["id"], {"停用时点": "2026-11-18", "停用原因": "群组下线"})
    assert deactivated is not None, message
    assert "延后" in message
    draft = store.find(MODULE, recalc[0]["id"])
    assert draft["重算状态"] == "已延后", "版本冲突判定：保留停用前口径并延后新周期"
    row = store.find(MODULE, confirmed["id"])
    assert row["status"] == "已停用"
    assert row["停用原因"] == "群组下线"
    assert len(row["留存明细"]) == len(PERIOD_COLUMNS), "矩阵必须显示完整历史周期"


def test_retry_only_fills_missing_periods():
    """重试只能补写缺失周期，不能覆盖完整历史。"""
    _clear_all()
    _seed_cohort()
    _seed_events()
    svc = RetentionMatrixService()
    created = _compute(svc)
    draft = store.find(MODULE, created[0]["id"])
    removed = draft["留存明细"].pop()  # 模拟缺失第3期
    entry, message = svc.retry(created[0]["id"])
    assert entry is not None, message
    assert "第3期" in message
    rates = {d["周期"]: d["留存率"] for d in store.find(MODULE, created[0]["id"])["留存明细"]}
    assert rates["第3期"] == removed["留存率"]
    assert rates["第1期"] == 0.8, "已有周期不得被重试覆盖"
    # 完整历史不允许重试覆盖
    entry2, message2 = svc.retry(created[0]["id"])
    assert entry2 is None
    assert "不会覆盖" in message2


def test_trace_column_to_cohort_definition():
    """每列留存率可从分母样本追到同一同期群定义。"""
    _clear_all()
    _seed_cohort()
    members = _seed_events()
    svc = RetentionMatrixService()
    entry = _published(svc)
    trace, message = svc.trace_column(entry["id"], "第1期")
    assert trace is not None, message
    assert trace["群组编号"] == "COHO-T001"
    assert trace["同期群定义"]["群组编号"] == "COHO-T001"
    assert trace["分母样本"] == 10
    assert trace["留存率"] == 0.8
    assert set(trace["成员样本"]) <= set(members)
    bad, message2 = svc.trace_column(entry["id"], "第9期")
    assert bad is None and "完整周期列" in message2


def test_metric_def_validates_retention_columns():
    """跨模块联动（V001）：指标定义读取矩阵的指标编号和周期口径，校验每列留存率。"""
    _clear_all()
    _seed_cohort()
    _seed_events()
    md = MetricDefService()
    metric, missing = md.create_entry({
        "指标编号": "METR-T1", "指标名称": "周留存率", "计算公式": "retained/base",
    })
    assert metric is not None, missing
    svc = RetentionMatrixService()
    _published(svc)
    result, message = md.validate_retention_columns(metric["id"])
    assert result is not None, message
    assert result["指标编号"] == "METR-T1"
    assert result["校验列数"] == len(PERIOD_COLUMNS)
    assert result["问题"] == [], message
    # 没有确认矩阵的指标校验失败
    metric2, _ = md.create_entry({"指标编号": "METR-T2", "指标名称": "无矩阵", "计算公式": "x"})
    result2, message2 = md.validate_retention_columns(metric2["id"])
    assert result2 is None and "V001" in message2


def test_timeline_and_overview():
    """时间线视图与业务概览直接呈现关键状态。"""
    _clear_all()
    _seed_cohort()
    _seed_events()
    svc = RetentionMatrixService()
    entry = _published(svc)
    svc.deactivate(entry["id"], {"停用时点": "2026-11-18", "停用原因": "群组下线"})
    timeline = svc.timeline()
    assert len(timeline) == 1
    assert timeline[0]["status"] == "已停用"
    overview = svc.overview()
    cards = {c["label"]: c["value"] for c in overview["cards"]}
    assert cards["已发布矩阵"] == 1
    assert cards["已停用"] == 1
    assert cards["覆盖同期群"] == 1
    assert cards["V001留档"] >= 1
