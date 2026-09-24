import base64
import requests
import os
from dotenv import load_dotenv

load_dotenv()

GENERATION_CLIENT_ID     = os.getenv("RTE_GENERATION_CLIENT_ID")
GENERATION_CLIENT_SECRET = os.getenv("RTE_GENERATION_CLIENT_SECRET")
BALANCING_CLIENT_ID      = os.getenv("RTE_BALANCING_CLIENT_ID")
BALANCING_CLIENT_SECRET  = os.getenv("RTE_BALANCING_CLIENT_SECRET")

def get_token(client_id: str, client_secret: str) -> str:
    credentials = base64.b64encode(
        f"{client_id}:{client_secret}".encode()
    ).decode()
    response = requests.post(
        "https://digital.iservices.rte-france.com/token/oauth/",
        headers={"Authorization": f"Basic {credentials}"}
    )
    response.raise_for_status()
    return response.json()["access_token"]


def fetch_generation_mix(token: str) -> dict:
    response = requests.get(
        "https://digital.iservices.rte-france.com/open_api/actual_generation/v1/generation_mix_15min_time_scale",
        headers={"Authorization": f"Bearer {token}"}
    )
    response.raise_for_status()
    data = response.json()

    # Extract the most recent 15-min interval
    values = data.get("generation_mix_15min_time_scale", [])
    if not values:
        raise ValueError("No generation mix data returned")

    latest = values[-1]  # most recent entry
    records = latest.get("values", [])

    total_mw = 0.0
    renewable_mw = 0.0
    renewable_types = {"WIND", "SOLAR", "HYDRO", "BIOENERGY"}

    for record in records:
        production_type = record.get("production_type", "")
        value = record.get("value") or 0.0
        total_mw += value
        if production_type in renewable_types:
            renewable_mw += value

    renewable_pct = round((renewable_mw / total_mw) * 100, 2) if total_mw > 0 else 0.0

    return {
        "total_generation_mw": round(total_mw, 1),
        "renewable_mw": round(renewable_mw, 1),
        "renewable_pct": renewable_pct,
        "timestamp": latest.get("start_date"),
    }


if __name__ == "__main__":
    token = get_token(GENERATION_CLIENT_ID, GENERATION_CLIENT_SECRET)
    print(f"✅ Token obtained: {token[:20]}...")

    gen = fetch_generation_mix(token)
    print(f"✅ Generation mix: {gen}")