"""船舶作业业务规则：船舶与作业条目共用一条单向作业链。

- 作业条目：待开工 → 作业中 → 已核对，只准顺着推进，打回只让该条目自己退回待开工；
  所属船已经离泊的条目一律锁定。
- 船舶：锚地待泊 → 靠泊中 → 作业中 → 已离泊，同样只能顺移一格；
  没有核对完的条目不许确认离泊，已离泊不许退回作业中。
- 同一条船下的条目可以整组提交，整组提交按批次号幂等去重。
- 确认离泊后回写一条记录到船舶作业台账待办清单。
- 看板箱量、各船箱量都从作业条目这一份数据汇总，不允许两处口径。
"""
from __future__ import annotations

from typing import Any

from app.store import store

MODULE = "vessel"
ITEM_MODULE = "vessel_item"
TODO_MODULE = "vessel_todo"
BATCH_MODULE = "vessel_batch"

REQUIRED_FIELDS = ["船舶编号", "船名", "船公司", "航线代码"]

# 船舶作业链：只能顺着推进，不允许跳级、不允许回退。
STATUS_ORDER = ["锚地待泊", "靠泊中", "作业中", "已离泊"]
ACTION_RULES = {"安排靠泊": "靠泊中", "开始作业": "作业中", "确认离泊": "已离泊"}

# 作业条目作业链：待开工 → 作业中 → 已核对。
ITEM_STATUS_ORDER = ["待开工", "作业中", "已核对"]
ITEM_ACTIONS = {"开工": "作业中", "核对": "已核对"}

ITEM_STAGE_LABELS = {"开工": "待开工", "核对": "作业中"}


