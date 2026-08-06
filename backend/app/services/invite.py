import secrets


INVITE_ALPHABET = (
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    "abcdefghijklmnopqrstuvwxyz"
    "0123456789"
)


def generate_invite_code(length: int = 12) -> str:
    return "".join(secrets.choice(INVITE_ALPHABET) for _ in range(length))
