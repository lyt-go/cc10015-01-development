"""留存矩阵数据表接口：计算、V001 留档核对发布、停用、重试与列回溯。"""
from __future__ import annotations

import csv
import io

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.retention_matrix import LIST_FIELDS, PERIOD_COLUMNS, RetentionMatrixService

router = APIRouter(prefix="/api/retention_matrix", tags=["留存矩阵数据表"])
service = RetentionMatrixService()


def query_filters(request: Request) -> dict[str, str]:
    """只接受当前模块声明过的字段，忽略分页等保留参数。"""
    return {
        key: value.strip()
        for key, value in request.query_params.items()
        if key in LIST_FIELDS and value.strip()
    }


@router.get("/overview")
def get_overview() -> dict:
    """业务概览：关键状态直接呈现。"""
    return service.overview()


@router.get("/timeline")
def get_timeline() -> dict:
    """时间线视图：按首次行为周期展示已确认矩阵。"""
    return {"items": service.timeline(), "periods": PERIOD_COLUMNS}


@router.get("/drafts")
def list_drafts() -> dict:
    """待 V001 核对的草稿列表：仅供核对操作入口，消费端列表不读取。"""
    return {"items": service.list_drafts()}


@router.get("", response_model=PageResult[dict])
def list_entries(
    request: Request,
    keyword: str | None = Query(default=None, description="按矩阵编号检索"),
    status: str | None = Query(default=None, description="已确认、已停用"),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=200),
) -> PageResult[dict]:
    """列表页：只读取 V001 核对通过的确认结果，只暴露一套完整周期列。"""
    items, total = service.list_entries(
        filters=query_filters(request), keyword=keyword, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/export")
def export_entries(request: Request) -> StreamingResponse:
    """下载当前筛选条件下的 UTF-8 CSV；必须声明在 /{entry_id} 之前。"""
    items, _ = service.list_entries(filters=query_filters(request), page=1, size=1000000)
    output = io.StringIO()
    output.write("﻿")
    writer = csv.writer(output, lineterminator="\n")
    columns = ["id", *LIST_FIELDS, *PERIOD_COLUMNS, "status", "核对留档", "停用时点", "停用原因"]
    writer.writerow(columns)
    for item in items:
        row = []
        for key in columns:
            text = "" if item.get(key) is None else str(item.get(key))
            row.append("'" + text if text.startswith(("=", "+", "-", "@")) else text)
        writer.writerow(row)
    return StreamingResponse(
        iter([output.getvalue()]), media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="retention_matrix.csv"'})


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """详情页：与列表页读取同一份 V001 确认结果。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"留存矩阵 {entry_id} 不存在或尚未通过 V001 核对")
    return entry


@router.post("/compute", response_model=ActionResult)
def compute_matrix(payload: EntryPayload) -> ActionResult:
    """读取同期群定义和成员事件，按首次行为周期计算留存率，生成留存矩阵草稿。"""
    created, message = service.compute_matrix(payload.values)
    if created is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry={"生成行数": len(created)})


@router.post("/{entry_id}/publish", response_model=ActionResult)
def publish_matrix(entry_id: int) -> ActionResult:
    """V001 留档核对：矩阵显示完整才发布；事务存在残留时整体回滚。"""
    entry, message = service.publish_matrix(entry_id)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)


@router.post("/{entry_id}/deactivate", response_model=ActionResult)
def deactivate(entry_id: int, payload: EntryPayload) -> ActionResult:
    """任一时点停用群组：保留已发布矩阵，只暴露一套完整周期列。"""
    entry, message = service.deactivate(entry_id, payload.values)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)


@router.post("/{entry_id}/retry", response_model=ActionResult)
def retry(entry_id: int) -> ActionResult:
    """重试只补写缺失周期，不覆盖完整历史。"""
    entry, message = service.retry(entry_id)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)


@router.get("/{entry_id}/trace/{period}", response_model=dict)
def trace_column(entry_id: int, period: str) -> dict:
    """列回溯：从分母样本追到同一同期群定义。"""
    result, message = service.trace_column(entry_id, period)
    if result is None:
        raise HTTPException(status_code=404, detail=message)
    return result
