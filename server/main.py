"""
FastAPI Server for ESP32 Voice Assistant with WebSocket Streaming TTS
Supports:
- /chat (POST): HTTP endpoint returning full audio
- /ws (WebSocket): Streaming audio endpoint
- /api/settings: Settings API for Voice ID and System Prompt
"""

import os
import io
import json
import tempfile
import struct
import asyncio
import base64
from pathlib import Path
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import Response, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
import openai
from fish_audio_sdk import Session, TTSRequest
from websockets.asyncio.client import connect as ws_connect
import firebase_service

app = FastAPI(title="Magoo - AI Voice Companion Server")

# Initialize Firebase
USE_FIREBASE = firebase_service.init_firebase()

# Settings file path (Fallback)
SETTINGS_FILE = Path(__file__).parent / "settings.json"

# Default settings
DEFAULT_SETTINGS = {
    "voice_id": "7b057c33b9b241b282954ee216af9906",
    "system_prompt": """あなたは「マゴー」という名前の8歳のAIコンパニオンロボットです。

【重要な制限】
- 音声での会話だけができます。

【話し方】
- 一人称は必ず「ぼく」を使います。
- 話し方は甘くてやさしい8歳らしく、素直に話してください。
- 語尾には「〜だよ」「〜なの」「〜なんだ」などの子どもらしい柔らかい言い方を使います。
- 絵文字や記号のような余計な文字は使いません。
- LLMっぽい堅い言い方や説明口調は避け、自然な子どもの会話だけにしてください。
- 返答の最後に「どんな話をしますか」のような案内文は入れません。
- 必ず日本語だけで返答してください。英語や他の言語は一切使わないでください。"""
}

def load_settings():
    """Load settings from file or return defaults (Fallback)"""
    if SETTINGS_FILE.exists():
        try:
            with open(SETTINGS_FILE, 'r', encoding='utf-8') as f:
                saved = json.load(f)
                return {**DEFAULT_SETTINGS, **saved}
        except:
            pass
    return DEFAULT_SETTINGS.copy()

def save_settings(settings):
    """Save settings to file"""
    with open(SETTINGS_FILE, 'w', encoding='utf-8') as f:
        json.dump(settings, f, ensure_ascii=False, indent=2)

# Load settings fallback
current_settings = load_settings()

# Enable CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount static files
static_dir = Path(__file__).parent / "static"
if static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

# Initialize OpenAI client
openai_client = openai.OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# Initialize Fish Audio session (for non-streaming)
fish_session = Session(apikey=os.getenv("FISH_API_KEY"))

# Configuration
FISH_API_KEY = os.getenv("FISH_API_KEY")
FISH_WS_URL = "wss://api.fish.audio/v1/tts/live"

def get_voice_id(device_id: str = None):
    """Get Voice ID from Firebase (if device_id present) or local settings"""
    if USE_FIREBASE and device_id:
        config = firebase_service.get_device_config(device_id)
        if config and "voice_id" in config:
            return config["voice_id"]
            
    return current_settings.get("voice_id", DEFAULT_SETTINGS["voice_id"])

def get_system_prompt(device_id: str = None):
    """Get System Prompt from Firebase (if device_id present) or local settings"""
    if USE_FIREBASE and device_id:
        config = firebase_service.get_device_config(device_id)
        if config and "system_prompt" in config and config["system_prompt"]:
            return config["system_prompt"]
            
    return current_settings.get("system_prompt", DEFAULT_SETTINGS["system_prompt"])


@app.get("/")
async def root():
    """Serve the settings page"""
    index_file = static_dir / "index.html"
    if index_file.exists():
        return FileResponse(index_file)
    return {"status": "ok", "message": "Magoo Server"}

@app.get("/api/settings")
async def get_settings():
    """Get current settings"""
    return {
        "voice_id": current_settings.get("voice_id", ""),
        "system_prompt": current_settings.get("system_prompt", "")
    }

