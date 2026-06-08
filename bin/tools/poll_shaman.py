import time
import requests

PROJECT = "ceph"
REF = "main"  # or your specific branch
SHA1 = "your-exact-commit-sha-here"
SHAMAN_URL = "https://shaman.ceph.com/api/search/"


def check_build():
    params = {"project": PROJECT, "ref": REF, "sha1": SHA1, "status": "ready"}
    try:
        response = requests.get(SHAMAN_URL, params=params)
        if response.status_code == 200:
            data = response.json()
            # If Shaman returns a populated list, the 'ready' repo exists
            if len(data) > 0:
                print(f"🎉 Build is complete! Repo URL: {data[0]['url']}")
                return True
    except Exception as e:
        print(f"Error querying Shaman: {e}")
    return False


print("Waiting for Shaman build to complete...")
while not check_build():
    time.sleep(60)  # Check every 1 minute
