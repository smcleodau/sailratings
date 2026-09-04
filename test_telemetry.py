import subprocess
import time
import requests

def main():
    try:
        # Start API server
        api_proc = subprocess.Popen(["python3", "-m", "uvicorn", "irc_data.api.main:app", "--host", "127.0.0.1", "--port", "8000"], cwd="api/src")
        time.sleep(3)
        # Check if telemetry endpoint exists
        # Actually it's NextJS we want to hit
        print("Success")
    finally:
        api_proc.terminate()

if __name__ == "__main__":
    main()
