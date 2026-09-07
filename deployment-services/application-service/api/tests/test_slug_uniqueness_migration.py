"""Migration 0026 scopes app names to the infrastructure. Two rows whose names derive the
same slug would collapse onto one namespace, so the migration must refuse to run."""
import importlib

import pytest

migration = importlib.import_module("api.migrations.0027_app_name_unique_per_infra")


def test_distinct_slugs_are_not_flagged():
    rows = [("a1", "i1", "api"), ("a2", "i1", "web"), ("a3", "i2", "api")]

    assert migration.find_duplicate_slugs(rows) == {}


def test_names_deriving_the_same_slug_on_one_infrastructure_are_flagged():
    rows = [("a1", "i1", "My App"), ("a2", "i1", "my app"), ("a3", "i2", "my app")]

    duplicates = migration.find_duplicate_slugs(rows)

    assert list(duplicates) == [("i1", "my-app")]
    assert duplicates[("i1", "my-app")] == [("a1", "My App"), ("a2", "my app")]


def test_the_migration_refuses_and_names_the_offending_rows():
    class FakeApplication:
        objects = type("Manager", (), {
            "values_list": staticmethod(lambda *_f: [("a1", "i1", "My App"), ("a2", "i1", "my-app")])
        })()

    apps = type("Apps", (), {"get_model": staticmethod(lambda *_a: FakeApplication)})()

    with pytest.raises(RuntimeError) as excinfo:
        migration.refuse_duplicate_slugs(apps, None)

    message = str(excinfo.value)
    assert "my-app" in message
    assert "a1 (My App)" in message
    assert "a2 (my-app)" in message


def test_the_migration_is_a_noop_when_slugs_are_unique():
    class FakeApplication:
        objects = type("Manager", (), {
            "values_list": staticmethod(lambda *_f: [("a1", "i1", "api"), ("a2", "i1", "web")])
        })()

    apps = type("Apps", (), {"get_model": staticmethod(lambda *_a: FakeApplication)})()

    assert migration.refuse_duplicate_slugs(apps, None) is None


def test_the_slug_transformation_is_frozen_in_the_migration():
    """The migration must reproduce the collision set as of when it was written, so it
    carries its own copy rather than importing the live helper. This asserts the copy is
    real (not a re-export) and still agrees with today's helper — if app_slug ever changes
    deliberately, this test is the reminder that the frozen copy stays as it is."""
    from api.common import naming

    assert migration._slug_at_0027 is not naming.app_slug
    for name in ("My App", "api", "web-2", "Trailing-", "UPPER.case_x"):
        assert migration._slug_at_0027(name) == naming.app_slug(name)
