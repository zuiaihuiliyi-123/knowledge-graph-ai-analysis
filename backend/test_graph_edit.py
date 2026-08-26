"""
教师手动编辑图谱端点端到端验证（新增/更新/删除节点、新增/删除关系）
用 course_id=5 的真实数据做完整 CRUD 闭环，测完清理临时节点，不留残留。
运行：cd backend && python test_graph_edit.py
"""
import sys

sys.stdout.reconfigure(encoding="utf-8")

from fastapi.testclient import TestClient
from app.main import app

c = TestClient(app)
CID = 5

# 1. 拿现有节点/边，取真实 kp_id / edge_id
g = c.get(f"/api/v1/graph/{CID}").json()
assert g["code"] == 0, g
nodes = g["data"]["nodes"]
edges = g["data"]["edges"]
print(f"[init] nodes={len(nodes)} edges={len(edges)}")
assert nodes, "course 5 无节点，无法测试"

node_id = nodes[0]["id"]          # 已有节点 kp_id
edge_id = edges[0]["id"] if edges else None

name = "临时测试知识点_xyz"
# 2. 新增节点
r = c.post(f"/api/v1/graph/{CID}/nodes", json={"name": name, "category": "概念", "description": "测试"}).json()
print("[create node]", r["code"], r.get("data"))
assert r["code"] == 0, r
new_kp = r["data"]["kp_id"]

# 3. 重复新增 → 应失败
r = c.post(f"/api/v1/graph/{CID}/nodes", json={"name": name}).json()
print("[dup node]", r["code"], r["message"])
assert r["code"] != 0

# 4. 更新节点（改名 + 改类别）
r = c.put(f"/api/v1/graph/{CID}/nodes/{new_kp}", json={"name": name + "_改", "category": "定理", "description": "改"}).json()
print("[update node]", r["code"], r["data"].get("name"), r["data"].get("category"))
assert r["code"] == 0 and r["data"]["name"] == name + "_改" and r["data"]["category"] == "定理"

# 5. 新增关系
r = c.post(f"/api/v1/graph/{CID}/edges", json={"source": new_kp, "target": node_id, "type": "RELATED_TO"}).json()
print("[create edge]", r["code"], r.get("data"))
assert r["code"] == 0, r
new_edge = r["data"]["edge_id"]

# 6. 非法关系类型 → 应失败
r = c.post(f"/api/v1/graph/{CID}/edges", json={"source": new_kp, "target": node_id, "type": "HACK"}).json()
print("[bad edge type]", r["code"], r["message"])
assert r["code"] != 0

# 7. 删除关系
r = c.delete(f"/api/v1/graph/{CID}/edges/{new_edge}").json()
print("[delete edge]", r["code"], r.get("data"))
assert r["code"] == 0

# 8. 删除节点（cleanup）
r = c.delete(f"/api/v1/graph/{CID}/nodes/{new_kp}").json()
print("[delete node]", r["code"], r.get("data"))
assert r["code"] == 0

# 9. 再删一次 → 应失败（已不存在）
r = c.delete(f"/api/v1/graph/{CID}/nodes/{new_kp}").json()
print("[delete missing]", r["code"], r["message"])
assert r["code"] != 0

print("\n✅ 教师编辑图谱端点全部通过")