@app.post("/api/settings")
async def update_settings(request: Request):
    """Update global settings (Legacy)"""
    global current_settings
    try:
        data = await request.json()
        if "voice_id" in data:
            current_settings["voice_id"] = data["voice_id"]
        if "system_prompt" in data:
            current_settings["system_prompt"] = data["system_prompt"]
        save_settings(current_settings)
        return {"success": True}
    except Exception as e:
        return {"success": False, "message": str(e)}

# === Device Management APIs ===

@app.get("/api/devices")
async def list_devices():
    """List all devices"""
    if not USE_FIREBASE:
        return {"success": False, "message": "Firebase not configured"}
    return firebase_service.get_all_devices()

@app.get("/api/devices/{device_id}")
async def get_device(device_id: str):
    """Get device details"""
    if not USE_FIREBASE:
        return {"success": False, "message": "Firebase not configured"}
    config = firebase_service.get_device_config(device_id)
    if not config:
        raise HTTPException(status_code=404, detail="Device not found")
    return config

@app.post("/api/devices/{device_id}")
async def update_device(device_id: str, request: Request):
    """Update device settings"""
    if not USE_FIREBASE:
        return {"success": False, "message": "Firebase not configured"}
    try:
        data = await request.json()
        # Only allow updating specific fields
        update_data = {}
        if "voice_id" in data: update_data["voice_id"] = data["voice_id"]
        if "system_prompt" in data: update_data["system_prompt"] = data["system_prompt"]
        
        success = firebase_service.update_device_config(device_id, update_data)
        return {"success": success}
    except Exception as e:
        return {"success": False, "message": str(e)}

@app.get("/api/devices/{device_id}/logs")
async def get_device_logs(device_id: str, limit: int = 50):
    """Get device conversation logs"""
    if not USE_FIREBASE:
        return {"success": False, "message": "Firebase not configured"}
    return firebase_service.get_device_logs(device_id, limit)



