"""One credential-name policy shared by indexing, tools, workers and backups."""
import re
from pathlib import Path

PRIVATE_DIRS = {".ssh", ".aws", ".git", ".gnupg", ".azure", ".kube"}
PRIVATE_NAMES = PRIVATE_DIRS | {
    ".env", ".npmrc", ".netrc", ".bash_history", "credentials", "credential", "credentials.json",
    "access-token", "access_token", "token", "secrets", "secret", "id_rsa", "id_ed25519",
}


def is_private(name: str, directory: bool = False) -> bool:
    lower = name.casefold()
    if lower in PRIVATE_NAMES or lower.startswith(".env"):
        return True
    if directory:
        return False
    return (Path(lower).stem in PRIVATE_NAMES
            or re.search(r"(?:^|[._-])(credentials?|access[_-]token|secrets?)(?:$|[._-])", lower) is not None
            or lower.endswith((".pem", ".key", ".p12", ".pfx")))
