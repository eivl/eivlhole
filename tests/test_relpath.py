import pytest

from eivlhole.errors import MailboxError
from eivlhole.mailbox.base import validate_relpath


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "/abs.txt",
        "../escape.txt",
        "a//b",
        "a/./b",
        "a\\b",
        "C:/Users/x/startup.bat",
        "C:evil.txt",
        "report.txt:hidden",
        "CON",
        "nul.txt",
        "pkg/LPT1",
        "com9.log",
        "a./b",
        "a /b",
        "bad\x01name",
        "a*b",
        "a?b",
        'a"b',
        "a<b",
        "a>b",
        "a|b",
    ],
)
def test_validate_relpath_rejects(bad):
    with pytest.raises(MailboxError, match="unsafe path"):
        validate_relpath(bad)


@pytest.mark.parametrize(
    "good",
    ["pkg/sub/file.txt", "weird name (1).csv", "a.b.c", "console.txt", "COM10", ".hidden"],
)
def test_validate_relpath_accepts(good):
    validate_relpath(good)