@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket, device_id: str = None):
    """
    WebSocket endpoint for real-time voice conversation
    
    Protocol:
    1. ESP32 continuously streams audio chunks
    2. Server relays to OpenAI Realtime API with VAD
    3. When speech ends, Realtime API generates response
    4. Server streams TTS audio back
    """
    await websocket.accept()
    print("\n" + "="*50)
    print(f"WebSocket client connected - Device ID: {device_id or 'Unknown'}")
    
    # Get device-specific config
    voice_id = get_voice_id(device_id)
    system_prompt = get_system_prompt(device_id)
    
    print(f"Using Voice ID: {voice_id}")
    print(f"Using System Prompt: {system_prompt[:50]}...")
    
    import websockets
    
    headers = {
        "Authorization": f"Bearer {os.getenv('OPENAI_API_KEY')}",
        "OpenAI-Beta": "realtime=v1"
    }
    
    try:
        async with websockets.connect(REALTIME_URL, additional_headers=headers) as realtime_ws:
            print("Connected to OpenAI Realtime API")
            
            # Configure session with VAD
            await realtime_ws.send(json.dumps({
                "type": "session.update",
                "session": {
                    "modalities": ["text"],
                    "instructions": get_system_prompt(),
                    "input_audio_format": "pcm16",
                    "input_audio_transcription": {
                        "model": "whisper-1",
                        "language": "ja"
                    },
                    "turn_detection": {
                        "type": "server_vad",
                        "threshold": 0.1,          # Lower = more sensitive to quiet speech
                        "prefix_padding_ms": 0,   # Capture audio before speech detected
                        "silence_duration_ms": 700  # Wait for speech to truly end
                    }
                }
            }))
            
            # Wait for session.updated
            await realtime_ws.recv()
            print("Realtime API session configured with VAD")
            print("*** Listening ***\n")
            
            # State flag for coordinating tasks
            is_playing_tts = False
            
            async def forward_audio_to_realtime():
                """Forward audio from ESP32 to Realtime API"""
                nonlocal is_playing_tts
                try:
                    while True:
                        try:
                            # Use timeout to prevent indefinite blocking
                            msg = await asyncio.wait_for(
                                websocket.receive(),
                                timeout=1.0
                            )
                            
                            if msg["type"] == "websocket.disconnect":
                                print("ESP32 disconnected")
                                break
                            
                            if "bytes" in msg and msg["bytes"] and not is_playing_tts:
                                data = msg["bytes"]
                                # Send audio chunk to Realtime API
                                await realtime_ws.send(json.dumps({
                                    "type": "input_audio_buffer.append",
                                    "audio": base64.b64encode(data).decode()
                                }))
                        except asyncio.TimeoutError:
                            # Normal during TTS playback - ESP32 not sending
                            continue
                except WebSocketDisconnect:
                    print("ESP32 disconnected")
                except Exception as e:
                    if "disconnect" not in str(e).lower():
                        print(f"Audio forward error: {type(e).__name__}")
            
            async def receive_realtime_events():
                """Receive and process events from Realtime API with streaming TTS"""
                nonlocal is_playing_tts
                user_text = ""
                
                try:
                    async for message in realtime_ws:
                        event = json.loads(message)
                        event_type = event.get("type", "")
                        
                        # Debug: log important events
                        if event_type not in ["input_audio_buffer.speech_started", "response.audio_transcript.delta"]:
                            print(f"[EVENT] {event_type}")
                        
                        # Speech started
                        if event_type == "input_audio_buffer.speech_started":
                            print("Speech detected...")
                        
                        # Speech ended - VAD triggered
                        elif event_type == "input_audio_buffer.speech_stopped":
                            print("Speech ended, processing...")
                            
                            # Update system prompt dynamically from Firebase
                            if device_id:
                                try:
                                    # Fetch latest prompt
                                    new_prompt = get_system_prompt(device_id)
                                    if new_prompt:
                                        await realtime_ws.send(json.dumps({
                                            "type": "session.update",
                                            "session": {
                                                "instructions": new_prompt
                                            }
                                        }))
                                        print("[Config] System prompt updated for next turn")
                                except Exception as e:
                                    print(f"Failed to update prompt: {e}")
                        
                        # Transcription complete
                        elif event_type == "conversation.item.input_audio_transcription.completed":
                            user_text = event.get("transcript", "")
                            print(f"\nUser: {user_text}")
                            
                            # Log user Input
                            if device_id:
                                firebase_service.log_conversation(
                                    device_id, 
                                    "user", 
                                    user_text,
                                    cost=firebase_service.COST_PER_MIN_REALTIME_IN * (event.get("item", {}).get("content", [{}])[0].get("duration_ms", 0)/60000.0)
                                )
                                
                            try:
                                await websocket.send_json({
                                    "event": "transcription",
                                    "text": user_text
                                })
                            except:
                                pass
                        
                        # Response started - begin streaming TTS
                        elif event_type == "response.output_item.added":
                            print("Response generation started, beginning streaming TTS...")
                            is_playing_tts = True
                            
                            # Get latest Voice ID dynamically
                            current_voice_id = get_voice_id(device_id)
                            
                            # Notify ESP32 audio is starting
                            try:
                                await websocket.send_json({
                                    "event": "audio_start",
                                    "sample_rate": 44100,
                                    "format": "pcm"
                                })
                            except:
                                pass
                            
                            # Use queue for parallel TTS (don't block LLM event loop)
                            sentence_queue = asyncio.Queue()
                            ai_response = ""
                            sentence_buffer = ""
                            tts_done = False
                            tts_error = False
                            
                            async def tts_worker():
                                """Process sentences from queue and stream TTS"""
                                nonlocal tts_done, tts_error
                                while True:
                                    try:
                                        # Wait for next sentence with timeout
                                        try:
                                            sentence = await asyncio.wait_for(sentence_queue.get(), timeout=2.0)
                                        except asyncio.TimeoutError:
                                            if tts_done:
                                                break
                                            continue
                                            
                                        if sentence is None:
                                            break
                                        
                                        # Log AI cost (TTS)
                                        tts_cost = firebase_service.estimate_cost_fish(sentence)
                                        if device_id:
                                            firebase_service.log_conversation(device_id, "assistant", sentence, cost=tts_cost)
                                            
                                        # ... streaming logic ...
                                        print(f"[TTS] Streaming: {sentence.strip()}")
                                        try:
                                            await stream_sentence_to_client(websocket, sentence, voice_id=current_voice_id)
                                        except Exception as e:
                                            error_name = type(e).__name__
                                            if "Closed" in error_name or "Disconnect" in error_name:
                                                tts_error = True
                                                break
                                            print(f"Sentence TTS error: {error_name}")
                                            # Continue with next sentence
                                    except asyncio.TimeoutError:
                                        if tts_done:
                                            break
                                        continue
                                    except Exception as e:
                                        print(f"TTS worker error: {type(e).__name__}")
                                        tts_error = True
                                        break
                            
                            # Start TTS worker in background
                            tts_task = asyncio.create_task(tts_worker())
                            
                            try:
                                # Continue receiving events until response.done
                                async for inner_message in realtime_ws:
                                    inner_event = json.loads(inner_message)
                                    inner_type = inner_event.get("type", "")
                                    
                                    if inner_type == "response.text.delta":
                                        delta = inner_event.get("delta", "")
                                        ai_response += delta
                                        sentence_buffer += delta
                                        
                                        # Check for sentence end (Japanese + common punctuation)
                                        for delim in ["。", "！", "？", "!", "?", "\n"]:
                                            if delim in sentence_buffer:
                                                parts = sentence_buffer.split(delim, 1)
                                                sentence = parts[0] + delim
                                                sentence_buffer = parts[1] if len(parts) > 1 else ""
                                                
                                                # Queue sentence for TTS (non-blocking)
                                                if sentence.strip():
                                                    await sentence_queue.put(sentence)
                                                break
                                    
                                    elif inner_type == "response.done":
                                        # Queue remaining text
                                        if sentence_buffer.strip():
                                            await sentence_queue.put(sentence_buffer)
                                        
                                        # Signal TTS worker to finish
                                        tts_done = True
                                        await sentence_queue.put(None)  # Sentinel
                                        
                                        # Wait for TTS to complete
                                        await tts_task
                                        
                                        print(f"\nAI: {ai_response}")
                                        
                                        # Always try to clear input buffer for next turn
                                        try:
                                            await realtime_ws.send(json.dumps({
                                                "type": "input_audio_buffer.clear"
                                            }))
                                        except:
                                            pass
                                        
                                        # Always send listening event to ESP32 (even on TTS error)
                                        # Otherwise ESP32 stays stuck in STATE_PLAYING
                                        try:
                                            await websocket.send_json({"event": "audio_end"})
                                            if not tts_error:
                                                await websocket.send_json({
                                                    "event": "response",
                                                    "text": ai_response
                                                })
                                            await websocket.send_json({"event": "listening"})
                                            print("\n*** Listening ***\n")
                                        except (RuntimeError, Exception) as e:
                                            error_name = type(e).__name__
                                            if "Closed" in error_name or "Disconnect" in error_name or error_name == "RuntimeError":
                                                print("Client disconnected during post-TTS")
                                            else:
                                                print(f"Post-TTS error: {error_name}")
                                        
                                        if tts_error:
                                            print("(TTS had some errors but recovered)")
                                        
                                        is_playing_tts = False
                                        break
                                    
                                    elif inner_type == "error":
                                        print(f"Realtime API error: {inner_event}")
                                        tts_done = True
                                        await sentence_queue.put(None)
                                        await tts_task
                                        # Still notify ESP32 to return to listening
                                        try:
                                            await websocket.send_json({"event": "audio_end"})
                                            await websocket.send_json({"event": "listening"})
                                        except:
                                            pass
                                        is_playing_tts = False
                                        break
                            except Exception as e:
                                tts_done = True
                                await sentence_queue.put(None)
                                try:
                                    await tts_task
                                except:
                                    pass
                                raise
                        # Error
                        elif event_type == "error":
                            print(f"Realtime API error: {event}")
                            
                except Exception as e:
                    error_name = type(e).__name__
                    if "Closed" in error_name or "Disconnect" in error_name:
                        print("Realtime API connection closed")
                    else:
                        print(f"Realtime receive error: {error_name}")
            
            # Run both tasks concurrently
            await asyncio.gather(
                forward_audio_to_realtime(),
                receive_realtime_events(),
                return_exceptions=True
            )
            
    except WebSocketDisconnect:
        print("WebSocket client disconnected")
    except Exception as e:
        error_name = type(e).__name__
        if "Closed" in error_name or "Disconnect" in error_name:
            print("Connection closed")
        else:
            print(f"WebSocket error: {error_name}: {e}")


