"""Exception hierarchy. Every CLI failure maps to one of these."""

from __future__ import annotations


class EivlholeError(Exception):
    exit_code = 1


class ConfigError(EivlholeError):
    def __init__(self, problems: list[str] | str):
        self.problems = [problems] if isinstance(problems, str) else list(problems)
        super().__init__("\n".join(self.problems))


class ConnectError(EivlholeError):
    """Network-level failure reaching the host."""


class AuthError(EivlholeError):
    """The server rejected our key or password."""


class HostKeyError(EivlholeError):
    """Host key unknown or changed, and we may not prompt."""


class MailboxError(EivlholeError):
    """Drop host layout or protocol problem."""


class CodeNotFound(MailboxError):
    """Code does not exist, was already claimed, or expired."""


class TransferError(EivlholeError):
    """A file could not be sent or received."""
