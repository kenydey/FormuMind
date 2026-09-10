### Task 1: Config + EnvFlag

**Files:**
- Modify: `backend/app/config.py` (near other `chat_*` fields ~498鈥?08)
- Modify: `backend/app/services/env_flags.py` (chem section after `chemtools_enabled`)
- Test: `backend/tests/test_env_flags.py` (extend registry assertion if present)

**Interfaces:**
- Produces: `Settings.chat_chem_tools_enabled: bool = True`, `Settings.chat_chem_tools_max_rounds: int = 4`; EnvFlag id `chat_chem_tools_enabled`

- [ ] **Step 1: Write failing assertion for new flag id**

In `backend/tests/test_env_flags.py`, add:

```python
def test_chat_chem_tools_flag_registered():
    ids = {f.id for f in env_flags.FLAG_REGISTRY}
    assert "chat_chem_tools_enabled" in ids
```

- [ ] **Step 2: Run test 鈥?expect FAIL**

Run: `cd backend && python -m pytest tests/test_env_flags.py::test_chat_chem_tools_flag_registered -v`  
Expected: FAIL (id not in registry)

- [ ] **Step 3: Add Settings fields**

In `backend/app/config.py` after `chat_multi_turn_enabled` / chat block:

```python
    # Chat native chem tool-calling (OpenAI-compatible tools 鈫?chemtools / SureChemBL / OCSR).
    chat_chem_tools_enabled: bool = True
    chat_chem_tools_max_rounds: int = 4
```

- [ ] **Step 4: Register EnvFlag**

In `env_flags.py` after `chemtools_enabled`:

```python
    EnvFlag("chat_chem_tools_enabled", "鑱婂ぉ鍖栧 Tool Calling",
            "瀵硅瘽涓€氳繃渚涘簲鍟嗗師鐢?tools 鑷姩璋冪敤 chemtools / SureChemBL / RDKit 缁撴瀯妫€绱?/ MolScribe銆?
            "浠?OpenAI 鍏煎渚涘簲鍟嗙敓鏁堬紱鍏抽棴鍚庤亰澶╅潤榛樼洿绛斻€?,
            "chem", "闇€ chemtools_enabled锛汳olScribe/SureChemBL 鍙﹀彈鍚勮嚜寮€鍏崇害鏉?),
```

- [ ] **Step 5: Run test 鈥?expect PASS**

Run: `cd backend && python -m pytest tests/test_env_flags.py::test_chat_chem_tools_flag_registered -v`

- [ ] **Step 6: Commit**

```bash
git add backend/app/config.py backend/app/services/env_flags.py backend/tests/test_env_flags.py
git commit -m "feat(config): add chat_chem_tools_enabled flag and max_rounds"
```

---

