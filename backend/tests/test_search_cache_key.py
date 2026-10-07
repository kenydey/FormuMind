"""P1-2: federated search cache key includes requirement fingerprint."""
from app.domain.schemas import ProductDomain, Requirement
from app.services.literature import _search_cache_key


def _req(substrate="carbon_steel"):
    return Requirement(domain=ProductDomain.anticorrosion_coating, substrate=substrate)


def test_cache_key_differs_by_substrate():
    """同一 query 换 substrate → key 不同，不命中脏缓存。"""
    k1 = _search_cache_key("防腐涂料", ["patent"], 10, 5, "anticorrosion_coating", _req("carbon_steel"))
    k2 = _search_cache_key("防腐涂料", ["patent"], 10, 5, "anticorrosion_coating", _req("aluminum"))
    assert k1 != k2, "substrate 不同必须产出不同 key"


def test_cache_key_stable_for_same_req():
    k1 = _search_cache_key("q", None, 10, 5, "d", _req("carbon_steel"))
    k2 = _search_cache_key("q", None, 10, 5, "d", _req("carbon_steel"))
    assert k1 == k2


def test_cache_key_without_req_still_works():
    # req=None（旧调用）不炸，且与有 req 的 key 不同
    k1 = _search_cache_key("q", None, 10, 5, "d", None)
    k2 = _search_cache_key("q", None, 10, 5, "d", _req("carbon_steel"))
    assert k1 != k2


def test_cache_key_differs_by_notebook_id():
    """v14-2: 独立形参 notebooklm_notebook_id 必须进 key，防跨 notebook 脏缓存。"""
    k1 = _search_cache_key("q", ["notebooklm"], 10, 5, "d", None,
                           notebooklm_notebook_id="nb-A")
    k2 = _search_cache_key("q", ["notebooklm"], 10, 5, "d", None,
                           notebooklm_notebook_id="nb-B")
    assert k1 != k2, "不同 notebook 必须产出不同 key"
    k3 = _search_cache_key("q", ["notebooklm"], 10, 5, "d", None,
                           notebooklm_notebook_id="nb-A")
    assert k1 == k3, "同 notebook key 稳定"


def test_v15_search_cache_defensive_copy():
    """v15: _search_cache_get 返回浅拷贝，调用方原地修改不污染缓存。"""
    from app.services import literature

    literature._search_cache_put("v15k", ["a", "b"], {"x": 1})
    f1, p1 = literature._search_cache_get("v15k", 600)
    assert f1 is not None
    f1.append("c")
    p1["y"] = 2
    f2, p2 = literature._search_cache_get("v15k", 600)
    assert f2 == ["a", "b"], "缓存 list 不应被调用方污染"
    assert p2 == {"x": 1}, "缓存 payload 不应被调用方污染"


def test_v15_federated_search_notebook_param():
    """v15: FederatedSearchEngine.search 接受 notebooklm_notebook_id 形参。"""
    import inspect

    from app.services.federated_search import FederatedSearchEngine

    assert "notebooklm_notebook_id" in inspect.signature(
        FederatedSearchEngine.search
    ).parameters