# OpenAI Realtime API configuration
REALTIME_MODEL = "gpt-realtime-mini-2025-12-15"
REALTIME_URL = f"wss://api.openai.com/v1/realtime?model={REALTIME_MODEL}"


async def stream_sentence_to_client(client_ws: WebSocket, sentence: str, voice_id: str = None):
    """Stream a single sentence TTS to ESP32 (no audio_start/end - caller handles that)"""
    import queue
    import threading
    
    audio_queue = queue.Queue()
    generation_done = threading.Event()
    
    # Generate TTS in background thread
    def generate_tts():
        try:
            tts_request = TTSRequest(
                text=sentence,
                reference_id=voice_id or get_voice_id(),
                format="pcm",
                latency="balanced"
            )
            for chunk in fish_session.tts(tts_request):
                audio_queue.put(chunk)
        except Exception as e:
            print(f"TTS generation error: {type(e).__name__}")
        finally:
            generation_done.set()
    
    gen_thread = threading.Thread(target=generate_tts)
    gen_thread.start()
    
    # Stream audio chunks to client with rate limiting
    MAX_CHUNK_SIZE = 512
    chunks_sent = 0
    
    while True:
        try:
            chunk = audio_queue.get(timeout=0.1)
            # Split into smaller chunks for WebSocket
            for i in range(0, len(chunk), MAX_CHUNK_SIZE):
                sub_chunk = chunk[i:i+MAX_CHUNK_SIZE]
                await client_ws.send_bytes(sub_chunk)
                chunks_sent += 1
                
                # Rate limiting: yield every 4 chunks to prevent buffer overflow
                if chunks_sent % 4 == 0:
                    await asyncio.sleep(0.005)
        except queue.Empty:
            if generation_done.is_set() and audio_queue.empty():
                break
            await asyncio.sleep(0.01)
    
    gen_thread.join()


