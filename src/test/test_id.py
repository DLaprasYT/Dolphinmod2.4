"""Test the ID parsing code."""
import pytest

from utils import obj_id, obj_id_optional, special_id, special_id_optional, ID_EMPTY


# Value -> casefolded ID
EXAMPLES = [
    ('iteM_Id48', 'ITEM_ID48'),
    ('_', '_'),
    ('189', '189'),
    ('regular name', 'REGULAR NAME'),
    ('UNCHANGED_NAME', 'UNCHANGED_NAME'),
]
EXAMPLE_IDS = ['mixed', 'underscore', 'numeric', 'spaces', 'unchanged']

BRACKETS = ['_', '<', '(', '[', ']', ')', '>']
BRACKET_IDS = ['blank', 'lang', 'lpar', 'lbrak', 'rbrak', 'rpar', 'rang']


@pytest.mark.parametrize('inp, result', EXAMPLES, ids=EXAMPLE_IDS)
def test_parse_normal(inp: str, result: str) -> None:
    """Test normal IDs."""
    assert obj_id(inp) == result
    # The other functions are a superset, should be the same.
    assert obj_id_optional(inp) == result
    assert special_id(inp) == result
    assert special_id_optional(inp) == result


def test_blank() -> None:
    """Blank IDs are accepted only by _optional() functions."""
    assert ID_EMPTY == ""

    with pytest.raises(ValueError, match='blank'):
        obj_id('')
    with pytest.raises(ValueError, match='blank'):
        special_id('')
    assert obj_id_optional('') == ""
    assert special_id_optional('') == ""


# Make it easy to add more if required later.
@pytest.mark.parametrize('left, right', [
    ('<', '>'),
], ids=['angle'])
@pytest.mark.parametrize('inp, result', EXAMPLES, ids=EXAMPLE_IDS)
def test_special(inp: str, result: str, left: str, right: str) -> None:
    """Test the same IDs, but with the three kinds of brackets surrounding."""
    inp = f'{left}{inp}{right}'
    result = f'{left}{result}{right}'
    with pytest.raises(ValueError, match='may not start/end'):
        obj_id(inp)
    with pytest.raises(ValueError, match='may not start/end'):
        obj_id_optional(inp)

    assert special_id(inp) == result
    assert special_id_optional(inp) == result


def test_identity() -> None:
    """Test that IDs which remain correctly cased preserve identity."""
    # Try and ensure sure the string isn't interned.
    value = bytes(list(b'AN_ITEM')).decode('ascii') + '_45_ID'
    assert obj_id(value) == value  # Check it is in fact unchanged.

    assert obj_id(value) is value
    assert obj_id_optional(value) is value
    assert special_id(value) is value
    assert special_id_optional(value) is value

    blank = ""
    assert obj_id_optional(blank) is blank
    assert special_id_optional(blank) is blank

    special = f'<{value}>'
    assert special_id(special) is special
    assert special_id_optional(special) is special


@pytest.mark.parametrize('left', BRACKETS, ids=BRACKET_IDS)
@pytest.mark.parametrize('right', BRACKETS, ids=BRACKET_IDS)
def test_bad_bracket_combos(left: str, right: str) -> None:
    """Test invalid bracket combinations."""
    if left == right == '_' or (left == '<' and right == '>'):
        return  # These are valid.

    bad = f'{left}some_id{right}'

    with pytest.raises(ValueError, match='may not start/end'):
        obj_id(bad)
    with pytest.raises(ValueError, match='may not start/end'):
        obj_id_optional(bad)
    with pytest.raises(ValueError):
        special_id(bad)
    with pytest.raises(ValueError):
        special_id_optional(bad)
