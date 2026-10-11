"""下一轮三项测试：硬件自适应 OCSR + BigSMILES + 反应式解析。"""
import pytest

from app.services import ocsr
from app.services import bigsmiles_adapter as bsa
from app.services import reaction_parser as rp
from app.services import molparser_mobile as mpm
from app.services import rxnim_adapter as rxnim


class TestHardwareDetection:
    def test_detect_returns_dict(self):
        hw = ocsr.detect_hardware_tier()
        assert "gpu" in hw
        assert "mem_gb" in hw
        assert "tier" in hw
        assert hw["tier"] in ("gpu", "high-cpu", "low-cpu", "minimal")

    def test_resolve_backend_off(self, monkeypatch):
        from app.config import get_settings
        s = get_settings()
        old = s.chem_vision_backend
        s.chem_vision_backend = "off"
        try:
            assert ocsr.resolve_vision_backend() == "off"
        finally:
            s.chem_vision_backend = old

    def test_resolve_backend_pin(self, monkeypatch):
        from app.config import get_settings
        s = get_settings()
        old = s.chem_vision_backend
        s.chem_vision_backend = "vision-llm"
        try:
            assert ocsr.resolve_vision_backend() == "vision-llm"
        finally:
            s.chem_vision_backend = old

    def test_predict_off_returns_none(self, monkeypatch):
        from app.config import get_settings
        s = get_settings()
        old = s.chem_vision_backend
        s.chem_vision_backend = "off"
        try:
            assert ocsr.predict_smiles_local("/nonexistent.png") is None
        finally:
            s.chem_vision_backend = old

    def test_availability_has_hardware(self):
        av = ocsr.availability()
        assert "hardware" in av
        assert "vision_backend_resolved" in av


class TestBigSMILES:
    def test_is_bigsmiles(self):
        assert bsa.is_bigsmiles("{[][<]CC[>][]}") is True
        assert bsa.is_bigsmiles("CCO") is False
        assert bsa.is_bigsmiles("") is False

    def test_validate_no_lib(self):
        # 无 bigsmiles 库时语法粗判通过
        r = bsa.validate_bigsmiles("{[][<]CC[>][]}")
        assert r["valid"] is True
        r = bsa.validate_bigsmiles("CCO")
        assert r["valid"] is False

    def test_repeat_unit_no_lib(self):
        assert bsa.repeat_unit_smiles("{[][<]CC[>][]}") is None
        assert bsa.repeat_unit_mw("{[][<]CC[>][]}") is None


class TestReactionParser:
    def test_looks_like_reaction(self):
        assert rp.looks_like_reaction("A + B → C, yield 85%, reflux 2h") is True
        assert rp.looks_like_reaction("This is a normal sentence.") is False
        assert rp.looks_like_reaction("") is False

    def test_parse_not_reaction(self):
        r = rp.parse_reaction_text("Hello world")
        assert r["parsed"] is False

    def test_parse_no_api_key(self, monkeypatch):
        from app.config import get_settings
        s = get_settings()
        old_ds = s.deepseek_api_key
        old_oai = s.openai_api_key
        s.deepseek_api_key = ""
        s.openai_api_key = ""
        try:
            r = rp.parse_reaction_text("A + B → C with catalyst, yield 90%")
            assert r["parsed"] is False
            assert r["reason"] == "no LLM API key"
        finally:
            s.deepseek_api_key = old_ds
            s.openai_api_key = old_oai


class TestPOCAdapters:
    def test_molparser_mobile_not_deployed(self):
        assert mpm.molparser_mobile_available() is False
        assert mpm.predict_smiles_molparser_mobile("/nonexistent.png") is None

    def test_rxnim_not_deployed(self):
        assert rxnim.rxnim_available() is False
        r = rxnim.parse_reaction_image("/nonexistent.png")
        assert r["parsed"] is False
