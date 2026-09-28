"""B-3 / B-4 / B-13 回归测试：Smart Collections 存储层加固。

- B-3：并发 create 不丢更新；半截 JSON 不清空整库（隔离备份 + 拒绝写回）。
- B-4：后台刷新在途时 GET list 不被长时间阻塞。
- B-13：PATCH 部分更新语义（schedule exclude_unset；filters 合并）。
"""
from __future__ import annotations

import json
import threading
import time

import pytest
from fastapi.testclient import TestClient

from app.api import smart_collections as api_sc
from app.config import get_settings
from app.services import smart_collections as sc
from app.services import literature_manifest as lm


@pytest.fixture()
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(sc, "_data_root", lambda: tmp_path)
    monkeypatch.setattr(lm, "_data_root", lambda: tmp_path)
    monkeypatch.setenv("FORMUMIND_API_AUTH_ENABLED", "false")
    get_settings.cache_clear()
    yield tmp_path
    get_settings.cache_clear()


def _client() -> TestClient:
    from app.main import app

    return TestClient(app)


# ── B-3 ──────────────────────────────────────────────────────────────────


def test_b3_concurrent_create_no_lost_updates(data_dir):
    """20 线程并发 create_collection：一条都不能丢。"""
    n = 20
    errors: list[BaseException] = []

    def make(i: int) -> None:
        try:
            sc.create_collection("p-b3-conc", name=f"c-{i}", query=f"q-{i}")
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=make, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"并发 create 出错: {errors!r}"
    cols = sc.list_collections("p-b3-conc")
    assert len(cols) == n, f"丢更新：期望 {n} 条，实际 {len(cols)} 条"
    assert len({c["collection_id"] for c in cols}) == n
    # 落盘文件是完整合法 JSON
    raw = json.loads(sc.collections_path("p-b3-conc").read_text(encoding="utf-8"))
    assert len(raw["collections"]) == n


def test_b3_corrupt_file_never_wipes_store(data_dir, caplog):
    """半截 JSON 落盘：隔离备份 + error 日志；写事务拒绝把空 store 写回去。"""
    pid = "p-b3-corrupt"
    sc.create_collection(pid, name="keep-1", query="q1")
    sc.create_collection(pid, name="keep-2", query="q2")
    path = sc.collections_path(pid)
    good = path.read_bytes()

    # 模拟撕裂写后落盘的半截文件
    torn = b'{"collections": [{"collection_id": "x"'
    path.write_bytes(torn)

    with caplog.at_level("ERROR", logger="app.services.smart_collections"):
        cols = sc.list_collections(pid)
    assert cols == []  # 读路径返回空，但不写回
    backups = list(path.parent.glob("collections.json.corrupt-*"))
    assert len(backups) == 1, "损坏文件必须隔离为 .corrupt-* 备份"
    assert backups[0].read_bytes() == torn, "备份必须原样保留损坏内容以便取证"
    assert "corrupt" in caplog.text.lower(), "必须打 error 日志"

    # 写路径：拒绝把空 store 写回去，而不是静默建空库覆盖损坏文件
    with pytest.raises(sc.StoreCorruptError):
        sc.create_collection(pid, name="new", query="q3")
    assert not path.exists(), "绝不把空 store 写回去"

    # 运维从备份/快照恢复好文件后，写保护自动解除，数据不丢
    path.write_bytes(good)
    sc.create_collection(pid, name="new", query="q3")
    assert {c["name"] for c in sc.list_collections(pid)} == {
        "keep-1",
        "keep-2",
        "new",
    }


def test_b3_concurrent_read_write_never_sees_torn_json(data_dir):
    """读写并发：读方永远看不到半截 JSON（原子写），且无更新丢失。"""
    pid = "p-b3-rw"
    cid = sc.create_collection(pid, name="c", query="q")["collection_id"]
    stop = threading.Event()
    errors: list[BaseException] = []

    def writer(i: int) -> None:
        try:
            n = 0
            while not stop.is_set():
                sc.update_collection(pid, cid, name=f"c-{i}-{n}")
                n += 1
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    def reader() -> None:
        try:
            while not stop.is_set():
                sc.list_collections(pid)
                assert sc.get_collection(pid, cid) is not None
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=writer, args=(i,)) for i in range(4)]
    threads += [threading.Thread(target=reader) for _ in range(4)]
    for t in threads:
        t.start()
    time.sleep(2.0)
    stop.set()
    for t in threads:
        t.join()

    assert not errors, f"并发读写出错: {errors!r}"
    path = sc.collections_path(pid)
    assert not list(path.parent.glob("collections.json.corrupt-*")), (
        "读到了半截 JSON：原子写被破坏"
    )
    # 最终文件合法且只有一条集合（更新不丢、也不多）
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert len(raw["collections"]) == 1


