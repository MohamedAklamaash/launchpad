"""Per-deploy image tags: EKS needs a new image reference or the Deployment never rolls."""
import re
from types import SimpleNamespace

from aws.codebuild import CodeBuildClient

from api.common.naming import app_slug, image_tag
from api.mock.mock_session import MockSession


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


def test_start_build_passes_image_tag_and_buildspec_pushes_both_tags():
    codebuild = CodeBuildClient(MockSession(region="us-west-2", account_id="000000000000"))
    buildspec = codebuild._get_buildspec()

    assert 'docker push "$ECR_URL:$IMAGE_TAG"' in buildspec
    # ECS keeps consuming -latest, so it must still be pushed (and ECR must stay mutable).
    assert 'docker push "$ECR_URL:$APP_NAME-latest"' in buildspec

    captured = {}
    codebuild.client.start_build = lambda **kwargs: captured.update(kwargs) or {"build": {"id": "b1"}}
    codebuild.start_build(
        project_name="p", repo_url="https://github.com/o/r", branch="main", commit_hash="c",
        ecr_url="repo", app_name="my-app", image_tag="my-app-abcdef012345",
    )

    env = {e["name"]: e["value"] for e in captured["environmentVariablesOverride"]}
    assert env["IMAGE_TAG"] == "my-app-abcdef012345"


def test_start_build_without_an_image_tag_falls_back_to_latest():
    codebuild = CodeBuildClient(MockSession(region="us-west-2", account_id="000000000000"))
    captured = {}
    codebuild.client.start_build = lambda **kwargs: captured.update(kwargs) or {"build": {"id": "b1"}}
    codebuild.start_build(
        project_name="p", repo_url="https://github.com/o/r", branch="main", commit_hash="c",
        ecr_url="repo", app_name="my-app",
    )

    env = {e["name"]: e["value"] for e in captured["environmentVariablesOverride"]}
    assert env["IMAGE_TAG"] == "my-app-latest"
