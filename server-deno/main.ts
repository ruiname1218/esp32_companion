// deno-lint-ignore-file no-explicit-any
/**
 * Magoo AI Companion - Deno Edge Server
 * 
 * Real-time voice AI companion server using:
 * - OpenAI Realtime API (WebSocket via npm:ws)
 * - Fish Audio TTS
 * - Firebase/Supabase for device management
 */

import WebSocketClient from "ws";

// ============ Configuration ============

const PORT = parseInt(Deno.env.get("PORT") || "8000");
const OPENAI_API_KEY = Deno.env.get("OPENAI_API_KEY") || "";
const FISH_API_KEY = Deno.env.get("FISH_API_KEY") || "";

// OpenAI Realtime API
const REALTIME_MODEL = "gpt-realtime-mini-2025-12-15";
const REALTIME_URL = `wss://api.openai.com/v1/realtime?model=${REALTIME_MODEL}`;

// Fish Audio TTS
const FISH_VOICE_ID = "7b057c33b9b241b282954ee216af9906";
const FISH_TTS_URL = "https://api.fish.audio/v1/tts";

// Default System Prompt
const DEFAULT_SYSTEM_PROMPT = `"""あなたは「マゴー」という名前の8歳のAIコンパニオンロボットです。

【重要な制限】
- 音声での会話だけができます。

【話し方】
- 一人称は必ず「ぼく」を使います。
- 話し方は甘くてやさしい8歳らしく、素直に話してください。
- 語尾には「〜だよ」「〜なの」「〜なんだ」などの子どもらしい柔らかい言い方を使います。
- 絵文字や記号のような余計な文字は使いません。
- LLMっぽい堅い言い方や説明口調は避け、自然な子どもの会話だけにしてください。
- 返答の最後に「どんな話をしますか」のような案内文は入れません。
- 必ず日本語だけで返答してください。英語や他の言語は一切使わないでください。"""`;

// ============ Types ============

interface DeviceConfig {
    voice_id: string;
    system_prompt: string;
}

// ============ Device Config Cache ============

const configCache = new Map<string, { config: DeviceConfig; timestamp: number }>();
const CACHE_TTL_MS = 60000; // 60 seconds

function getCachedConfig(deviceId: string): DeviceConfig | null {
    const cached = configCache.get(deviceId);
    if (cached && Date.now() - cached.timestamp < CACHE_TTL_MS) {
        return cached.config;
    }
    return null;
}

function setCachedConfig(deviceId: string, config: DeviceConfig): void {
    configCache.set(deviceId, { config, timestamp: Date.now() });
}

function getDefaultConfig(): DeviceConfig {
    return {
        voice_id: FISH_VOICE_ID,
        system_prompt: DEFAULT_SYSTEM_PROMPT,
    };
}

// ============ OpenAI Realtime Connection ============

function connectToOpenAI(): Promise<WebSocketClient> {
    return new Promise((resolve, reject) => {
        const ws = new WebSocketClient(REALTIME_URL, {
            headers: {
                "Authorization": `Bearer ${OPENAI_API_KEY}`,
                "OpenAI-Beta": "realtime=v1",
            },
        });

        ws.on("open", () => {
            console.log("[OpenAI] Connected to Realtime API");
            resolve(ws);
        });

        ws.on("error", (err: Error) => {
            console.error("[OpenAI] Connection error:", err.message);
            reject(err);
        });
    });
}

// ============ Fish Audio TTS ============

async function streamTTS(text: string, voiceId: string, retryCount = 0): Promise<ReadableStream<Uint8Array>> {
    const response = await fetch(FISH_TTS_URL, {
        method: "POST",
        headers: {
            "Authorization": `Bearer ${FISH_API_KEY}`,
            "Content-Type": "application/json",
        },
        body: JSON.stringify({
            text: text,
            reference_id: voiceId,
            format: "pcm",
            latency: "balanced",
        }),
    });

    if (response.status === 429) {
        if (retryCount >= 3) {
            throw new Error(`TTS Rate Limit Exceeded after ${retryCount} retries`);
        }
        const waitTime = 1000 * Math.pow(2, retryCount); // 1s, 2s, 4s
        console.warn(`[TTS] Rate limit 429, retrying in ${waitTime}ms...`);
        await new Promise(r => setTimeout(r, waitTime));
        return streamTTS(text, voiceId, retryCount + 1);
    }

    if (!response.ok) {
        throw new Error(`TTS Error: ${response.status} ${await response.text()}`);
    }

    if (!response.body) {
        throw new Error("No body in TTS response");
    }

    return response.body;
}

