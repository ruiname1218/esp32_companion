
import firebase_admin
from firebase_admin import credentials, firestore
from datetime import datetime
import time
import os
from pathlib import Path

# Initialize Firebase
CREDENTIALS_FILE = Path(__file__).parent / "serviceAccountKey.json"
_db = None

# ========== Config Cache ==========
# Cache structure: { device_id: { "config": {...}, "timestamp": float } }
_config_cache = {}
CACHE_TTL_SECONDS = 60  # 60 seconds cache validity

def _get_cached_config(device_id: str):
    """Get config from cache if valid, otherwise return None"""
    if device_id in _config_cache:
        cached = _config_cache[device_id]
        if time.time() - cached["timestamp"] < CACHE_TTL_SECONDS:
            return cached["config"]
    return None

def _set_cached_config(device_id: str, config: dict):
    """Store config in cache"""
    _config_cache[device_id] = {
        "config": config,
        "timestamp": time.time()
    }

def invalidate_cache(device_id: str = None):
    """Invalidate cache for a specific device or all devices"""
    global _config_cache
    if device_id:
        _config_cache.pop(device_id, None)
    else:
        _config_cache = {}

# ========== Firebase Initialization ==========

def init_firebase():
    """Initialize Firebase Admin SDK"""
    global _db
    if not CREDENTIALS_FILE.exists():
        print(f"[WARNING] Firebase credentials not found at {CREDENTIALS_FILE}")
        print("Detailed logging will be disabled.")
        return False
    
    try:
        cred = credentials.Certificate(str(CREDENTIALS_FILE))
        firebase_admin.initialize_app(cred)
        _db = firestore.client()
        print("[INFO] Firebase initialized successfully")
        return True
    except Exception as e:
        print(f"[ERROR] Auto-initializing Firebase failed: {e}")
        return False

# ========== Device Config ==========

def get_device_config(device_id: str, use_cache: bool = True):
    """
    Get configuration for a specific device.
    Uses cache by default (60s TTL).
    Returns default config if not found.
    """
    # Check cache first
    if use_cache:
        cached = _get_cached_config(device_id)
        if cached is not None:
            return cached
    
    if not _db:
        return None
    
    try:
        doc_ref = _db.collection('devices').document(device_id)
        doc = doc_ref.get()
        if doc.exists:
            config = doc.to_dict()
            _set_cached_config(device_id, config)
            return config
        else:
            # Create default config for new device
            default_config = {
                "created_at": firestore.SERVER_TIMESTAMP,
                "last_active": firestore.SERVER_TIMESTAMP,
                "voice_id": "7b057c33b9b241b282954ee216af9906",
                "system_prompt": "", # Empty means use server default
            }
            doc_ref.set(default_config)
            _set_cached_config(device_id, default_config)
            return default_config
    except Exception as e:
        print(f"[ERROR] Failed to get device config: {e}")
        return None

def update_device_config(device_id: str, config: dict):
    """Update device configuration and invalidate cache"""
    if not _db: return False
    try:
        _db.collection('devices').document(device_id).update(config)
        # Invalidate cache so next read gets fresh data
        invalidate_cache(device_id)
        return True
    except Exception as e:
        print(f"[ERROR] Failed to update device config: {e}")
        return False

# ========== Device List ==========

def get_all_devices():
    """Get list of all devices ordered by last active"""
    if not _db: return []
    try:
        docs = _db.collection('devices').order_by('last_active', direction=firestore.Query.DESCENDING).get()
        return [{"id": d.id, **d.to_dict()} for d in docs]
    except Exception as e:
        print(f"[ERROR] Failed to get devices: {e}")
        return []

# ========== Conversation Logs ==========

def get_device_logs(device_id: str, limit: int = 50):
    """Get recent conversation logs for a device"""
    if not _db: return []
    try:
        docs = _db.collection('conversations')\
            .where('device_id', '==', device_id)\
            .order_by('timestamp', direction=firestore.Query.DESCENDING)\
            .limit(limit)\
            .get()
        return [{"id": d.id, **d.to_dict()} for d in docs]
    except Exception as e:
        print(f"[ERROR] Failed to get logs: {e}")
        return []

def log_conversation(device_id: str, role: str, content: str, cost: float=0.0):
    """
    Log a conversation turn to Firestore
    """
    if not _db:
        return
        
    try:
        # Update last active timestamp
        _db.collection('devices').document(device_id).update({
            "last_active": firestore.SERVER_TIMESTAMP
        })
        
        # Add log entry
        log_entry = {
            "device_id": device_id,
            "timestamp": firestore.SERVER_TIMESTAMP,
            "role": role,
            "content": content,
            "cost_estimate": cost
        }
        _db.collection('conversations').add(log_entry)
        
    except Exception as e:
        print(f"[ERROR] Failed to log conversation: {e}")

# ========== Cost Estimation ==========

# Cost estimation constants (approximate USD)
COST_PER_CHAR_FISH = 0.000002  # ~$2 per 1M chars
COST_PER_MIN_REALTIME_IN = 0.06 # $0.06/min input
COST_PER_MIN_REALTIME_OUT = 0.24 # $0.24/min output

def estimate_cost_fish(text: str):
    return len(text) * COST_PER_CHAR_FISH
