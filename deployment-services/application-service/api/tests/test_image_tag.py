"""Per-deploy image tags: EKS needs a new image reference or the Deployment never rolls."""
import re
from types import SimpleNamespace

from api.common.naming import app_slug, image_tag


def _application(commit_hash):
    return SimpleNamespace(name="My App", project_commit_hash=commit_hash)


def test_commit_hash_produces_a_deterministic_tag():
    assert image_tag(_application("abcdef0123456789abcd")) == "my-app-abcdef012345"


def test_manual_deploys_get_a_unique_tag():
    tags = {image_tag(_application(placeholder)) for placeholder in ("", None, "None", "null")}

    assert len(tags) == 4
    for tag in tags:
        assert re.fullmatch(r"my-app-[0-9a-f]{12}", tag), tag


def test_slug_matches_the_legacy_derivation():
    assert app_slug("My App") == "my-app"
    assert app_slug("-Weird_.Name-") == "weird_.name"