async def stream_tts_to_client(client_ws: WebSocket, text: str):
    """Stream TTS audio from Fish Audio SDK to ESP32 client"""
    
    import asyncio
    import queue
    import threading
    
    try:
        audio_queue = queue.Queue()
        generation_done = threading.Event()
        
        # Notify client audio is starting
        await client_ws.send_json({
            "event": "audio_start",
            "sample_rate": 44100,
            "format": "pcm"
        })
        
        # Create TTS request with "normal" latency for faster generation
        tts_request = TTSRequest(
            text=text,
            reference_id=get_voice_id(),
            format="pcm",
            latency="normal"  # Changed from "balanced" for faster response
        )
        
        def generate_audio():
            """Generate audio in separate thread"""
            try:
                for chunk in fish_session.tts(tts_request):
                    audio_queue.put(chunk)
            finally:
                generation_done.set()
        
        # Start generation thread
        gen_thread = threading.Thread(target=generate_audio)
        gen_thread.start()
        
        print("Starting TTS stream...")
        
        chunk_count = 0
        total_bytes = 0
        
        # Maximum chunk size to send (ESP32 WebSocket has limit)
        MAX_CHUNK_SIZE = 512
        
        # Send chunks as they become available
        while True:
            try:
                # Try to get chunk with short timeout
                chunk = audio_queue.get(timeout=0.01)
                total_bytes += len(chunk)
                
                # Split large chunks into smaller pieces
                for i in range(0, len(chunk), MAX_CHUNK_SIZE):
                    sub_chunk = chunk[i:i+MAX_CHUNK_SIZE]
                    chunk_count += 1
                    
                    if chunk_count <= 3:
                        print(f"Sending chunk {chunk_count}: {len(sub_chunk)} bytes")
                    
                    await client_ws.send_bytes(sub_chunk)
                
            except queue.Empty:
                # No chunk ready - check if generation is done
                if generation_done.is_set() and audio_queue.empty():
                    break
                # Yield to other tasks
                await asyncio.sleep(0.001)
        
        gen_thread.join()
        
        print(f"TTS finished: {chunk_count} chunks, {total_bytes} bytes")
        
        # Notify client audio is complete
        await client_ws.send_json({"event": "audio_end"})
        
    except WebSocketDisconnect:
        print("Client disconnected during TTS stream")
    except Exception as e:
        # Silently handle disconnection errors
        error_name = type(e).__name__
        if "Disconnect" in error_name or "Closed" in error_name:
            print("Client disconnected during TTS stream")
        else:
            print(f"TTS error: {error_name}")


