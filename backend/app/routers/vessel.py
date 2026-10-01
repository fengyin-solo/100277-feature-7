"""船舶作业接口：船舶台账 + 同船作业条目的单向作业链。

- GET    /api/vessel                       船舶作业台账（箱量由作业条目同源汇总）
- GET    /api/vessel/board                 作业看板（箱量与作业明细同源）
- GET    /api/vessel/todos                 船舶作业台账待办清单（确认离泊回写）
- POST   /api/vessel                       登记船舶（航线代码必填）
- GET    /api/vessel/{id}                  船舶明细
- POST   /api/vessel/{id}/actions          安排靠泊 / 开始作业 / 确认离泊
- GET    /api/vessel/{id}/items            该船作业条目
- POST   /api/vessel/{id}/items/actions    同船条目整组提交（开工 / 核对）
- POST   /api/vessel/items/{item_id}/reject 单个条目打回，只退回它自己
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionResult, BatchItemPayload, EntryPayload, PageResult
from app.services.vessel import VesselService

router = APIRouter(prefix="/api/vessel", tags=["船舶作业"])

service = VesselService()

LIST_FIELDS = ["船舶编号", "船名", "船公司", "航线代码", "进口航次", "出口航次", "作业箱量合计", "船舶状态"]
STATUSES = ["锚地待泊", "靠泊中", "作业中", "已离泊"]


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按船舶编号检索"),
    status: str | None = Query(default=None, description="锚地待泊、靠泊中、作业中、已离泊"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按船舶编号与状态过滤船舶作业台账；没有数据时返回空页，不报错。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_entries(keyword=keyword, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/board")
def board() -> dict[str, Any]:
    """作业看板：箱量与作业条目明细同源，全部由 vessel_item 汇总。"""
    return service.board()


@router.get("/todos")
def list_todos(vessel_id: int | None = Query(default=None, description="只看某条船的台账待办")) -> dict[str, Any]:
    """船舶作业台账待办清单；确认离泊后会回写一条「跟进离泊」。"""
    rows = service.list_todos(vessel_id)
    return {"total": len(rows), "items": rows}


@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出船舶作业清单：返回当前过滤条件下的全量数据（箱量与看板同源）。"""
    items, total = service.list_entries(page=1, size=10000)
    return {"module": "vessel", "total": total, "items": items}


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条船舶，航线代码留空不允许提交。"""
    entry, missing = service.create_entry(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message="船舶已登记", entry=entry)


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条船舶明细；不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"船舶 {entry_id} 不存在或已归档")
    return entry


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """安排靠泊、开始作业、确认离泊；跳级、回退、未核对完离泊都会被拦下并说明原因。"""
    action = str(payload.values.get("action") or "").strip()
    entry, message = service.run_action(entry_id, action)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)


@router.get("/{entry_id}/items")
def list_items(entry_id: int) -> dict[str, Any]:
    """同一条船下的作业条目，整组多选与看板箱量都读这里这一份数据。"""
    if service.get_entry(entry_id) is None:
        raise HTTPException(status_code=404, detail=f"船舶 {entry_id} 不存在或已归档")
    items = service.list_items(entry_id)
    return {"vessel_id": entry_id, "total": len(items), "items": items}


@router.post("/{entry_id}/items/actions", response_model=ActionResult)
def batch_items(entry_id: int, payload: BatchItemPayload) -> ActionResult:
    """同一条船下多选条目后整组提交：开工、核对都只准顺一格；有一个不合规整组驳回。"""
    record, message = service.batch_action(
        vessel_id=entry_id,
        action=str(payload.action or "").strip(),
        item_ids=list(payload.item_ids or []),
        batch_no=payload.batch_no,
    )
    if record is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=record)


@router.post("/items/{item_id}/reject", response_model=ActionResult)
def reject_item(item_id: int) -> ActionResult:
    """打回只作用于这一个条目，让它自己退回待开工重走；已离泊的不允许打回。"""
    item, message = service.reject_item(item_id)
    if item is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=item)
