import pytest
from pydantic import ValidationError

from memhub.types import EntityRef, Fact, Preference, TypeRegistry


def test_builtin_types_registered_at_version_1():
    reg = TypeRegistry()
    assert reg.get("fact", 1) is Fact
    assert reg.latest_version("fact") == 1
    assert "fact" in reg
    assert "nope" not in reg


def test_unknown_type_version_raises():
    reg = TypeRegistry()
    with pytest.raises(KeyError):
        reg.get("fact", 99)


def test_register_path_custom_type():
    reg = TypeRegistry()
    reg.register_path("preference", "memhub.types:Preference", schema_version=2)
    assert reg.get("preference", 2) is Preference


def test_entity_ref_requires_type_and_id():
    ref = EntityRef(type="machine", id="M-1")
    assert ref.type == "machine"


def test_memory_content_required():
    with pytest.raises(ValidationError):
        Fact()


def test_preference_requires_key():
    with pytest.raises(ValidationError):
        Preference(content="likes dark mode")
    Preference(content="likes dark mode", key="theme")


def test_plan_is_not_a_registered_type():
    assert "plan" not in TypeRegistry()