class VesselService:
    # ------------------------------------------------------------------ 列表/读取
    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(MODULE)
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("船舶编号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = max(page - 1, 0) * size
        return [self._augment(row) for row in rows[start:start + size]], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        row = store.find(MODULE, entry_id)
        return self._augment(row) if row else None

    # ------------------------------------------------------------------ 登记船舶
    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        entry = {"id": self._next_id(rows)}
        entry.update({field: str(values.get(field)).strip() for field in REQUIRED_FIELDS})
        entry["进口航次"] = str(values.get("进口航次") or "").strip()
        entry["出口航次"] = str(values.get("出口航次") or "").strip()
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        rows.append(entry)
        return self._augment(entry), []

    # ------------------------------------------------------------------ 船舶动作
    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"船舶 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于船舶作业可执行范围"

        current = str(entry.get("status"))
        target = ACTION_RULES[action]
        if current not in STATUS_ORDER or STATUS_ORDER.index(target) != STATUS_ORDER.index(current) + 1:
            return None, f"当前状态「{current}」只能推进到下一环节，不允许跳到「{target}」，也不允许退回"

        # 作业链走到「作业中」时，必须带着非空航线代码，台账后续才对得上。
        if not str(entry.get("航线代码") or "").strip():
            return None, "航线代码为空，不允许提交，请先补全航线代码"

        if target == "已离泊":
            items = self._items_of(entry_id)
            if not items:
                return None, "该船还没有作业条目，核对完成前不允许确认离泊"
            unfinished = [item for item in items if item.get("status") != "已核对"]
            if unfinished:
                return None, f"还有 {len(unfinished)} 个作业条目未核对，未核对完不允许确认离泊"

        entry["status"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = False
        if target == "已离泊":
            self._write_departure_todo(entry, items)
        return self._augment(entry), f"船舶已{action}"

    # ------------------------------------------------------------------ 作业条目
    def list_items(self, vessel_id: int) -> list[dict[str, Any]]:
        if store.find(MODULE, vessel_id) is None:
            return []
        return [dict(item) for item in self._items_of(vessel_id)]

    def batch_action(
        self,
        *,
        vessel_id: int,
        action: str,
        item_ids: list[int],
        batch_no: str | None = None,
    ) -> tuple[dict[str, Any] | None, str]:
        vessel = store.find(MODULE, vessel_id)
        if vessel is None:
            return None, f"船舶 {vessel_id} 不存在或已归档"
        if action not in ITEM_ACTIONS:
            return None, f"整组提交只支持「开工」「核对」，收到的动作是「{action}」"

        clean_ids = sorted({int(raw) for raw in item_ids if raw is not None})
        if not clean_ids:
            return None, "请先勾选同一条船下的作业条目再提交"

        # 同一条船下的条目才能整组提交：跨船选择直接整组驳回。
        items: list[dict[str, Any]] = []
        foreign: list[int] = []
        missing: list[int] = []
        for item_id in clean_ids:
            item = store.find(ITEM_MODULE, item_id)
            if item is None:
                missing.append(item_id)
            elif int(item.get("所属船舶", 0)) != vessel_id:
                foreign.append(item_id)
            else:
                items.append(item)
        if missing:
            return None, f"作业条目 {', '.join(map(str, missing))} 不存在或已归档"
        if foreign:
            return None, f"条目 {', '.join(map(str, foreign))} 不属于本条船，整组提交只允许同一条船下的条目"

        # 同一批条目重复提交只留一条：批次号命中直接返回首次结果，不重复推进；
        # 不带批次号的重复提交则由下面的作业链守卫拦下（条目已不在允许环节），
        # 也不会再落第二条批次记录。打回后重新开工属于新动作，因此这里不按条目集合判重。
        batch_no = str(batch_no or "").strip()
        if batch_no:
            existed = self._find_batch(batch_no)
            if existed is not None:
                return dict(existed), "该批次已经提交过，按首次提交保留一条，不重复处理"

        if str(vessel.get("status")) == "已离泊":
            return None, "船舶已经离泊，作业条目锁定，不允许再提交"
        if str(vessel.get("status")) != "作业中":
            return None, f"船舶当前「{vessel.get('status')}」，开始作业后才能整组推进作业条目"

        # 原子校验：有一个条目不能顺着走，整组都不动。
        expected = ITEM_STAGE_LABELS[action]
        blocked = [
            f"{item.get('id')}（当前{item.get('status')}）"
            for item in items
            if item.get("status") != expected
        ]
        if blocked:
            return None, (
                f"「{action}」只能提交处于「{expected}」的条目，"
                f"以下条目不允许跳级或重复提交：{', '.join(blocked)}"
            )

        target = ITEM_ACTIONS[action]
        for item in items:
            item["status"] = target
            item["pending"] = target != "已核对"
        record = {
            "id": self._next_id(store.rows(BATCH_MODULE)),
            "batch_no": batch_no,
            "所属船舶": vessel_id,
            "船舶编号": str(vessel.get("船舶编号")),
            "action": action,
            "item_ids": clean_ids,
        }
        store.rows(BATCH_MODULE).append(record)
        return dict(record), f"已对 {len(items)} 个作业条目整组{action}"

    def reject_item(self, item_id: int) -> tuple[dict[str, Any] | None, str]:
        """打回只让被选中的那一个条目自己退回待开工，同船其他条目不受影响。"""
        item = store.find(ITEM_MODULE, item_id)
        if item is None:
            return None, f"作业条目 {item_id} 不存在或已归档"
        vessel = store.find(MODULE, int(item.get("所属船舶", 0)))
        if vessel is not None and str(vessel.get("status")) == "已离泊":
            return None, "船舶已经离泊，条目已锁定，不允许打回"
        if item.get("status") == ITEM_STATUS_ORDER[0]:
            return None, "条目还在待开工，无需打回"
        item["status"] = ITEM_STATUS_ORDER[0]
        item["pending"] = True
        item["abnormal"] = True
        return dict(item), f"条目 {item_id} 已打回，自行从待开工重走作业链"

    # ------------------------------------------------------------------ 台账/看板
    def list_todos(self, vessel_id: int | None = None) -> list[dict[str, Any]]:
        rows = store.rows(TODO_MODULE)
        if vessel_id is not None:
            rows = [row for row in rows if int(row.get("所属船舶", 0)) == vessel_id]
        return [dict(row) for row in rows]

    def board(self) -> dict[str, Any]:
        """看板箱量与作业明细同源：全部从 vessel_item 这一份数据汇总。"""
        items = store.rows(ITEM_MODULE)
        vessel_status = {int(row["id"]): row.get("status") for row in store.rows(MODULE)}
        per_status = {status: 0 for status in ITEM_STATUS_ORDER}
        total_box = 0
        per_vessel: dict[int, int] = {}
        for item in items:
            qty = int(item.get("作业箱量", 0))
            total_box += qty
            per_status[str(item.get("status"))] = per_status.get(str(item.get("status")), 0) + 1
            per_vessel[int(item.get("所属船舶", 0))] = per_vessel.get(int(item.get("所属船舶", 0)), 0) + qty

        vessels = store.rows(MODULE)
        vessel_counts = {status: 0 for status in STATUS_ORDER}
        for vessel in vessels:
            vessel_counts[str(vessel.get("status"))] = vessel_counts.get(str(vessel.get("status")), 0) + 1

        return {
            "船舶总数": len(vessels),
            "船舶状态分布": vessel_counts,
            "作业条目总数": len(items),
            "条目状态分布": per_status,
            "待核对条目数": sum(
                1 for item in items
                if item.get("status") != "已核对"
                and vessel_status.get(int(item.get("所属船舶", 0))) != "已离泊"
            ),
            "总作业箱量": total_box,
            "各船作业箱量": [
                {"vessel_id": vessel_id, "box_qty": qty}
                for vessel_id, qty in sorted(per_vessel.items())
            ],
        }

    # ------------------------------------------------------------------ 内部方法
    def _items_of(self, vessel_id: int) -> list[dict[str, Any]]:
        return [
            item for item in store.rows(ITEM_MODULE)
            if int(item.get("所属船舶", 0)) == vessel_id
        ]

    def _augment(self, vessel: dict[str, Any]) -> dict[str, Any]:
        """列表里每艘船带的箱量，与看板走同一处汇总，避免两份口径对不上。"""
        items = self._items_of(int(vessel["id"]))
        counts = {status: 0 for status in ITEM_STATUS_ORDER}
        box_qty = 0
        for item in items:
            counts[str(item.get("status"))] = counts.get(str(item.get("status")), 0) + 1
            box_qty += int(item.get("作业箱量", 0))
        result = dict(vessel)
        result["作业条目数"] = len(items)
        result["已核对条目数"] = counts["已核对"]
        result["作业箱量合计"] = box_qty
        result["待办数"] = sum(
            1 for row in store.rows(TODO_MODULE)
            if int(row.get("所属船舶", 0)) == int(vessel["id"]) and row.get("pending")
        )
        return result

    def _write_departure_todo(self, vessel: dict[str, Any], items: list[dict[str, Any]]) -> dict[str, Any]:
        """确认离泊后回写到船舶作业台账的待办清单。"""
        todo = {
            "id": self._next_id(store.rows(TODO_MODULE)),
            "所属船舶": int(vessel["id"]),
            "船舶编号": str(vessel.get("船舶编号")),
            "船名": str(vessel.get("船名")),
            "航线代码": str(vessel.get("航线代码")),
            "事项": "跟进离泊",
            "作业条目数": len(items),
            "作业箱量合计": sum(int(item.get("作业箱量", 0)) for item in items),
            "status": "待跟进",
            "pending": True,
            "abnormal": False,
        }
        store.rows(TODO_MODULE).append(todo)
        return todo

    def _find_batch(self, batch_no: str) -> dict[str, Any] | None:
        for row in store.rows(BATCH_MODULE):
            if str(row.get("batch_no")) == batch_no:
                return row
        return None

    @staticmethod
    def _next_id(rows: list[dict[str, Any]]) -> int:
        return max((int(row.get("id", 0)) for row in rows), default=0) + 1
