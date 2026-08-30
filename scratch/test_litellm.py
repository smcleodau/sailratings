import os
import asyncio
from litellm import completion

os.environ["GEMINI_API_KEY"] = os.environ.get("GEMINI_API_KEY", "")

try:
    response = completion(
        model="gemini/gemini-1.5-pro",
        messages=[{"role": "user", "content": "Hello"}],
    )
    print("gemini-1.5-pro SUCCESS:", response)
except Exception as e:
    print("gemini-1.5-pro FAILED:", e)

try:
    response = completion(
        model="gemini/gemini-2.5-pro",
        messages=[{"role": "user", "content": "Hello"}],
    )
    print("gemini-2.5-pro SUCCESS:", response)
except Exception as e:
    print("gemini-2.5-pro FAILED:", e)
