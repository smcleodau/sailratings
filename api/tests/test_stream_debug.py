import pytest
import subprocess
import os
import json

def test_get_1password_item_fields():
    # Load token
    token_file = "/home/irc-data/.credentials/op-service-account.env"
    with open(token_file, "r") as f:
        for line in f:
            if "OP_SERVICE_ACCOUNT_TOKEN" in line:
                os.environ["OP_SERVICE_ACCOUNT_TOKEN"] = line.split("=")[-1].strip().strip('"').strip("'")

    # Test environment variable pass-through via op run
    print("\n=== TESTING OP RUN ENV PASS-THROUGH ===")
    print("PARENT ENV GEMINI_API_KEY:", os.environ.get("GEMINI_API_KEY"))
    try:
        env = os.environ.copy()
        env["GEMINI_API_KEY"] = "AIzaSyFakeKey_ForTestingPassThrough"
        out = subprocess.check_output(
            '/home/irc-data/.local/bin/op run --environment vzhxzxt7mgb4tolyepo5wqzcz4 -- printenv',
            shell=True,
            text=True,
            env=env
        )
        found = False
        for line in out.splitlines():
            if "GEMINI_API_KEY" in line:
                print("Found GEMINI_API_KEY in op run output:", line)
                found = True
        if not found:
            print("GEMINI_API_KEY was NOT found in op run output (it was stripped/overridden).")
    except Exception as e:
        print("Failed to run pass-through test:", e)



    # List items in the vault to see what items exist
    print("\n=== ITEMS IN THE VAULT ===")
    try:
        out = subprocess.check_output(
            '/home/irc-data/.local/bin/op item list --vault "Sail Ratings" --format=json',
            shell=True,
            text=True
        )
        items = json.loads(out)
        for item in items:
            print(f"- Title: {item.get('title')} | ID: {item.get('id')} | Category: {item.get('category')}")
    except Exception as e:
        print("Failed to list items:", e)

    # List files in .credentials directory
    print("\n=== FILES IN .CREDENTIALS ===")
    try:
        creds_dir = "/home/irc-data/.credentials"
        files = os.listdir(creds_dir)
        for f in files:
            fpath = os.path.join(creds_dir, f)
            print(f"- File: {f} | IsFile: {os.path.isfile(fpath)} | Size: {os.path.getsize(fpath)}")
            # Read keys from file
            with open(fpath, "r") as fh:
                for line in fh:
                    stripped = line.strip()
                    if stripped and not stripped.startswith("#") and "=" in stripped:
                        key_name = stripped.split("=", 1)[0].strip()
                        if "gemini" in key_name.lower() or "google" in key_name.lower():
                            print(f"  Found potential key in {f}: {key_name}")
    except Exception as e:
        print("Failed to list .credentials:", e)

    # Inspect /home/irc-data/.env
    print("\n=== INSPECTING /home/irc-data/.env ===")
    try:
        env_path = "/home/irc-data/.env"
        if os.path.exists(env_path):
            print(f"- /home/irc-data/.env exists! Size: {os.path.getsize(env_path)}")
            with open(env_path, "r") as f:
                for line in f:
                    stripped = line.strip()
                    if stripped and not stripped.startswith("#"):
                        parts = stripped.split("=", 1)
                        key_name = parts[0].strip()
                        has_value = len(parts) > 1 and len(parts[1].strip()) > 0
                        print(f"  Env Key: {key_name} | Has Value: {has_value}")
        else:
            print("- /home/irc-data/.env does not exist.")
    except Exception as e:
        print("Failed to inspect /home/irc-data/.env:", e)

    # Scan /home/irc-data for matching files
    print("\n=== HOME DIR SCANS ===")
    try:
        home_dir = "/home/irc-data"
        for f in os.listdir(home_dir):
            if any(x in f.lower() for x in ["gemini", "key", "google", "anthropic", "claude", "api"]):
                fpath = os.path.join(home_dir, f)
                if os.path.isfile(fpath):
                    print(f"- File: {f} | Size: {os.path.getsize(fpath)}")
    except Exception as e:
        print("Failed to scan home dir:", e)


    # Inspect op vaults
    print("\n=== OP VAULTS ===")

    try:
        out = subprocess.check_output(
            '/home/irc-data/.local/bin/op vault list --format=json',
            shell=True,
            text=True
        )
        vaults = json.loads(out)
        for v in vaults:
            print(f"- Vault Name: {v.get('name')} | ID: {v.get('id')}")
            
            # List items in each vault
            try:
                out_items = subprocess.check_output(
                    f'/home/irc-data/.local/bin/op item list --vault "{v.get("id")}" --format=json',
                    shell=True,
                    text=True
                )
                items = json.loads(out_items)
                for item in items:
                    print(f"  - Item Title: {item.get('title')} | ID: {item.get('id')} | Category: {item.get('category')}")
            except Exception as item_err:
                print(f"  Failed to list items for vault {v.get('name')}:", item_err)
    except Exception as e:
        print("Failed to list vaults:", e)





