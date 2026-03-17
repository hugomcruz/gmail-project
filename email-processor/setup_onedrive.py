"""
One-time OneDrive Personal OAuth2 setup via device code flow.

Usage:
    # Look up an OneDrive connection from connections.yaml by its ID:
    python setup_onedrive.py --connection onedrive-personal

    # Or provide the client ID directly:
    python setup_onedrive.py --client-id <azure-app-client-id> [--token-cache onedrive_token_cache.json]

Steps:
  1. Prints a URL + code — open it in any browser and sign in with your
     Microsoft personal account (hotmail.com / outlook.com / live.com).
  2. On success the token cache is saved to the path defined in the
     connection (or onedrive_token_cache.json by default).

Azure App prerequisites:
  - Go to https://portal.azure.com → Azure Active Directory → App registrations
  - New registration → Supported account types: "Personal Microsoft accounts only"
  - Platform:  Mobile and desktop applications
  - Redirect URI: https://login.microsoftonline.com/common/oauth2/nativeclient
  - API permissions: Microsoft Graph → Delegated → Files.ReadWrite
  - Copy the Application (client) ID → use with --client-id or set in connections.yaml
"""

import argparse
import os
import sys
from pathlib import Path

import msal

sys.path.insert(0, str(Path(__file__).parent))
from app.rules.connections import ConnectionRegistry

AUTHORITY = "https://login.microsoftonline.com/consumers"
SCOPES = ["Files.ReadWrite"]


def main() -> None:
    parser = argparse.ArgumentParser(description="OneDrive device code auth setup")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--connection", metavar="ID",
                       help="OneDrive connection ID from connections.yaml")
    group.add_argument("--client-id", metavar="CLIENT_ID",
                       help="Azure app client ID (direct)")
    parser.add_argument("--connections-file", default="connections.yaml",
                        help="Path to connections.yaml (default: connections.yaml)")
    parser.add_argument("--token-cache", default=None,
                        help="Token cache file path (overrides connection config)")
    args = parser.parse_args()

    if args.connection:
        registry = ConnectionRegistry(args.connections_file)
        try:
            conn = registry.get(args.connection)
        except KeyError as exc:
            print(f"✗  {exc}", file=sys.stderr)
            sys.exit(1)
        if conn.get("type") != "onedrive":
            print(f"✗  Connection '{args.connection}' is not of type 'onedrive'", file=sys.stderr)
            sys.exit(1)
        client_id = conn["client_id"]
        cache_file = args.token_cache or conn.get("token_cache", "onedrive_token_cache.json")
    else:
        client_id = args.client_id
        cache_file = args.token_cache or "onedrive_token_cache.json"

    cache = msal.SerializableTokenCache()
    if os.path.exists(cache_file):
        cache.deserialize(Path(cache_file).read_text())

    app = msal.PublicClientApplication(
        client_id=client_id,
        authority=AUTHORITY,
        token_cache=cache,
    )

    # Check if we already have a valid token
    accounts = app.get_accounts()
    if accounts:
        result = app.acquire_token_silent(SCOPES, account=accounts[0])
        if result and "access_token" in result:
            print(f"✓  Already authenticated as: {accounts[0].get('username')}")
            print(f"   Token cache: {cache_file}")
            return

    # Device code flow — works in headless / terminal environments
    flow = app.initiate_device_flow(scopes=SCOPES)
    if "user_code" not in flow:
        print(f"✗  Failed to create device flow: {flow.get('error_description')}", file=sys.stderr)
        sys.exit(1)

    print("\n" + "=" * 60)
    print(flow["message"])
    print("=" * 60 + "\n")

    result = app.acquire_token_by_device_flow(flow)  # blocks until user signs in

    if "access_token" not in result:
        print(
            f"✗  Authentication failed: {result.get('error')} — {result.get('error_description')}",
            file=sys.stderr,
        )
        sys.exit(1)

    Path(cache_file).write_text(cache.serialize())
    print(f"\n✓  Authenticated! Token cache saved to '{cache_file}'")
    print("   You can now start the email-processor service.")


if __name__ == "__main__":
    main()
