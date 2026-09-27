"""
llm_service.py

Natural-language grid Q&A, used by POST /llm/ask in main.py. Fetches
battery asset data from the DB and injects it into the system prompt,
then streams the model's response back token by token via Ollama.

NOTE: hardcoded to Ollama's "mistral:7b-instruct" model — not yet updated
to use the VLLM_* settings in .env (vLLM on the Spark, per the V1.0
architecture). fetch_battery_context() also references Asset fields
(capacity_mwh, is_active) that don't exist on the current Asset model
(see models.py: max_capacity_mwh, no is_active) — this will raise an
AttributeError whenever a battery asset is fetched.
"""

import ollama
from database import SessionLocal
from models import Asset, AssetType


def fetch_battery_context() -> str:
    """Fetch all battery records from the DB and format as a string for the system prompt."""
    db = SessionLocal()
    try:
        batteries = db.query(Asset).filter(Asset.asset_type == AssetType.BATTERY).all()
        if not batteries:
            return "No battery records found in the database."
        result = []
        for b in batteries:
            result.append(
                f"ID: {b.id}, Name: {b.name}, Capacity: {b.capacity_mwh} MWH, "
                f"Max Charge Rate: {b.max_charge_rate_mw} MW, Active: {b.is_active}"
            )
        return "\n".join(result)
    finally:
        db.close()


def ask_grid_question_stream(question: str):
    """
    Single-pass generator that streams tokens back to FastAPI.

    Asset data is always fetched from the DB and injected into the system
    prompt — no tool-calling round trip, so inference runs once only.
    """
    battery_data = fetch_battery_context()

    messages = [
        {
            "role": "system",
            "content": (
                "You are an Electical grid  expert assistant managing Wind, Solar and BESS (Battery Energy Storage System) assets. "
                "You have access to the following live asset data from the system database:\n\n"
                f"{battery_data}\n\n"
                "Use this data when answering questions about the batteries in the system. "
                "For general grid questions not related to the database, answer from your expert knowledge."
            )
        },
        {
            "role": "user",
            "content": question
        }
    ]

    stream = ollama.chat(
        model="mistral:7b-instruct",
        messages=messages,
        stream=True
    )

    for chunk in stream:
        token = chunk.message.content
        if token:
            yield token