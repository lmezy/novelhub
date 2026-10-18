"""Saved model provider list on 设置 → AI.

The AI page used to hold exactly one provider: switching vendors meant retyping
the endpoint, the model and the key.  These tests pin the list that replaced it,
including the two things that are easy to get wrong -- keys stay encrypted at
rest and are never echoed back, and a client that only speaks the old
single-provider fields keeps editing the provider that is actually in use.
"""

import json
from types import SimpleNamespace

import pytest
from sqlalchemy import select  # noqa: F401  (kept for the fake db's contract)

from app.services.ai_config import (
    AI_ACTIVE_PROFILE_KEY,
    AI_API_KEY_KEY,
    AI_MODEL_KEY,
    AI_PROFILES_KEY,
    AI_PROVIDER_KEY,
    MASKED_KEY,
    get_ai_config,
    parse_profiles,
    resolve_ai_config,
    set_ai_config,
)
from app.models.app_setting import AppSetting
from app.services.cookie_crypto import encrypt_cookie


class FakeDB:
    """Enough of an ``AsyncSession`` for the settings reader/writer."""

    def __init__(self, rows=None):
        self.rows = list(rows or [])

    async def scalars(self, _statement):
        return list(self.rows)

    def add(self, obj):
        self.rows.append(obj)

    async def commit(self):
        return None

    def value_of(self, key):
        for row in self.rows:
            if row.key == key:
                return row.value
        return None


def row(key, value):
    return SimpleNamespace(key=key, value=value)


def profile_entry(**overrides):
    entry = {
        "id": "p1",
        "name": "DeepSeek 主力",
        "provider": "deepseek",
        "base_url": "",
        "model": "deepseek-flash",
        "api_key": "sk-deepseek",
    }
    entry.update(overrides)
    return entry


def test_active_profile_supplies_the_vendor_fields():
    stored = {
        AI_PROFILES_KEY: json.dumps([
            profile_entry(),
            profile_entry(
                id="p2", name="本地 Ollama", provider="ollama",
                base_url="http://192.168.1.5:11434/v1", model="qwen2.5:7b",
                api_key="",
            ),
        ]),
        AI_ACTIVE_PROFILE_KEY: "p2",
    }

    cfg = resolve_ai_config(stored, env={})

    assert cfg.provider == "ollama"
    assert cfg.effective_base_url == "http://192.168.1.5:11434/v1"
    assert cfg.effective_model == "qwen2.5:7b"
    assert cfg.active_profile_id == "p2"
    # A keyless local runtime is still a usable configuration.
    assert cfg.configured is True

    public = cfg.public_dict()
    assert public["profile_source"] == "profiles"
    assert [profile["id"] for profile in public["profiles"]] == ["p1", "p2"]
    assert [profile["active"] for profile in public["profiles"]] == [False, True]
    # The list never carries a key, only the fact that one is stored.
    assert public["profiles"][0]["api_key_set"] is True
    assert public["profiles"][0]["api_key_hint"].startswith("sk-")
    assert "sk-deepseek" not in json.dumps(public)


def test_unknown_active_id_falls_back_to_the_first_profile():
    stored = {
        AI_PROFILES_KEY: json.dumps([profile_entry(), profile_entry(id="p2")]),
        AI_ACTIVE_PROFILE_KEY: "gone",
    }

    cfg = resolve_ai_config(stored, env={})

    assert cfg.active_profile_id == "p1"
    assert cfg.provider == "deepseek"


def test_broken_profile_json_falls_back_to_the_single_provider_keys():
    stored = {
        AI_PROFILES_KEY: "{not json",
        AI_PROVIDER_KEY: "qwen",
        AI_MODEL_KEY: "qwen-plus",
        AI_API_KEY_KEY: "sk-legacy",
    }

    cfg = resolve_ai_config(stored, env={})

    assert cfg.profiles == ()
    assert cfg.provider == "qwen"
    assert cfg.effective_model == "qwen-plus"
    assert cfg.public_dict()["profile_source"] == "single"


def test_parse_profiles_skips_entries_without_an_id_and_dedupes():
    raw = json.dumps([
        profile_entry(id=""),
        profile_entry(id="p1"),
        profile_entry(id="p1", name="重复的"),
        "not an object",
    ])

    profiles = parse_profiles(raw)

    assert [profile.id for profile in profiles] == ["p1"]
    assert profiles[0].name == "DeepSeek 主力"


