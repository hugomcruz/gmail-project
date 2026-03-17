"""
Generate a bcrypt password hash suitable for direct database insertion.

Usage:
    python hash_password.py <password>
    python hash_password.py          # prompts securely
"""

import sys

import bcrypt


def main() -> None:
    if len(sys.argv) >= 2:
        plain = sys.argv[1]
    else:
        import getpass
        plain = getpass.getpass("Password: ")

    hashed = bcrypt.hashpw(plain.encode(), bcrypt.gensalt()).decode()
    print(hashed)
    print()
    print("SQL to update the admin user:")
    print(f"  UPDATE users SET hashed_password = '{hashed}' WHERE username = 'admin';")


if __name__ == "__main__":
    main()
