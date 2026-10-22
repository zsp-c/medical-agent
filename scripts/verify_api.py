"""后端接口全量验证脚本（临时用）：逐项断言并打印结果表。运行：.venv python verify_api.py"""

import json
import time
import uuid

import requests

BASE = "http://127.0.0.1:8000"
USER = f"verify_{uuid.uuid4().hex[:6]}"
PWD = "verify-pass-123"

results = []


def check(name, ok, detail=""):
    results.append((name, ok, detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}  {detail}")


# 0. health
r = requests.get(f"{BASE}/api/health", timeout=5)
check("GET /api/health 免鉴权", r.status_code == 200 and r.json().get("status") == "ok")

# 1. 注册
r = requests.post(f"{BASE}/api/auth/register", json={"username": USER, "password": PWD}, timeout=10)
check("POST /api/auth/register -> 201", r.status_code == 201, f"status={r.status_code}")
token = r.json()["access_token"] if r.status_code == 201 else None
user_id = r.json().get("user_id", "") if r.status_code == 201 else ""
H = {"Authorization": f"Bearer {token}"}

# 2. 重复注册 -> 409
r = requests.post(f"{BASE}/api/auth/register", json={"username": USER, "password": PWD}, timeout=10)
check("重复注册 -> 409", r.status_code == 409)

# 3. 登录
r = requests.post(f"{BASE}/api/auth/login", json={"username": USER, "password": PWD}, timeout=10)
check("POST /api/auth/login -> 200", r.status_code == 200 and "access_token" in r.json())

# 4. 错误密码 -> 401
r = requests.post(f"{BASE}/api/auth/login", json={"username": USER, "password": "wrong-pass-123"}, timeout=10)
check("错误密码 -> 401", r.status_code == 401)

# 5. me
r = requests.get(f"{BASE}/api/auth/me", headers=H, timeout=5)
check("GET /api/auth/me -> 200", r.status_code == 200 and r.json().get("username") == USER, str(r.json()))

# 6. 无令牌 -> 401
r = requests.get(f"{BASE}/api/sessions", timeout=5)
check("无令牌 -> 401", r.status_code == 401)

# 7. 实体详情
r = requests.get(f"{BASE}/api/entities/disease:hypertension", headers=H, timeout=15)
d = r.json()
check("实体详情 found=true", r.status_code == 200 and d.get("found") is True,
      f"name_zh={d.get('name_zh')} degree={d.get('degree')}")

# 8. 实体不存在
r = requests.get(f"{BASE}/api/entities/disease:nope", headers=H, timeout=15)
check("实体不存在 found=false", r.status_code == 200 and r.json().get("found") is False)

# 9. 子图
r = requests.get(f"{BASE}/api/entities/disease:hypertension/subgraph?depth=1&limit=10", headers=H, timeout=15)
d = r.json()
check("子图 nodes>0", r.status_code == 200 and len(d.get("nodes", [])) > 0,
      f"nodes={len(d.get('nodes', []))} edges={len(d.get('edges', []))} truncated={d.get('truncated')}")

# 10. 子图参数越界 -> 422
r = requests.get(f"{BASE}/api/entities/disease:hypertension/subgraph?depth=5", headers=H, timeout=15)
check("子图 depth=5 -> 422", r.status_code == 422)

# 11. 清空记忆
r = requests.delete(f"{BASE}/api/users/me/memory", headers=H, timeout=30)
check("清空记忆 -> ok", r.status_code == 200 and "ok" in r.json(), str(r.json()))

# 12. 会话列表（新用户为空）
r = requests.get(f"{BASE}/api/sessions", headers=H, timeout=10)
check("新用户会话列表为空", r.status_code == 200 and r.json().get("total") == 0)

# 13. 不存在会话历史 -> 404
r = requests.get(f"{BASE}/api/sessions/ghost/messages", headers=H, timeout=10)
check("不存在会话 -> 404", r.status_code == 404)

# 14. 真实对话（非流式，LLM 约 20~120s）
sid = uuid.uuid4().hex
t0 = time.time()
r = requests.post(
    f"{BASE}/api/chat",
    headers={**H, "Content-Type": "application/json"},
    json={"session_id": sid, "query": "二甲双胍治什么病？"},
    timeout=400,
)
elapsed = time.time() - t0
if r.status_code == 200:
    d = r.json()
    ev = [e.get("name_zh") or e.get("name") for e in d.get("evidence_entities", [])]
    check("POST /api/chat -> 200", bool(d.get("answer")),
          f"{elapsed:.0f}s, 证据={ev}, meta={d.get('meta')}")
else:
    check("POST /api/chat -> 200", False, f"status={r.status_code} body={r.text[:200]}")

# 15. 会话历史（应有 2 条消息）
r = requests.get(f"{BASE}/api/sessions/{sid}/messages", headers=H, timeout=10)
d = r.json()
check("会话历史 2 条消息", r.status_code == 200 and len(d.get("messages", [])) == 2,
      f"messages={len(d.get('messages', []))}")

# 16. SSE 流式（用新会话，验证事件流格式）
sid2 = uuid.uuid4().hex
events = []
with requests.post(
    f"{BASE}/api/chat/stream",
    headers={**H, "Content-Type": "application/json", "Accept": "text/event-stream"},
    json={"session_id": sid2, "query": "高血压有什么症状？"},
    stream=True,
    timeout=400,
) as r:
    check("POST /api/chat/stream -> 200", r.status_code == 200, f"status={r.status_code}")
    cur_event = None
    for line in r.iter_lines(decode_unicode=True):
        if not line:
            continue
        if line.startswith(":"):
            continue
        if line.startswith("event:"):
            cur_event = line[6:].strip()
            events.append(cur_event)
        elif line.startswith("data:") and cur_event == "done":
            pass  # done 的数据体大，不解析

kinds = {}
for e in events:
    kinds[e] = kinds.get(e, 0) + 1
check("SSE 事件序列完整（含 delta 与 done）", "done" in kinds and "delta" in kinds,
      f"事件统计={kinds}")

# 17. 他人会话 -> 403（alice 访问本测试用户的会话）
r = requests.post(f"{BASE}/api/auth/login", json={"username": "alice", "password": "alice-pass-123"}, timeout=10)
if r.status_code == 200:
    H2 = {"Authorization": f"Bearer {r.json()['access_token']}"}
    r = requests.get(f"{BASE}/api/sessions/{sid}/messages", headers=H2, timeout=10)
    check("他人会话 -> 403", r.status_code == 403)
else:
    check("他人会话 -> 403", False, "alice 登录失败，跳过")

# 18. 删除会话
r = requests.delete(f"{BASE}/api/sessions/{sid}", headers=H, timeout=10)
check("删除会话 -> ok", r.status_code == 200 and r.json().get("ok") is True, str(r.json()))
r = requests.get(f"{BASE}/api/sessions/{sid}/messages", headers=H, timeout=10)
check("删除后再查 -> 404", r.status_code == 404)
requests.delete(f"{BASE}/api/sessions/{sid2}", headers=H, timeout=10)

print()
failed = [n for n, ok, _ in results if not ok]
print(f"==== 汇总：{len(results) - len(failed)}/{len(results)} 通过" + (f"，失败：{failed}" if failed else " ===="))
