import os
import requests

def test_update_paths():
    print("\n=== GCP ENVIRONMENT DETECTOR ===")
    
    # 1. Check for local environment variables
    for k, v in sorted(os.environ.items()):
        if "google" in k.lower() or "gcp" in k.lower() or "gcurrent" in k.lower():
            print(f"  Env: {k} = {v}")
            
    # 2. Check GCE Metadata Server
    try:
        url = "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/scopes"
        r = requests.get(url, headers={"Metadata-Flavor": "Google"}, timeout=2)
        print(f"  GCE Metadata Server default scopes: {r.text.strip()}")
        
        # Get active service account email
        email_url = "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/email"
        r_email = requests.get(email_url, headers={"Metadata-Flavor": "Google"}, timeout=2)
        print(f"  GCE Service Account Email: {r_email.text.strip()}")
    except Exception as e:
        print(f"  Could not access GCE Metadata Server: {e}")
        
    print("=================================\n")



