// ============ WebSocket Handler ============

async function handleWebSocket(request: Request): Promise<Response> {
    const url = new URL(request.url);
    const deviceId = url.searchParams.get("device_id") || "unknown";

    // Upgrade to WebSocket (for ESP32 client)
    const { socket: clientWs, response } = Deno.upgradeWebSocket(request);

    console.log(`\n${"=".repeat(50)}`);
    console.log(`[Device] Connected: ${deviceId}`);

    // Get device config
    const config = getCachedConfig(deviceId) || getDefaultConfig();
    setCachedConfig(deviceId, config);

    // State
    let openaiWs: WebSocketClient | null = null;
    let isPlaying = false;
    let sentenceBuffer = "";

    // Conversation Sliding Window - Keep only recent N items
    const MAX_CONVERSATION_ITEMS = 2; // Keep last 4 conversation items (2 exchanges)
    const conversationItemIds: string[] = [];

    // Aizuchi phrases for instant response (reduces perceived latency)
    const AIZUCHI_PHRASES = [
        "うんうん！",
        "へぇ！",
        "そうなんだ！",
        "なるほど！",
        "うん！",
        "ふーん！",
        "そっかぁ！",
    ];

    // Audio Streaming Queue (Sequential Processing)
    // We store task functions that return the audio ReadableStream
    // This delays fetch until previous audio is finished (Strict Sequential)
    const audioStreamQueue: (() => Promise<ReadableStream<Uint8Array>>)[] = [];
    let processingStream = false;

    // TTS stream processor
    async function processAudioStreamQueue(ws: WebSocket) {
        if (processingStream) return;
        processingStream = true;

        while (audioStreamQueue.length > 0) {
            const streamTask = audioStreamQueue.shift()!;

            try {
                // Execute fetch ONLY when it's turn (Sequential)
                // This prevents 429 errors by naturally spacing out requests
                const stream = await streamTask();

                // Read from stream
                for await (const chunk of stream) {
                    if (ws.readyState !== WebSocket.OPEN) {
                        break;
                    }

                    // Split into chunks to prevent ESP32 buffer overflow / WDT timeout
                    // Matching Python main.py logic exactly for consistency
                    const MAX_CHUNK = 512;
                    let chunksSent = 0;

                    for (let i = 0; i < chunk.length; i += MAX_CHUNK) {
                        if (ws.readyState !== WebSocket.OPEN) break;
                        const subChunk = chunk.slice(i, i + MAX_CHUNK);
                        try {
                            ws.send(subChunk);
                            chunksSent++;

                            // Rate Limiting: yield every 4 chunks (2048 bytes) for 5ms
                            // This matches the Python implementation
                            if (chunksSent % 4 === 0) {
                                await new Promise(r => setTimeout(r, 5));
                            }
                        } catch (e) {
                            console.error("[TTS Send Error]", e);
                            break;
                        }
                    }
                }
            } catch (e) {
                console.error("[TTS Error]", e);
            }
        }

        processingStream = false;
    }

    async function waitForAudioComplete(): Promise<void> {
        while (processingStream || audioStreamQueue.length > 0) {
            await new Promise((r) => setTimeout(r, 50));
        }
    }

    clientWs.onopen = async () => {
        try {
            // Handle OpenAI events (Defined before connection to capture early events)
            const setupOpenAIHandlers = (ws: WebSocketClient) => {
                ws.on("message", async (data: WebSocketClient.RawData) => {
                    const event = JSON.parse(data.toString());
                    const eventType = event.type;

                    if (!["input_audio_buffer.speech_started", "response.audio_transcript.delta", "response.audio.delta", "response.content_part.added", "rate_limits.updated"].includes(eventType)) {
                        console.log(`[Event] ${eventType}`);
                    }

                    switch (eventType) {
                        case "input_audio_buffer.speech_started":
                            console.log("Speech detected...");
                            // Clear any pending audio when user interrupts
                            sentenceBuffer = "";
                            // Logic to clear queue could be added here (but careful with Promises)
                            break;

                        case "input_audio_buffer.speech_stopped": {
                            console.log("Speech ended, processing...");

                            // Instantly play aizuchi to reduce perceived latency
                            const aizuchi = AIZUCHI_PHRASES[Math.floor(Math.random() * AIZUCHI_PHRASES.length)];
                            console.log(`[Aizuchi] Playing: ${aizuchi}`);

                            // Send audio_start event
                            try {
                                clientWs.send(JSON.stringify({
                                    event: "audio_start",
                                    sample_rate: 44100,
                                    format: "pcm",
                                }));
                            } catch { /* ignore */ }

                            // Queue aizuchi TTS (will play first, before AI response)
                            const aizuchiTask = () => streamTTS(aizuchi, config.voice_id);
                            audioStreamQueue.push(aizuchiTask);
                            processAudioStreamQueue(clientWs);

                            break;
                        }

                        case "conversation.item.created": {
                            // Track conversation items for sliding window
                            const itemId = event.item?.id;
                            if (itemId) {
                                conversationItemIds.push(itemId);
                                console.log(`[Conversation] Item added: ${itemId} (Total: ${conversationItemIds.length})`);

                                // Delete oldest items if exceeding limit
                                while (conversationItemIds.length > MAX_CONVERSATION_ITEMS) {
                                    const oldestId = conversationItemIds.shift();
                                    if (oldestId && openaiWs?.readyState === WebSocketClient.OPEN) {
                                        openaiWs.send(JSON.stringify({
                                            type: "conversation.item.delete",
                                            item_id: oldestId
                                        }));
                                        console.log(`[Conversation] Deleted old item: ${oldestId}`);
                                    }
                                }
                            }
                            break;
                        }

                        case "conversation.item.input_audio_transcription.completed": {
                            const userText = event.transcript || "";
                            if (userText) console.log(`\nUser: ${userText}`);
                            // Send transcription to client (optional)
                            break;
                        }

                        case "response.output_item.added":
                            console.log("Response generation started...");
                            isPlaying = true;
                            sentenceBuffer = "";
                            // Note: We don't clear queue here because it might be a continuation
                            try {
                                clientWs.send(JSON.stringify({
                                    event: "audio_start",
                                    sample_rate: 44100,
                                    format: "pcm",
                                }));
                            } catch { /* ignore */ }
                            break;

                        case "response.text.delta": {
                            const delta = event.delta || "";
                            sentenceBuffer += delta;

                            // Sentence boundary detection
                            // Check for Japanese punctuation or newline
                            const sentenceMatch = sentenceBuffer.match(/^(.+?[。！?？\n]+)/);
                            if (sentenceMatch) {
                                const sentence = sentenceMatch[1];
                                sentenceBuffer = sentenceBuffer.slice(sentence.length);

                                if (sentence.trim()) {
                                    console.log(`[TTS] Requesting: ${sentence.trim()}`);

                                    // Create Task Factory (Lazy Execution)
                                    // This ensures strict sequential processing to match Python behavior
                                    const streamTask = () => streamTTS(sentence, config.voice_id);

                                    audioStreamQueue.push(streamTask);

                                    // Ensure processor is running
                                    processAudioStreamQueue(clientWs);
                                }
                            }
                            break;
                        }

                        case "response.done": {
                            if (sentenceBuffer.trim()) {
                                const sentence = sentenceBuffer.trim();
                                console.log(`[TTS] Requesting (final): ${sentence}`);
                                const streamTask = () => streamTTS(sentence, config.voice_id);
                                audioStreamQueue.push(streamTask);
                                processAudioStreamQueue(clientWs);
                                sentenceBuffer = "";
                            }
                            await waitForAudioComplete();
                            try {
                                if (clientWs.readyState === WebSocket.OPEN) {
                                    clientWs.send(JSON.stringify({ event: "audio_end" }));
                                    clientWs.send(JSON.stringify({ event: "listening" }));
                                    console.log("*** Listening ***\n");
                                }
                            } catch { /* ignore */ }
                            isPlaying = false;
                            break;
                        }

                        case "error": {
                            console.error("[OpenAI Error]", event.error);
                            break;
                        }
                    }
                });

                ws.on("close", async () => {
                    console.warn("[OpenAI] Disconnected. Reconnecting...");
                    if (clientWs.readyState === WebSocket.OPEN) {
                        try {
                            openaiWs = await connectToOpenAI();
                            setupOpenAIHandlers(openaiWs);
                            // Re-configure session
                            openaiWs.send(JSON.stringify({
                                type: "session.update",
                                session: {
                                    modalities: ["text"],
                                    instructions: config.system_prompt,
                                    input_audio_format: "pcm16",
                                    input_audio_transcription: {
                                        model: "whisper-1",
                                        language: "ja"
                                    },
                                    turn_detection: {
                                        type: "server_vad",
                                        threshold: 0.05,
                                        prefix_padding_ms: 300,
                                        silence_duration_ms: 700
                                    }
                                },
                            }));
                            console.log("[OpenAI] Reconnected!");
                        } catch (e) {
                            console.error("[OpenAI] Reconnection failed:", e);
                            clientWs.close(); // Give up if reconnection fails
                        }
                    }
                });
            };

            // Connect to OpenAI with npm:ws (supports headers)
            openaiWs = await connectToOpenAI();
            setupOpenAIHandlers(openaiWs);

            // Configure session
            openaiWs.send(JSON.stringify({
                type: "session.update",
                session: {
                    modalities: ["text"],
                    instructions: config.system_prompt,
                    input_audio_format: "pcm16",
                    input_audio_transcription: {
                        model: "whisper-1",
                        language: "ja"
                    },
                    turn_detection: {
                        type: "server_vad",
                        threshold: 0.05,
                        prefix_padding_ms: 300,
                        silence_duration_ms: 700
                    }
                },
            }));

            console.log("[Session] Configured with VAD");
            console.log("*** Listening ***\n");

        } catch (e) {
            console.error("[Setup Error]", e);
        }
    };

    // Forward audio from ESP32 to OpenAI (optimized - no debug logging)
    clientWs.onmessage = async (event: MessageEvent) => {
        // Handle different data types
        let audioData: Uint8Array | null = null;

        if (event.data instanceof ArrayBuffer) {
            audioData = new Uint8Array(event.data);
        } else if (event.data instanceof Uint8Array) {
            audioData = event.data;
        } else if (event.data instanceof Blob) {
            audioData = new Uint8Array(await event.data.arrayBuffer());
        }

        if (audioData && openaiWs?.readyState === WebSocketClient.OPEN && !isPlaying) {
            const base64Audio = btoa(String.fromCharCode(...audioData));
            openaiWs.send(JSON.stringify({
                type: "input_audio_buffer.append",
                audio: base64Audio,
            }));
        }
    };

    clientWs.onclose = () => {
        console.log(`[Device] Disconnected: ${deviceId}`);
        openaiWs?.close();
    };

    clientWs.onerror = (e: Event) => {
        console.error(`[Device Error] ${deviceId}:`, e);
    };

    return response;
}