@pytest.mark.asyncio
async def test_set_ai_config_stores_keys_encrypted_and_switches_the_active_entry():
    db = FakeDB()

    cfg = await set_ai_config(db, {
        "profiles": [
            profile_entry(),
            profile_entry(id="p2", name="Kimi", provider="moonshot",
                          model="moonshot-v1-8k", api_key="sk-moonshot"),
        ],
        "active_profile_id": "p2",
    })

    raw = db.value_of(AI_PROFILES_KEY)
    assert "sk-deepseek" not in raw and "sk-moonshot" not in raw
    assert raw.count("enc:") == 2
    assert db.value_of(AI_ACTIVE_PROFILE_KEY) == "p2"
    assert cfg.provider == "moonshot"
    assert cfg.api_key == "sk-moonshot"

    # Switching back only needs the id: the keys come from the stored list.
    switched = await set_ai_config(db, {"active_profile_id": "p1"})
    assert switched.provider == "deepseek"
    assert switched.api_key == "sk-deepseek"
    assert switched.effective_model == "deepseek-flash"


@pytest.mark.asyncio
async def test_mask_keeps_the_stored_key_and_an_empty_string_clears_it():
    db = FakeDB()
    await set_ai_config(db, {"profiles": [profile_entry()]})

    kept = await set_ai_config(db, {
        "profiles": [profile_entry(name="改名了", api_key=MASKED_KEY)],
    })
    assert kept.api_key == "sk-deepseek"
    assert kept.profiles[0].name == "改名了"

    cleared = await set_ai_config(db, {"profiles": [profile_entry(api_key="")]})
    assert cleared.api_key == ""
    assert cleared.profiles[0].api_key == ""


@pytest.mark.asyncio
async def test_single_provider_fields_update_the_active_profile():
    """An older client edits the provider in use, not a shadowed legacy row."""
    db = FakeDB()
    await set_ai_config(db, {
        "profiles": [profile_entry(), profile_entry(id="p2", provider="qwen")],
        "active_profile_id": "p1",
    })

    cfg = await set_ai_config(db, {"model": "deepseek-v4-pro", "api_key": "sk-new"})

    assert cfg.effective_model == "deepseek-v4-pro"
    assert cfg.api_key == "sk-new"
    stored = json.loads(db.value_of(AI_PROFILES_KEY))
    assert stored[0]["model"] == "deepseek-v4-pro"
    assert stored[1]["provider"] == "qwen"
    # The legacy keys are not shadow-written: the list stays the only owner.
    assert db.value_of(AI_MODEL_KEY) is None
    assert db.value_of(AI_API_KEY_KEY) is None


@pytest.mark.asyncio
async def test_without_profiles_the_single_provider_keys_still_work():
    db = FakeDB([row(AI_PROVIDER_KEY, "zhipu"), row(AI_MODEL_KEY, "glm-4-flash")])

    cfg = await set_ai_config(db, {"api_key": "sk-zhipu"})

    assert db.value_of(AI_API_KEY_KEY).startswith("enc:")
    assert cfg.provider == "zhipu"
    assert cfg.effective_model == "glm-4-flash"
    assert cfg.api_key == "sk-zhipu"
    assert (await get_ai_config(db)).api_key == "sk-zhipu"


@pytest.mark.asyncio
async def test_saving_the_first_list_keeps_the_single_providers_key():
    """Upgrading must not lose the key that was already configured.

    The UI shows the current single configuration as the first (unsaved)
    provider, and it cannot echo a key it never received -- so the server hands
    the stored single-provider key to that first entry.
    """
    db = FakeDB([
        row(AI_PROVIDER_KEY, "deepseek"),
        row(AI_API_KEY_KEY, "enc:" + encrypt_cookie("sk-legacy")),
    ])

    cfg = await set_ai_config(db, {
        "profiles": [
            {"id": "p1", "provider": "deepseek", "model": "deepseek-flash"},
            {"id": "p2", "name": "空的", "provider": "qwen"},
        ],
        "active_profile_id": "p1",
    })

    assert cfg.profiles[0].api_key == "sk-legacy"
    # Only the entry that replaces the single configuration inherits it.
    assert cfg.profiles[1].api_key == ""
    assert cfg.api_key == "sk-legacy"

    # Saving again without touching any key keeps it.
    again = await set_ai_config(db, {
        "profiles": [
            {"id": "p1", "provider": "deepseek", "model": "deepseek-v4-pro"},
            {"id": "p2", "name": "空的", "provider": "qwen"},
        ],
        "active_profile_id": "p1",
    })
    assert again.profiles[0].api_key == "sk-legacy"


@pytest.mark.asyncio
async def test_api_key_never_leaves_the_server():
    db = FakeDB()
    await set_ai_config(db, {"profiles": [profile_entry(api_key="sk-secret-value")]})

    public = (await get_ai_config(db)).public_dict()

    assert "sk-secret-value" not in json.dumps(public)
    assert public["profiles"][0]["api_key_hint"] == "sk-********alue"


def test_app_setting_rows_are_the_only_storage():
    """Guard the shape the fake db emulates: key/value columns only."""
    assert set(AppSetting.__table__.columns.keys()) >= {"key", "value"}
