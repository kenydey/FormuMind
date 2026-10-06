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
