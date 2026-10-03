"""
llm_service.py

Natural-language grid Q&A, used by POST /llm/ask in main.py. Fetches
battery asset data from the DB and injects it into the system prompt,
then streams the model's response back token by token from a vLLM
server, via its OpenAI-compatible API (VLLM_* settings in .env).
"""

import os
from dotenv import load_dotenv
from openai import APIError, OpenAI
from database import SessionLocal
from models import Asset, AssetType

load_dotenv()

# os.environ (not getenv): with no base_url the SDK silently falls back to
# api.openai.com, which would send DB data to OpenAI. Fail fast instead.
client = OpenAI(
    base_url=os.environ["VLLM_BASE_URL"],
    api_key=os.getenv("VLLM_API_KEY", "not-needed"),
    timeout=60,
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
    """
    Open a vLLM stream and return a generator of its tokens for FastAPI.

    The request is sent here, before the generator is returned, so an
    unreachable vLLM raises openai.APIError while the endpoint can still
    return a proper error status instead of an empty 200.
    """
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

    def tokens():
        try:
            for chunk in stream:
                if chunk.choices and chunk.choices[0].delta.content:
                    yield chunk.choices[0].delta.content
        except APIError:
            # Headers (200) are already sent by now, so flag it in the body
            yield "\n\n[Error: the LLM stream was interrupted]"

    return tokens()
