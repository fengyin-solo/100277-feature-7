"""船舶作业业务规则：船舶靠离泊与船下作业条目的单向作业链都收在这里。

作业链（条目级，只能顺推、不能跳级、不能倒退）：

    待开工 ──确认开工──▶ 作业中 ──核对完成──▶ 已核对 ──确认离泊──▶ 随船离泊

- 没核对完的条目不允许确认离泊；已离泊船舶的条目不允许再打回。
- 打回只作用于被选中的那一条，由它自己回到待开工重走。
- 同一条船下的条目可以整组提交；跨船混选、航线代码为空都会被拦下。
- 离泊确认后按船去重回写一条台账待办；同一批重复提交靠幂等键只生效一次。
- 看板箱量直接对作业明细表求和，与明细列表同源，不另存一份。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from app.store import store

MODULE = "vessel"
WORK_MODULE = "_vessel_work"
LEDGER_MODULE = "_vessel_ledger"
IDEMPOTENT_MODULE = "_vessel_idempotency"

REQUIRED_FIELDS = ["船舶编号", "船名", "船公司", "航线代码"]
SHIP_FIELDS = ["船舶编号", "船名", "船公司", "航线代码", "进口航次", "出口航次", "预计作业箱量"]

# 船舶级状态序列与条目作业链分别维护，均只允许顺推一格。
STATUS_ORDER = ["锚地待泊", "靠泊中", "作业中", "已离泊"]
SHIP_ACTION_RULES = {"安排靠泊": "靠泊中", "开始作业": "作业中"}

WORK_ORDER = ["待开工", "作业中", "已核对"]
WORK_ACTION_RULES = {"确认开工": "作业中", "核对完成": "已核对"}
REJECT_ACTION = "打回"
DEPARTURE_ACTION = "确认离泊"
WORK_ACTIONS = [*WORK_ACTION_RULES.keys(), REJECT_ACTION, DEPARTURE_ACTION]


class VesselService:
    # ---- 船舶台账 ----------------------------------------------------------

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
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        return store.find(MODULE, entry_id)

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        for field in SHIP_FIELDS:
            entry[field] = str(values.get(field) or "").strip()
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        rows.append(entry)
        return entry, []

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"船舶 {entry_id} 不存在或已归档"
        if action == DEPARTURE_ACTION:
            return None, "离泊必须在作业条目全部核对完成后，由作业链的「确认离泊」提交"
        target = SHIP_ACTION_RULES.get(action)
        if target is None:
            return None, f"动作「{action}」不属于船舶作业可执行范围"
        current = str(entry.get("status") or "")
        if current not in STATUS_ORDER:
            return None, f"当前状态「{current}」不在允许的状态序列里"
        cur_idx, target_idx = STATUS_ORDER.index(current), STATUS_ORDER.index(target)
        if target_idx != cur_idx + 1:
            return None, f"船舶当前为「{current}」，不能跳到「{target}」，作业链只允许顺推一格"
        entry["status"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        return entry, f"船舶已{action}"

    # ---- 作业看板（箱量与作业明细同源）------------------------------------

    def board(self) -> dict[str, Any]:
        """对作业明细表直接聚合，看板读到的箱量与明细列表永远是同一份数据。"""
        ships: list[dict[str, Any]] = []
        for vessel in store.rows(MODULE):
            works = self._works_of(int(vessel["id"]))
            by_status = {stage: 0 for stage in WORK_ORDER}
            total_volume = checked_volume = 0
            for work in works:
                by_status[str(work.get("status"))] = by_status.get(str(work.get("status")), 0) + 1
                volume = int(work.get("作业箱量") or 0)
                total_volume += volume
                if work.get("status") == "已核对":
                    checked_volume += volume
            ships.append({
                "vessel_id": vessel["id"],
                "船舶编号": vessel.get("船舶编号"),
                "船名": vessel.get("船名"),
                "航线代码": vessel.get("航线代码"),
                "船舶状态": vessel.get("status"),
                "预计作业箱量": vessel.get("预计作业箱量"),
                "条目总数": len(works),
                "待开工": by_status.get("待开工", 0),
                "作业中": by_status.get("作业中", 0),
                "已核对": by_status.get("已核对", 0),
                "明细箱量": total_volume,
                "核对箱量": checked_volume,
                "可离泊": bool(works) and all(w.get("status") == "已核对" for w in works),
            })
        return {
            "ships": ships,
            "stats": {
                "待开工条目": sum(s["待开工"] for s in ships),
                "作业中条目": sum(s["作业中"] for s in ships),
                "已核对条目": sum(s["已核对"] for s in ships),
                "可离泊船舶": sum(1 for s in ships if s["可离泊"]),
            },
        }

    def list_works(self, vessel_id: int) -> list[dict[str, Any]]:
        return self._works_of(vessel_id)

    # ---- 台账待办 ----------------------------------------------------------

    def list_ledger(self) -> list[dict[str, Any]]:
        return store.rows(LEDGER_MODULE)

    # ---- 条目作业链：单条与整组提交走同一套规则 ---------------------------

    def run_work_action(self, work_id: int, action: str) -> dict[str, Any]:
        return self.batch_action([work_id], action, None, None)

    def batch_action(
        self,
        work_ids: list[int],
        action: str,
        vessel_id_hint: int | None,
        idempotency_key: str | None,
    ) -> dict[str, Any]:
        key = str(idempotency_key or "").strip()
        if key:
            cached = store.find_by(IDEMPOTENT_MODULE, "key", key)
            if cached is not None:
                result = dict(cached["result"])
                result["duplicate"] = True
                result["message"] = f"同一批条目已提交过，已合并为一条：{result['message']}"
                return result

        # 同一批里重复勾选的条目先去重，只按一条处理。
        unique_ids = list(dict.fromkeys(int(i) for i in work_ids))
        result = self._batch_result(action)
        if not unique_ids:
            result.update(ok=False, message="未选择任何作业条目，无法提交")
            return self._finish(key, result)

        works: list[dict[str, Any]] = []
        for work_id in unique_ids:
            work = store.find(WORK_MODULE, work_id)
            if work is None:
                result.update(ok=False, message=f"作业条目 {work_id} 不存在或已归档")
                return self._finish(key, result)
            works.append(work)

        vessel_ids = {int(w["vessel_id"]) for w in works}
        if vessel_id_hint is not None and vessel_ids != {int(vessel_id_hint)}:
            result.update(ok=False, message="整组提交只支持同一条船下的作业条目，不能跨船混选")
            return self._finish(key, result)
        if len(vessel_ids) > 1:
            result.update(ok=False, message="整组提交只支持同一条船下的作业条目，不能跨船混选")
            return self._finish(key, result)

        vessel = store.find(MODULE, vessel_ids.pop())
        if vessel is None:
            result.update(ok=False, message="条目所属船舶不存在，无法提交")
            return self._finish(key, result)
        if not str(vessel.get("航线代码") or "").strip():
            result.update(ok=False, message=f"船舶 {vessel.get('船舶编号')} 的航线代码为空，补齐前不许提交")
            return self._finish(key, result)

        if action == DEPARTURE_ACTION:
            self._depart(vessel, works, result)
        elif action == REJECT_ACTION:
            self._reject(vessel, works, result)
        elif action in WORK_ACTION_RULES:
            self._advance(vessel, works, action, result)
        else:
            result.update(ok=False, message=f"动作「{action}」不属于作业链可执行范围")

        return self._finish(key, result)

    # ---- 作业链各动作的具体规则 -------------------------------------------

    def _advance(
        self,
        vessel: dict[str, Any],
        works: list[dict[str, Any]],
        action: str,
        result: dict[str, Any],
    ) -> None:
        """确认开工 / 核对完成：只接受当前环节顺推一格，其余一律拦下。"""
        target = WORK_ACTION_RULES[action]
        required = WORK_ORDER[WORK_ORDER.index(target) - 1]
        if vessel.get("status") == STATUS_ORDER[-1]:
            result.update(ok=False, message="船舶已离泊，作业条目不能再继续推进")
            return
        if action == "确认开工" and vessel.get("status") == STATUS_ORDER[0]:
            result.update(ok=False, message=f"船舶「{vessel.get('船名')}」尚未靠泊，条目不能开工")
            return

        for work in works:
            current = str(work.get("status"))
            if current == required:
                work["status"] = target
                work["pending"] = target != WORK_ORDER[-1]
                work["abnormal"] = False
                if target == "已核对":
                    work["理货核对"] = "已核对"
                result["advanced"].append(self._work_brief(work))
            elif current == target:
                # 重复点击同一环节：不回退、不重复记账，只做跳过。
                result["skipped"].append(self._work_brief(work))
            else:
                result["conflicted"].append({**self._work_brief(work), "原因": f"当前为「{current}」，不能跳到「{target}」"})

        advanced, skipped, conflicted = (
            len(result["advanced"]), len(result["skipped"]), len(result["conflicted"])
        )
        if conflicted:
            result["ok"] = False
            tail = f"，{skipped} 条已在「{target}」未重复处理" if skipped else ""
            result["message"] = f"{conflicted} 条不在可{action}的环节，已拦下；{advanced} 条已推进{tail}"
        elif advanced:
            result["ok"] = True
            tail = f"，{skipped} 条已在「{target}」未重复处理" if skipped else ""
            result["message"] = f"{advanced} 条已{action}{tail}"
        else:
            result["ok"] = True
            result["message"] = f"所选 {skipped} 条均已处于「{target}」，无需重复提交"

    def _reject(self, vessel: dict[str, Any], works: list[dict[str, Any]], result: dict[str, Any]) -> None:
        """打回只准一条，且已离泊的不许退；被打回的条目自己回待开工重走。"""
        if len(works) != 1:
            result.update(ok=False, message="打回只允许选择一条，由该条目自己退回重走，不影响同船其他条目")
            return
        work = works[0]
        if vessel.get("status") == STATUS_ORDER[-1] or work.get("已离泊"):
            result.update(ok=False, message="船舶已离泊，作业条目不允许再退回作业中")
            return
        current = str(work.get("status"))
        if current == WORK_ORDER[0]:
            result.update(ok=False, message=f"条目 {work.get('作业条目编号')} 尚在「待开工」，无需打回")
            return
        work["status"] = WORK_ORDER[0]
        work["理货核对"] = "未核对"
        work["pending"] = True
        work["abnormal"] = True
        result["advanced"].append(self._work_brief(work))
        result["ok"] = True
        result["message"] = f"条目 {work.get('作业条目编号')} 已打回，从「待开工」重新走作业链"

    def _depart(self, vessel: dict[str, Any], works: list[dict[str, Any]], result: dict[str, Any]) -> None:
        """确认离泊：同船条目必须全部已核对；随后按船回写唯一一条台账待办。"""
        if vessel.get("status") == STATUS_ORDER[-1]:
            result.update(ok=False, message=f"船舶「{vessel.get('船名')}」已离泊，不能重复确认")
            return
        vessel_works = self._works_of(int(vessel["id"]))
        unfinished = [w for w in vessel_works if w.get("status") != "已核对"]
        if unfinished:
            names = "、".join(str(w.get("作业条目编号")) for w in unfinished[:5])
            more = "等" if len(unfinished) > 5 else ""
            result.update(ok=False, message=f"还有 {len(unfinished)} 条未核对完成（{names}{more}），不许离泊")
            return

        now = datetime.now().isoformat(timespec="seconds")
        vessel["status"] = STATUS_ORDER[-1]
        vessel["pending"] = False
        for work in vessel_works:
            work["已离泊"] = True
            work["离泊时间"] = now

        checked_volume = sum(int(w.get("作业箱量") or 0) for w in vessel_works)
        ledger = self._upsert_ledger(vessel, checked_volume, len(vessel_works), now)
        result["advanced"] = [self._work_brief(w) for w in works]
        result["ledger"] = ledger
        result["ok"] = True
        result["message"] = (
            f"船舶「{vessel.get('船名')}」已确认离泊，核对箱量 {checked_volume}，"
            "已回写船舶作业台账待办清单"
        )

    # ---- 内部辅助 ----------------------------------------------------------

    def _works_of(self, vessel_id: int) -> list[dict[str, Any]]:
        return [w for w in store.rows(WORK_MODULE) if int(w.get("vessel_id", 0)) == vessel_id]

    def _upsert_ledger(
        self, vessel: dict[str, Any], checked_volume: int, work_count: int, now: str
    ) -> dict[str, Any]:
        """台账按船舶编号去重：同一船重复离泊确认只保留一条，更新不新增。"""
        rows = store.rows(LEDGER_MODULE)
        ledger = store.find_by(LEDGER_MODULE, "vessel_id", int(vessel["id"]))
        payload = {
            "船舶编号": vessel.get("船舶编号"),
            "船名": vessel.get("船名"),
            "航线代码": vessel.get("航线代码"),
            "出口航次": vessel.get("出口航次"),
            "核对箱量": checked_volume,
            "条目数": work_count,
            "离泊时间": now,
            "待办状态": "待处理",
        }
        if ledger is None:
            ledger = {"id": max((int(r.get("id", 0)) for r in rows), default=0) + 1,
                      "vessel_id": int(vessel["id"])}
            ledger.update(payload)
            rows.append(ledger)
        else:
            ledger.update(payload)
        return ledger

    @staticmethod
    def _work_brief(work: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": work.get("id"),
            "vessel_id": work.get("vessel_id"),
            "作业条目编号": work.get("作业条目编号"),
            "status": work.get("status"),
            "作业箱量": work.get("作业箱量"),
        }

    @staticmethod
    def _batch_result(action: str) -> dict[str, Any]:
        return {
            "ok": False,
            "message": "",
            "action": action,
            "advanced": [],
            "skipped": [],
            "conflicted": [],
            "ledger": None,
            "duplicate": False,
        }

    @staticmethod
    def _finish(key: str, result: dict[str, Any]) -> dict[str, Any]:
        """落幂等记录：只有真正生效的提交才登记，失败重试不应被吞掉。"""
        if key and result.get("ok"):
            rows = store.rows(IDEMPOTENT_MODULE)
            rows.append({"id": len(rows) + 1, "key": key, "result": dict(result)})
        return result
