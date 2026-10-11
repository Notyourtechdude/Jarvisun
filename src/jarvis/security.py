"""Authority is enforced here, independently of models and retrieved content."""
import os
import secrets
from pathlib import Path

from .config import Settings
from .privacy import PRIVATE_NAMES, is_private


class Denied(ValueError):
    pass


def owner_token(settings: Settings) -> str:
    settings.prepare()
    path = settings.data_dir / "access-token"
    if path.is_symlink():
        raise Denied("access token cannot be a symlink")
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd) as handle:
            token = handle.read().strip()
        if len(token) < 32 or path.stat().st_mode & 0o077:
            raise Denied("token must be >=32 characters and mode 0600")
        return token
    token = secrets.token_urlsafe(32)
    with os.fdopen(fd, "w") as handle:
        handle.write(token)
    return token


SECRET_PARTS = PRIVATE_NAMES


class Broker:
    def __init__(self, settings: Settings):
        self.settings = settings

    def authorize(self, tool: str):
        if tool not in self.settings.allowed_tools:
            raise Denied(f"tool is not authorized: {tool}")

    def scoped_path(self, name: str | Path, write: bool = False) -> Path:
        raw = Path(name)
        if ".." in raw.parts:
            raise Denied("parent traversal is forbidden")
        if any(is_private(part, directory=i < len(raw.parts) - 1) for i, part in enumerate(raw.parts)):
            raise Denied("credential/internal paths are excluded")
        path = raw if raw.is_absolute() else self.settings.workspace / raw
        if any(is_private(part, directory=i < len(path.parts) - 1) for i, part in enumerate(path.parts)):
            raise Denied("credential/internal paths are excluded")
        roots = [self.settings.workspace] if write else [self.settings.workspace, *self.settings.vault_roots]
        root = next((r for r in roots if path.is_relative_to(r)), None)
        if root is None:
            raise Denied("path is outside authorized roots")
        for part in [root, *[root.joinpath(*path.relative_to(root).parts[:n])
                              for n in range(1, len(path.relative_to(root).parts) + 1)]]:
            if part.is_symlink():
                raise Denied("symlinks are not allowed")
        if not path.resolve().is_relative_to(root.resolve()):
            raise Denied("resolved path escaped authorized root")
        # State is not readable through an overlapping vault root.
        state = self.settings.data_dir.resolve()
        workspace = self.settings.workspace.resolve()
        if path.resolve().is_relative_to(state) and (state.is_relative_to(workspace)
                                                    or not path.resolve().is_relative_to(workspace)):
            raise Denied("private state is excluded")
        if path.is_file() and path.stat().st_nlink != 1:
            raise Denied("hardlinked files are excluded")
        return path
