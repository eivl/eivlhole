import re

import pytest

from eivlhole.mailbox import codes


def test_wordlist_loads_two_lists():
    even, odd = codes.load_wordlist()
    assert len(even) >= 200 and len(odd) >= 200
    assert len(set(even)) == len(even) and len(set(odd)) == len(odd)
    assert all(w == w.lower() and w.isalpha() for w in even + odd)


def test_generate_code_shape():
    code = codes.generate_code(3)
    parts = code.split("-")
    assert len(parts) == 4
    assert 1 <= int(parts[0]) <= 999
    even, odd = codes.load_wordlist()
    assert parts[1] in even and parts[2] in odd and parts[3] in even


def test_generate_code_lengths():
    assert len(codes.generate_code(1).split("-")) == 2
    assert len(codes.generate_code(5).split("-")) == 6
    with pytest.raises(ValueError):
        codes.generate_code(0)


def test_generate_code_is_random():
    assert len({codes.generate_code(3) for _ in range(20)}) > 1


def test_normalize_code_accepts_spaces_and_case():
    assert codes.normalize_code("7 Tunnel Stairway Glucose") == "7-tunnel-stairway-glucose"
    assert codes.normalize_code("  7-tunnel-stairway-glucose\n") == "7-tunnel-stairway-glucose"
    assert codes.normalize_code("7 - tunnel  stairway") == "7-tunnel-stairway"


@pytest.mark.parametrize("bad", ["", "tunnel-stairway", "7", "7-", "abc-7-x"])
def test_normalize_code_rejects(bad):
    with pytest.raises(ValueError):
        codes.normalize_code(bad)


def test_code_re_matches_generated():
    assert re.fullmatch(codes.CODE_RE, codes.generate_code(3))
