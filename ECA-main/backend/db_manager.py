import os
from dotenv import load_dotenv
from supabase import create_client, Client

load_dotenv()

# Securely grab keys from main backend .env
URL = os.getenv("SUPABASE_URL")
KEY = os.getenv("SUPABASE_KEY")

if URL and KEY:
    supabase: Client = create_client(URL, KEY)
else:
    supabase = None

def save_version(part_id, parameter, old_val, new_val, session_id):
    """Logs a specific engineering change record into Supabase."""
    if not supabase:
        return None
    try:
        data = {
            "part_id": str(part_id),
            "parameter_name": str(parameter),
            "old_value": float(old_val) if old_val is not None else 0,
            "new_value": float(new_val) if new_val is not None else 0,
            "session_id": str(session_id)
        }
        res = supabase.table("model_versions").insert(data).execute()
        return res.data
    except Exception as e:
        print(f"Supabase Log Error: {e}")
        return None

def get_history():
    """Retrieves all historical engineering changes."""
    if not supabase:
        return []
    try:
        res = supabase.table("model_versions").select("*").order("created_at", desc=True).execute()
        return res.data
    except Exception as e:
        print(f"Supabase Query Error: {e}")
        return []
