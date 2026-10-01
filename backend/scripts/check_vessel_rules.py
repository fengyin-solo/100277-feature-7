"""船舶作业单向作业链规则验证：直接用 TestClient 逐条跑需求。

运行：PYTHONPATH=/workspace/backend/.pydeps python3 scripts/check_vessel_rules.py
"""
from __future__ import annotations

import sys

from fastapi.testclient import TestClient

sys.path.insert(0, "/workspace/backend")

from app.main import app  # noqa: E402

client = TestClient(app)
fails: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f" —— {detail}" if detail and not cond else ""))
    if not cond:
        fails.append(name)


def act(vessel_id: int, action: str):
    return client.post(f"/api/vessel/{vessel_id}/actions", json={"values": {"action": action}})


def batch(vessel_id: int, action: str, item_ids, batch_no=None):
    payload = {"action": action, "item_ids": item_ids}
    if batch_no:
        payload["batch_no"] = batch_no
    return client.post(f"/api/vessel/{vessel_id}/items/actions", json=payload)


def statuses(vessel_id: int):
    rows = client.get(f"/api/vessel/{vessel_id}/items").json()["items"]
    return {row["id"]: row["status"] for row in rows}


# ---------- 1. 船舶作业链：只能顺移一格，不能跳级、不能回退
r = act(1, "开始作业")  # 锚地待泊 → 直接作业中
check("1a 船舶不允许从锚地待泊跳到作业中", r.status_code == 200 and not r.json()["ok"], r.text)
r = act(1, "确认离泊")  # 锚地待泊 → 直接离泊
check("1b 船舶不允许从锚地待泊跳到已离泊", not r.json()["ok"])
r = act(2, "安排靠泊")  # 靠泊中重复靠泊
check("1c 靠泊中不允许重复安排靠泊", not r.json()["ok"])
r = act(2, "开始作业")  # 靠泊中 → 作业中，合法
check("1d 靠泊中 → 作业中 合法", r.json()["ok"] and r.json()["entry"]["status"] == "作业中")
r = act(2, "安排靠泊")  # 已作业中 → 想退回靠泊中
check("1e 已作业中不允许退回靠泊中", not r.json()["ok"])

# ---------- 2. 条目链只能顺一格，跳级/重复提交被拦，且原子性
# vessel 2 现在作业中，条目 5-8 待开工
r = batch(2, "核对", [5, 6], batch_no="b-skip")  # 待开工直接核对
check("2a 待开工条目不允许跳过开工直接核对", not r.json()["ok"])
r = batch(2, "开工", [5, 6, 7], batch_no="b-open-1")
check("2b 同船多选中整组开工合法", r.json()["ok"] and r.json()["entry"]["item_ids"] == [5, 6, 7])
r = batch(2, "开工", [5, 6, 7], batch_no="b-open-1")  # 同批次号重放
check("2c 同一批次重复提交只保留一条(幂等)", r.json()["ok"] and "已经提交过" in r.json()["message"])
st = statuses(2)
r = batch(2, "开工", [5, 8], batch_no="b-open-mix")  # 5已作业中，8待开工 → 原子驳回
check("2d 组内一个不能推进则整组驳回(原子)", not r.json()["ok"])
st2 = statuses(2)
check("2e 原子驳回后 8 仍停留在待开工", st2[8] == "待开工", str(st2))
r = batch(2, "核对", [5, 6], batch_no="b-check-1")
check("2f 作业中 → 已核对 合法", r.json()["ok"])
check("2g 核对后 7 仍为作业中(只动被选条目)", statuses(2)[7] == "作业中")

# ---------- 3. 整组提交只允许同一条船；空选择不行
r = batch(3, "开工", [7, 12], batch_no="b-cross")  # 7 属于 vessel 2
check("3a 跨船选择整组提交被驳回", not r.json()["ok"] and "不属于本条船" in r.json()["message"])
r = batch(3, "开工", [], batch_no="b-empty")
check("3b 空选择不允许提交", not r.json()["ok"])
r = batch(999, "开工", [9], batch_no="b-noship")
check("3c 不存在的船舶提交报错", not r.json()["ok"])
# 去重：item_ids 列表内重复 id 不产生重复效果
r = batch(3, "开工", [12, 12, 13], batch_no="b-dup-in-req")
check("3d 请求内重复 id 自动去重后正常推进", r.json()["ok"] and r.json()["entry"]["item_ids"] == [12, 13])

