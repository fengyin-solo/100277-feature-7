"""船舶作业接口：船舶登记靠离泊，以及船下作业条目的单向作业链。

新增的作业链相关接口都挂在本路由下，路由层不做业务判断，规则一律走 VesselService。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import (
    ActionResult,
    BatchWorkPayload,
    BatchWorkResult,
    EntryPayload,
    PageResult,
)
from app.services.vessel import DEPARTURE_ACTION, REJECT_ACTION, WORK_ACTIONS, VesselService

router = APIRouter(prefix="/api/vessel", tags=["船舶作业"])

service = VesselService()

LIST_FIELDS = ["船舶编号", "船名", "船公司", "航线代码", "进口航次", "出口航次", "预计作业箱量", "船舶状态"]
STATUSES = ["锚地待泊", "靠泊中", "作业中", "已离泊"]


@router.get("/board")
def work_board() -> dict[str, Any]:
    """作业看板：箱量直接由作业明细聚合，和明细表同源。"""
    return service.board()


@router.get("/ledger")
def work_ledger() -> dict[str, Any]:
    """船舶作业台账待办清单：条目来自离泊确认后的回写。"""
    items = service.list_ledger()
    return {"items": items, "total": len(items)}


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按船舶编号检索"),
    status: str | None = Query(default=None, description="锚地待泊、靠泊中、作业中、已离泊"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按船舶编号与状态过滤船舶作业列表；没有数据时返回空页，不报错。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_entries(keyword=keyword, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条船舶明细；不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"船舶 {entry_id} 不存在或已归档")
    return entry


@router.get("/{vessel_id}/works")
def list_works(vessel_id: int) -> dict[str, Any]:
    """读取一条船下的全部作业条目，看板与明细取的是同一份数据。"""
    vessel = service.get_entry(vessel_id)
    if vessel is None:
        raise HTTPException(status_code=404, detail=f"船舶 {vessel_id} 不存在或已归档")
    items = service.list_works(vessel_id)
    return {"vessel_id": vessel_id, "total": len(items), "items": items}


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条船舶；航线代码是必填，为空直接拦下。"""
    entry, missing = service.create_entry(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message="船舶已登记", entry=entry)


@router.post("/works/batch", response_model=BatchWorkResult)
def batch_works(payload: BatchWorkPayload) -> BatchWorkResult:
    """同一条船下的条目多选后整组提交：开工、核对、离泊都走这里。

    同一批重复提交可带 idempotency_key，后端只让它生效一次。
    """
    if payload.action not in WORK_ACTIONS:
        return BatchWorkResult(
            ok=False,
            action=payload.action,
            message=f"动作「{payload.action}」不属于作业链可执行范围",
        )
    result = service.batch_action(
        payload.work_ids, payload.action, payload.vessel_id, payload.idempotency_key
    )
    return BatchWorkResult(**result)


@router.post("/works/{work_id}/reject", response_model=BatchWorkResult)
def reject_work(work_id: int) -> BatchWorkResult:
    """打回只作用于被选中的那一条，让它自己回待开工重走。"""
    return BatchWorkResult(**service.run_work_action(work_id, REJECT_ACTION))


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """船舶级动作：安排靠泊、开始作业；离泊不在此通道，避免绕过核对。"""
    action = str(payload.values.get("action") or "").strip()
    entry, message = service.run_action(entry_id, action)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)


@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出船舶作业清单：返回当前全量船舶及同源聚合的作业看板。"""
    items, total = service.list_entries(page=1, size=10000)
    return {"module": "vessel", "total": total, "items": items, "board": service.board()}
