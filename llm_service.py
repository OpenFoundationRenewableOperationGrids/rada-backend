import os
from dotenv import load_dotenv
from openai import OpenAI
from database import SessionLocal
from models import Asset, AssetType

load_dotenv()

client = OpenAI(
    base_url=os.getenv("VLLM_BASE_URL"),
    api_key=os.getenv("VLLM_API_KEY", "not-needed"),
)
MODEL = os.getenv("VLLM_MODEL", "Qwen/Qwen2.5-7B-Instruct")


def fetch_battery_context() -> str:
    """Fetch all battery records from the DB and format as a string for the system prompt."""
    db = SessionLocal()
    try:
        batteries = db.query(Asset).filter(Asset.asset_type == AssetType.BATTERY).all()
        if not batteries:
            return "No battery records found in the database."
        return "\n".join(
            f"ID: {b.id}, Name: {b.name}, Capacity: {b.max_capacity_mwh} MWh, "
            f"Max Charge Rate: {b.max_charge_rate_mw} MW"
            for b in batteries
        )
    finally:
        db.close()


def ask_grid_question_stream(question: str):
    """Stream tokens from vLLM on the Spark back to FastAPI."""
    battery_data = fetch_battery_context()

    messages = [
        {
            "role": "system",
            "content": (
                "You are an electrical grid expert assistant managing Wind, Solar and BESS "
                "(Battery Energy Storage System) assets. You have access to the following "
                f"live asset data from the system database:\n\n{battery_data}\n\n"
                "Use this data when answering questions about the batteries in the system. "
                "For general grid questions not related to the database, answer from your expert knowledge."
            ),
        },
        {"role": "user", "content": question},
    ]

    stream = client.chat.completions.create(
        model=MODEL,
        messages=messages,
        stream=True,
        max_tokens=1024,
    )

    for chunk in stream:
        if chunk.choices and chunk.choices[0].delta.content:
            yield chunk.choices[0].delta.content