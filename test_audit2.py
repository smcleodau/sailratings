import requests

def main():
    headers = {"Authorization": "Bearer sailfast2026"}
    base_url = "http://localhost:4100/v1"

    print("Pausing scraper...")
    r = requests.post(f"{base_url}/admin/scrapers/orc/pause", headers=headers)
    print(r.status_code)
    
    r = requests.get(f"{base_url}/admin/tables/admin_edits?limit=5", headers=headers)
    print(r.json())

if __name__ == '__main__':
    main()