# ---------- 4. 没核对完不许确认离泊
r = act(3, "确认离泊")
check("4a V3 还有未核对条目，离泊被拦", not r.json()["ok"] and "未核对" in r.json()["message"])
# vessel 4 全部已核对，可以离泊
before_todos = client.get("/api/vessel/todos").json()["total"]
r = act(4, "确认离泊")
check("4b V4 全部已核对，确认离泊成功", r.json()["ok"] and r.json()["entry"]["status"] == "已离泊")

# ---------- 5. 离泊回写台账待办
todos = client.get("/api/vessel/todos?vessel_id=4").json()["items"]
check("5a 离泊后回写一条台账待办", len(todos) == 1 and todos[0]["事项"] == "跟进离泊", str(todos))
detail = client.get("/api/vessel/4").json()
check("5b 船舶明细带待办数且已离泊", detail["待办数"] == 1 and detail["status"] == "已离泊")
check("5c 回写箱量与明细一致(432+146=588)", todos[0]["作业箱量合计"] == 588, str(todos[0]))

# ---------- 6. 已离泊：不许退回作业中，条目锁定（不能开工/核对/打回）
r = act(4, "开始作业")
check("6a 已离泊不允许退回作业中", not r.json()["ok"])
r = batch(4, "开工", [14], batch_no="b-after-depart")
check("6b 已离泊船条目不允许再整组提交", not r.json()["ok"])
r = client.post("/api/vessel/items/14/reject")
check("6c 已离泊船条目不允许打回", not r.json()["ok"])

# ---------- 7. 打回只退回它自己
r = client.post("/api/vessel/items/10/reject")  # V3 作业中 → 待开工
check("7a 单个条目打回成功", r.json()["ok"] and r.json()["entry"]["status"] == "待开工")
st = statuses(3)
check("7b 打回不影响同船其他条目(9仍已核对,11仍作业中)", st[9] == "已核对" and st[11] == "作业中", str(st))
# 打回后重新开工是合法的新动作（不能被「同批去重」误杀）
r = batch(3, "开工", [10], batch_no="b-reopen-10")
check("7c 打回后可以重新开工重走", r.json()["ok"] and statuses(3)[10] == "作业中")
r = client.post("/api/vessel/items/1/reject")
check("7d 待开工条目无需打回", not r.json()["ok"])

# ---------- 8. 航线代码空不许提交（登记 & 离泊动作前也已保证非空）
r = client.post("/api/vessel", json={"values": {"船舶编号": "V-X", "船名": "x", "船公司": "c", "航线代码": ""}})
check("8a 登记时空航线代码被拒", not r.json()["ok"] and "航线代码" in r.json()["message"])
r = client.post("/api/vessel", json={"values": {"船舶编号": "V-X2", "船名": "x", "船公司": "c", "航线代码": "CN-XX-9"}})
check("8b 登记时航线代码齐全通过", r.json()["ok"])

# ---------- 9. 看板箱量与作业明细同源
board = client.get("/api/vessel/board").json()
items_all = []
for vid in range(1, 6):
    items_all += client.get(f"/api/vessel/{vid}/items").json()["items"]
same_sum = sum(i["作业箱量"] for i in items_all)
check("9a 看板总箱量=明细箱量之和", board["总作业箱量"] == same_sum == 2161, str(board))
v3_list = next(x for x in client.get("/api/vessel").json()["items"] if x["id"] == 3)
v3_items_sum = sum(i["作业箱量"] for i in items_all if i["所属船舶"] == 3)
check("9b 列表各船箱量与看板明细同源", v3_list["作业箱量合计"] == v3_items_sum == 553, str(v3_list))
check("9c 看板条目总数=22", board["作业条目总数"] == 22)
check("9d 待核对条目不计已离泊船(V1=4,V2=2,V3=4)", board["待核对条目数"] == 10, str(board))

print()
if fails:
    print(f"{len(fails)} 条未通过：")
    for f in fails:
        print(" -", f)
    sys.exit(1)
print("全部规则通过")
