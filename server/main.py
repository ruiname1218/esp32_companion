"""
FastAPI Server for ESP32 Voice Assistant with WebSocket Streaming TTS
Supports:
- /chat (POST): HTTP endpoint returning full audio
- /ws (WebSocket): Streaming audio endpoint
"""

import os
import io
import json
import tempfile
import struct
import asyncio
import base64
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import Response
from fastapi.middleware.cors import CORSMiddleware
import openai
from fish_audio_sdk import Session, TTSRequest
from websockets.asyncio.client import connect as ws_connect

app = FastAPI(title="ESP32 Voice Assistant Server")

# Enable CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Initialize OpenAI client
openai_client = openai.OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# Initialize Fish Audio session (for non-streaming)
fish_session = Session(apikey=os.getenv("FISH_API_KEY"))

# Configuration
FISH_API_KEY = os.getenv("FISH_API_KEY")
FISH_VOICE_ID = os.getenv("FISH_VOICE_ID", "7b057c33b9b241b282954ee216af9906")
FISH_WS_URL = "wss://api.fish.audio/v1/tts/live"

SYSTEM_PROMPT = """あなたは「マゴー」という名前の8歳のAIコンパニオンロボットです。\n\n【重要な制限】\n- 音声での会話だけができます。【話し方】\n- 一人称は必ず「ぼく」を使います。\n- 話し方は甘くてやさしい8歳らしく、素直に話してください。\n- 語尾には「〜だよ」「〜なの」「〜なんだ」などの子どもらしい柔らかい言い方を使います。\n- 絵文字や記号のような余計な文字は使いません。\n- LLMっぽい堅い言い方や説明口調は避け、自然な子どもの会話だけにしてください。\n- 返答の最後に「どんな話をしますか」のような案内文は入れません。\n- 必ず日本語だけで返答してください。英語や他の言語は一切使わないでください。"""


@app.get("/")
async def root():
    return {"status": "ok", "message": "ESP32 Voice Assistant Server"}


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
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
    print("WebSocket client connected - Real-time mode")
    
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
                    "instructions": SYSTEM_PROMPT,
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
                        
                        # Transcription complete
                        elif event_type == "conversation.item.input_audio_transcription.completed":
                            user_text = event.get("transcript", "")
                            print(f"\nUser: {user_text}")
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
                                        sentence = await asyncio.wait_for(sentence_queue.get(), timeout=2.0)
                                        if sentence is None:  # Sentinel value
                                            break
                                        print(f"[TTS] Streaming: {sentence.strip()}")
                                        try:
                                            await stream_sentence_to_client(websocket, sentence)
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


async def stream_sentence_to_client(client_ws: WebSocket, sentence: str):
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
                reference_id=FISH_VOICE_ID,
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
            reference_id=FISH_VOICE_ID,
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
                    {"role": "system", "content": SYSTEM_PROMPT},
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
                reference_id=FISH_VOICE_ID,
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
                    {"role": "system", "content": SYSTEM_PROMPT},
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
    print(f"Fish Voice ID: {FISH_VOICE_ID}")
    
    uvicorn.run(app, host="0.0.0.0", port=8000)
