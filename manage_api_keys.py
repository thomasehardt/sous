#!/usr/bin/env python3
"""
CLI for managing Sous public-API keys (create/list/revoke). There is no
in-app way to do this - the app has no authenticated admin session anywhere
- so key management is deliberately a server-side CLI, not an endpoint.

Usage:
    python3 manage_api_keys.py create "my integration"
    python3 manage_api_keys.py list
    python3 manage_api_keys.py revoke 3
"""
import sys

from api_keys import create_api_key, list_api_keys, revoke_api_key


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    command = sys.argv[1]

    if command == 'create':
        if len(sys.argv) < 3:
            print('Usage: manage_api_keys.py create "<label>"')
            sys.exit(1)
        label = sys.argv[2]
        result = create_api_key(label)
        print(f"Created key #{result['id']} (\"{result['label']}\"):")
        print(result['key'])
        print("\nThis key is shown once and not recoverable - store it now.")

    elif command == 'list':
        keys = list_api_keys()
        if not keys:
            print("No API keys yet.")
            return
        for k in keys:
            status = 'REVOKED' if k['revoked'] else 'active'
            last_used = k['last_used_at'] or 'never'
            print(f"#{k['id']:<4} [{status:>7}] {k['label']:<30} created={k['created_at'][:19]} last_used={last_used[:19]}")

    elif command == 'revoke':
        if len(sys.argv) < 3:
            print('Usage: manage_api_keys.py revoke <id>')
            sys.exit(1)
        try:
            key_id = int(sys.argv[2])
        except ValueError:
            print('id must be an integer')
            sys.exit(1)
        if revoke_api_key(key_id):
            print(f"Revoked key #{key_id}.")
        else:
            print(f"No key with id {key_id} found.")
            sys.exit(1)

    else:
        print(__doc__)
        sys.exit(1)


if __name__ == '__main__':
    main()
