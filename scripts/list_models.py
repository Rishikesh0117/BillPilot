import os
import requests
from dotenv import load_dotenv

load_dotenv()
base = os.environ["LLM_BASE_URL"].rstrip("/")
r = requests.get(f"{base}/models", headers={"Authorization": f"Bearer {os.environ['LLM_API_KEY']}"})
r.raise_for_status()
for m in sorted(x["id"] for x in r.json()["data"]):
    print(m)