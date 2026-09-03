import requests

def main():
    headers = {"Authorization": "Bearer sailfast2026"}
    base_url = "http://localhost:4100/v1"

    print("Checking admin_edits...")
    r = requests.get(f"{base_url}/admin/tables/admin_edits?limit=5", headers=headers)
    print(r.status_code)
    print(r.json())

    # Try pausing a scraper
    print("Pausing scraper...")
    r = requests.post(f"{base_url}/admin/scrapers/orc/pause", headers=headers)
    print(r.status_code)

    print("Checking admin_edits...")
    r = requests.get(f"{base_url}/admin/tables/admin_edits?limit=5", headers=headers)
    print(r.status_code)
    for row in r.json().get('rows', []):
        if row['table_name'] == 'scrapers:orc':
            print("Found audit event!")
            print(row)
            break
    else:
        print("Audit event not found!")

if __name__ == '__main__':
    main()