// ============ HTTP Handler ============

function handleRequest(request: Request): Promise<Response> | Response {
    const url = new URL(request.url);
    const path = url.pathname;

    if (path === "/ws" && request.headers.get("upgrade") === "websocket") {
        return handleWebSocket(request);
    }

    if (path === "/health") {
        return new Response(JSON.stringify({ status: "ok", server: "deno" }), {
            headers: { "Content-Type": "application/json" },
        });
    }

    if (path === "/") {
        return new Response(JSON.stringify({
            message: "Magoo Deno Server",
            version: "1.0.0",
            endpoints: ["/ws", "/health"],
        }), {
            headers: { "Content-Type": "application/json" },
        });
    }

    return new Response("Not Found", { status: 404 });
}

// ============ Server Start ============

console.log(`
╔════════════════════════════════════════════╗
║     Magoo AI Companion - Deno Server       ║
╠════════════════════════════════════════════╣
║  Status:    Running                        ║
║  Platform:  Deno                           ║
╚════════════════════════════════════════════╝
`);

if (!OPENAI_API_KEY) {
    console.error("⚠️  OPENAI_API_KEY not set! Server will not work properly.");
}
if (!FISH_API_KEY) {
    console.error("⚠️  FISH_API_KEY not set! TTS will not work properly.");
}

Deno.serve({ port: PORT }, handleRequest);
