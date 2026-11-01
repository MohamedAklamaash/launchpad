import pytest
from shared.flags import flag_default_on


@pytest.mark.parametrize("value", [None, "", "  ", "true", "True", "TRUE", "1", "yes", "garbage"])
def test_flag_is_on_unless_explicitly_off(value):
    assert flag_default_on(value) is True


@pytest.mark.parametrize("value", ["false", "False", "FALSE", " false ", "0", "no", "Off"])
def test_flag_is_off_only_for_explicit_off_values(value):
    assert flag_default_on(value) is False
