"""infrastructure-service, application-service, and payment-service each compute an
owner's personal-org id with no cross-service call, by calling this one
function independently. Pinning its output against fixed vectors locks the value in --
a future change to ORG_NAMESPACE or the hashing scheme that silently changed the result
would desync three already-deployed databases."""
import uuid

from shared.utils.org_id import ORG_NAMESPACE, personal_org_id

_VECTOR_1 = uuid.UUID("11111111-1111-1111-1111-111111111111")
_VECTOR_1_EXPECTED = uuid.UUID("622fe5e9-2e23-53f5-a533-9fb6a4a8bd14")

_VECTOR_2 = uuid.UUID("019f2c2b-9b19-71a9-8f3e-cd81d13effb9")
_VECTOR_2_EXPECTED = uuid.UUID("9762168d-fbdc-5ee1-aa27-39ad30b6223f")


def test_personal_org_id_matches_known_vector_1():
    assert personal_org_id(_VECTOR_1) == _VECTOR_1_EXPECTED


def test_personal_org_id_matches_known_vector_2():
    assert personal_org_id(_VECTOR_2) == _VECTOR_2_EXPECTED


def test_personal_org_id_is_deterministic():
    assert personal_org_id(_VECTOR_1) == personal_org_id(_VECTOR_1)


def test_personal_org_id_accepts_a_string_form():
    assert personal_org_id(str(_VECTOR_1)) == _VECTOR_1_EXPECTED


def test_personal_org_id_canonicalizes_before_hashing():
    """Same id, different casing/representation, must hash to the same result --
    rows arriving via different mirrors/consumers can't be relied on to normalize their
    own string form before this is called."""
    assert personal_org_id(str(_VECTOR_1).upper()) == _VECTOR_1_EXPECTED


def test_different_owners_get_different_org_ids():
    assert personal_org_id(_VECTOR_1) != personal_org_id(_VECTOR_2)


def test_org_namespace_is_fixed_not_derived():
    assert ORG_NAMESPACE == uuid.UUID("4c900569-d11e-46e2-b34c-cd677a57ca73")