# Keep the old /chat endpoint for backward compatibility
@app.post("/chat")
async def chat_with_audio(request: Request):
    """HTTP endpoint - returns full audio (non-streaming)"""
    try:
        audio_data = await request.body()
        
        if len(audio_data) < 44:
            raise HTTPException(status_code=400, detail="Invalid audio data")
        
        print(f"\n{'='*50}")
        print(f"[HTTP] Received audio: {len(audio_data)} bytes")
        
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            f.write(audio_data)
            temp_path = f.name
        
        try:
            # Transcribe
            print("Step 1: Transcribing...")
            with open(temp_path, "rb") as audio_file:
                transcript = openai_client.audio.transcriptions.create(
                    model="whisper-1",
                    file=audio_file,
                    language="ja"
                )
            
            user_text = transcript.text
            print(f"User said: {user_text}")
            
            # Generate response
            print("Step 2: Generating response...")
            chat_response = openai_client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": get_system_prompt()},
                    {"role": "user", "content": user_text}
                ],
                max_tokens=200,
                temperature=0.7
            )
            
            ai_response = chat_response.choices[0].message.content
            print(f"AI response: {ai_response}")
            
            # Generate TTS
            print("Step 3: Generating TTS...")
            tts_request = TTSRequest(
                text=ai_response,
                reference_id=get_voice_id(),
                format="wav",
                latency="balanced"
            )
            
            wav_data = b''.join(fish_session.tts(tts_request))
            print(f"Generated {len(wav_data)} bytes of audio")
            print(f"{'='*50}\n")
            
            return Response(content=wav_data, media_type="audio/wav")
            
        finally:
            os.unlink(temp_path)
            
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


# Keep /transcribe for backward compatibility
@app.post("/transcribe")
async def transcribe_only(request: Request):
    """Legacy endpoint - returns JSON only"""
    try:
        audio_data = await request.body()
        
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            f.write(audio_data)
            temp_path = f.name
        
        try:
            with open(temp_path, "rb") as audio_file:
                transcript = openai_client.audio.transcriptions.create(
                    model="whisper-1",
                    file=audio_file,
                    language="ja"
                )
            
            user_text = transcript.text
            
            chat_response = openai_client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": get_system_prompt()},
                    {"role": "user", "content": user_text}
                ],
                max_tokens=200,
                temperature=0.7
            )
            
            ai_response = chat_response.choices[0].message.content
            
            return {"text": user_text, "response": ai_response}
            
        finally:
            os.unlink(temp_path)
            
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":
    import uvicorn
    
    if not os.getenv("OPENAI_API_KEY"):
        print("WARNING: OPENAI_API_KEY not set!")
    if not os.getenv("FISH_API_KEY"):
        print("WARNING: FISH_API_KEY not set!")
    
    print("Starting ESP32 Voice Assistant Server...")
    print("Endpoints:")
    print("  WebSocket /ws    - Streaming audio")
    print("  POST /chat       - Full audio response")
    print("  POST /transcribe - JSON response")
    print(f"Fish Voice ID: {get_voice_id()}")
    
    uvicorn.run(app, host="0.0.0.0", port=8000)
