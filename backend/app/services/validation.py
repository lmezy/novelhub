import re


USERNAME_RE = re.compile(r"^[A-Za-z0-9]+$")


def username_error(username: str) -> str | None:
    if not USERNAME_RE.fullmatch(username or ""):
        return "Username can only contain letters and digits"
    return None


def password_error(password: str) -> str | None:
    categories = 0
    if re.search(r"[A-Za-z]", password or ""):
        categories += 1
    if re.search(r"\d", password or ""):
        categories += 1
    if "_" in (password or ""):
        categories += 1
    if re.search(r"[^A-Za-z0-9_]", password or ""):
        categories += 1
    if categories < 2:
        return (
            "Password must contain at least two of letters, digits, "
            "underscore, and special characters"
        )
    return None