# ── B-4 ──────────────────────────────────────────────────────────────────


def test_b4_get_list_not_blocked_by_inflight_refresh(data_dir, monkeypatch):
    """后台刷新在途（含分钟级搜索）时，GET list 不被长时间阻塞。"""
    search_sleep_s = 5.0

    def slow_search(query, filters, settings=None):
        time.sleep(search_sleep_s)
        return []

    monkeypatch.setattr(sc, "_run_search", slow_search)
    c = _client()

    r = c.post(
        "/api/collections",
        json={"project_id": "p-b4", "name": "auto", "query": "epoxy coating"},
    )
    assert r.status_code == 201, r.text
    cid = r.json()["collection_id"]

    # 第一次 GET：触发后台刷新，本身立即返回
    r = c.get("/api/collections", params={"project_id": "p-b4"})
    assert r.status_code == 200

    key = ("p-b4", cid)
    deadline = time.time() + 10
    while key not in api_sc._AUTO_REFRESH_IN_FLIGHT and time.time() < deadline:
        time.sleep(0.05)
    assert key in api_sc._AUTO_REFRESH_IN_FLIGHT, "后台刷新线程未进入 in-flight"

    # 刷新在途（5s 搜索）时第二次 GET：旧实现会卡 ~5s（同一把锁串行化耗时搜索）
    t0 = time.monotonic()
    r = c.get("/api/collections", params={"project_id": "p-b4"})
    dt = time.monotonic() - t0
    assert r.status_code == 200
    assert dt < 3.0, f"GET list 被阻塞了 {dt:.1f}s（搜索耗时 {search_sleep_s}s）"

    # 等后台刷新完成，避免 tmp_path 回收与 daemon 线程竞态
    deadline = time.time() + 20
    while key in api_sc._AUTO_REFRESH_IN_FLIGHT and time.time() < deadline:
        time.sleep(0.1)
    assert key not in api_sc._AUTO_REFRESH_IN_FLIGHT, "后台刷新未完成"
    col = sc.get_collection("p-b4", cid)
    assert (col.get("schedule") or {}).get("last_run") is not None
    assert len(col.get("snapshots") or []) >= 1


# ── B-13 ─────────────────────────────────────────────────────────────────


def test_b13_patch_schedule_partial_update(data_dir):
    """PATCH schedule 只传 enabled 时，interval_hours 必须保留。"""
    c = _client()
    r = c.post(
        "/api/collections",
        json={
            "project_id": "p-b13",
            "name": "c",
            "query": "q",
            "schedule": {"enabled": True, "interval_hours": 12},
        },
    )
    assert r.status_code == 201, r.text
    cid = r.json()["collection_id"]
    assert r.json()["schedule"]["interval_hours"] == 12

    r = c.patch(
        f"/api/collections/{cid}",
        params={"project_id": "p-b13"},
        json={"schedule": {"enabled": False}},
    )
    assert r.status_code == 200, r.text
    sched = r.json()["schedule"]
    assert sched["enabled"] is False
    assert sched["interval_hours"] == 12, "未提供的 interval_hours 被模型默认值覆盖"


def test_b13_patch_filters_merge_not_replace(data_dir):
    """PATCH filters 只传一个键时，其他键保留；显式 null 清掉该键。"""
    c = _client()
    r = c.post(
        "/api/collections",
        json={
            "project_id": "p-b13",
            "name": "c",
            "query": "q",
            "filters": {
                "date_from": 2020,
                "date_to": 2025,
                "domain_allowlist": ["a.com"],
            },
        },
    )
    assert r.status_code == 201, r.text
    cid = r.json()["collection_id"]

    # 只传 date_from：其余保留
    r = c.patch(
        f"/api/collections/{cid}",
        params={"project_id": "p-b13"},
        json={"filters": {"date_from": 2021}},
    )
    assert r.status_code == 200, r.text
    filters = r.json()["filters"]
    assert filters["date_from"] == 2021
    assert filters["date_to"] == 2025, "未提供的 date_to 被清空（全量替换）"
    assert filters["domain_allowlist"] == ["a.com"]

    # 显式 null：只清该键
    r = c.patch(
        f"/api/collections/{cid}",
        params={"project_id": "p-b13"},
        json={"filters": {"date_from": None}},
    )
    assert r.status_code == 200, r.text
    filters = r.json()["filters"]
    assert "date_from" not in filters
    assert filters["date_to"] == 2025
    assert filters["domain_allowlist"] == ["a.com"]
