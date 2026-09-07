"""Fake Kubernetes API surface for dev/mock infrastructures.

Mirrors api/mock/mock_session.py: state is kept per infrastructure so it survives the
deployer reconnecting between deploy and cleanup, and any method the deployer has not
been taught raises NotImplementedError so the mock grows with real usage.
"""
from kubernetes import client as k8s
from kubernetes.client.rest import ApiException

_STATES: dict = {}


class MockK8sState:
    def __init__(self):
        self.objects: dict = {}
        self.available_replicas = 1
        self.warning_events: list = []
        self.container_waiting: tuple = None


def get_mock_apis(infra_id: str) -> "MockK8sApis":
    state = _STATES.setdefault(str(infra_id), MockK8sState())
    return MockK8sApis(state)


def reset():
    _STATES.clear()


def _not_found(kind: str, name: str) -> ApiException:
    return ApiException(status=404, reason=f"{kind} {name} not found")


def _conflict(kind: str, name: str) -> ApiException:
    return ApiException(status=409, reason=f"{kind} {name} already exists")


def _name_of(body) -> str:
    metadata = body["metadata"] if isinstance(body, dict) else body.metadata
    return metadata["name"] if isinstance(metadata, dict) else metadata.name


class _MockApi:
    def __init__(self, state: MockK8sState):
        self._state = state

    def _create(self, kind: str, namespace: str, body):
        key = (kind, namespace, _name_of(body))
        if key in self._state.objects:
            raise _conflict(kind, key[2])
        self._state.objects[key] = body
        return body

    def _patch(self, kind: str, namespace: str, name: str, body):
        key = (kind, namespace, name)
        if key not in self._state.objects:
            raise _not_found(kind, name)
        self._state.objects[key] = body
        return body

    def _read(self, kind: str, namespace: str, name: str):
        key = (kind, namespace, name)
        if key not in self._state.objects:
            raise _not_found(kind, name)
        return self._state.objects[key]

    def _delete(self, kind: str, namespace: str, name: str):
        key = (kind, namespace, name)
        if key not in self._state.objects:
            raise _not_found(kind, name)
        del self._state.objects[key]
        return {}

    def __getattr__(self, name: str):
        raise NotImplementedError(
            f"Mock Kubernetes API does not implement {type(self).__name__}.{name}; "
            "add an explicit stub before routing this path through dev mode."
        )


class MockCoreV1Api(_MockApi):
    def create_namespace(self, body):
        return self._create("namespace", "", body)

    def delete_namespace(self, name, **kwargs):
        for key in [k for k in self._state.objects if k[1] == name]:
            del self._state.objects[key]
        return self._delete("namespace", "", name)

    def create_namespaced_resource_quota(self, namespace, body):
        return self._create("resourcequota", namespace, body)

    def create_namespaced_limit_range(self, namespace, body):
        return self._create("limitrange", namespace, body)

    def create_namespaced_config_map(self, namespace, body):
        return self._create("configmap", namespace, body)

    def patch_namespaced_config_map(self, name, namespace, body):
        return self._patch("configmap", namespace, name, body)

    def delete_namespaced_config_map(self, name, namespace, **kwargs):
        return self._delete("configmap", namespace, name)

    def create_namespaced_service(self, namespace, body):
        return self._create("service", namespace, body)

    def patch_namespaced_service(self, name, namespace, body):
        return self._patch("service", namespace, name, body)

    def delete_namespaced_service(self, name, namespace, **kwargs):
        return self._delete("service", namespace, name)

    def list_namespaced_pod(self, namespace, **kwargs):
        waiting = self._state.container_waiting
        state = (
            k8s.V1ContainerState(waiting=k8s.V1ContainerStateWaiting(reason=waiting[0], message=waiting[1]))
            if waiting
            else k8s.V1ContainerState(running=k8s.V1ContainerStateRunning())
        )
        pods = [
            k8s.V1Pod(
                metadata=k8s.V1ObjectMeta(name=f"{name}-mock-0", namespace=namespace),
                status=k8s.V1PodStatus(
                    container_statuses=[
                        k8s.V1ContainerStatus(
                            name=name, image="mock", image_id="", ready=False,
                            restart_count=0, state=state,
                        )
                    ]
                ),
            )
            for kind, ns, name in self._state.objects
            if kind == "deployment" and ns == namespace
        ]
        return k8s.V1PodList(items=pods)

    def list_namespaced_event(self, namespace, **kwargs):
        return k8s.CoreV1EventList(
            items=[
                k8s.CoreV1Event(
                    metadata=k8s.V1ObjectMeta(name=f"event-{index}", namespace=namespace),
                    involved_object=k8s.V1ObjectReference(namespace=namespace),
                    type="Warning", reason=reason, message=message,
                )
                for index, (reason, message) in enumerate(self._state.warning_events)
            ]
        )


class MockAppsV1Api(_MockApi):
    def create_namespaced_deployment(self, namespace, body):
        return self._create("deployment", namespace, body)

    def patch_namespaced_deployment(self, name, namespace, body):
        return self._patch("deployment", namespace, name, body)

    def read_namespaced_deployment(self, name, namespace):
        self._read("deployment", namespace, name)
        return k8s.V1Deployment(
            metadata=k8s.V1ObjectMeta(name=name, namespace=namespace),
            status=k8s.V1DeploymentStatus(available_replicas=self._state.available_replicas),
        )

    def delete_namespaced_deployment(self, name, namespace, **kwargs):
        return self._delete("deployment", namespace, name)


class MockNetworkingV1Api(_MockApi):
    def create_namespaced_network_policy(self, namespace, body):
        return self._create("networkpolicy", namespace, body)

    def create_namespaced_ingress(self, namespace, body):
        return self._create("ingress", namespace, body)

    def patch_namespaced_ingress(self, name, namespace, body):
        return self._patch("ingress", namespace, name, body)

    def delete_namespaced_ingress(self, name, namespace, **kwargs):
        return self._delete("ingress", namespace, name)


class MockK8sApis:
    def __init__(self, state: MockK8sState):
        self.state = state
        self.core = MockCoreV1Api(state)
        self.apps = MockAppsV1Api(state)
        self.networking = MockNetworkingV1Api(state)
