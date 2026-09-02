"""The Fargate CPU/memory matrix is an ECS constraint; Kubernetes only needs positive values."""
import pytest

from api.serializers.application import ApplicationCreateSerializer

BASE = {
    "name": "myapp",
    "infrastructure_id": "00000000-0000-0000-0000-000000000001",
    "project_remote_url": "https://github.com/o/r",
    "project_branch": "main",
}


def _serializer(compute_type, **overrides):
    return ApplicationCreateSerializer(data={**BASE, **overrides}, context={"compute_type": compute_type})


def test_ecs_rejects_a_cpu_memory_pair_outside_the_fargate_matrix():
    serializer = _serializer("ecs_fargate", alloted_cpu=0.25, alloted_memory=4.0)

    assert not serializer.is_valid()
    assert "memory must be between" in str(serializer.errors)


def test_ecs_rejects_a_cpu_value_off_the_fargate_ladder():
    serializer = _serializer("ecs_fargate", alloted_cpu=0.75, alloted_memory=2.0)

    assert not serializer.is_valid()
    assert "Invalid CPU value" in str(serializer.errors)


def test_eks_accepts_the_same_pair():
    assert _serializer("eks", alloted_cpu=0.25, alloted_memory=4.0).is_valid()


def test_eks_still_rejects_non_positive_allocations():
    from rest_framework import serializers

    serializer = _serializer("eks")

    with pytest.raises(serializers.ValidationError, match="greater than zero"):
        serializer.validate({"alloted_cpu": 0.25, "alloted_memory": 0.0})


def test_missing_context_defaults_to_the_fargate_matrix():
    serializer = ApplicationCreateSerializer(data={**BASE, "alloted_cpu": 0.25, "alloted_memory": 4.0})

    assert not serializer.is_valid()


def test_eks_accepts_allocations_above_the_fargate_ladder():
    """The Fargate ceiling is 4 vCPU / 30GB. A Kubernetes app must be able to exceed it,
    which the old field-level max_value silently prevented before validate() ever ran."""
    serializer = _serializer("eks", alloted_cpu=8.0, alloted_memory=48.0)
    assert serializer.is_valid(), serializer.errors


def test_ecs_rejects_allocations_above_the_fargate_ladder():
    serializer = _serializer("ecs_fargate", alloted_cpu=8.0, alloted_memory=48.0)
    assert not serializer.is_valid()


def test_eks_rejects_allocations_above_the_kubernetes_ceiling():
    serializer = _serializer("eks", alloted_cpu=999.0, alloted_memory=999.0)
    assert not serializer.is_valid()
