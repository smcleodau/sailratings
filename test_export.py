import requests
def main():
    headers = {"Authorization": "Bearer sailfast2026"}
    base_url = "http://localhost:4100/v1"
    r = requests.get(f"{base_url}/admin/tables/admin_edits/export", headers=headers)
    print(r.status_code)
    print(r.text[:500])
if __name__ == '__main__':
    main()
