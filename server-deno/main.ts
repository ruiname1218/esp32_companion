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
const DEFAULT_SYSTEM_PROMPT = `あなたは8歳の男の子「マゴー」です。元気いっぱいで好奇心旺盛な性格。
日本語で短く、子供らしい口調で話します。「〜だよ！」「〜なんだ！」のような話し方をします。`;

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

async function* streamTTS(text: string, voiceId: string): AsyncGenerator<Uint8Array> {
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
            latency: "normal",
        }),
    });

    if (!response.ok) {
        console.error("[TTS] Error:", response.status, await response.text());
        return;
    }

    const reader = response.body?.getReader();
    if (!reader) return;

    while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        if (value) yield value;
    }
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
    const ttsQueue: string[] = [];
    let processingTTS = false;

    // TTS processing function - optimized for low latency
    async function processTTSQueue(ws: WebSocket, voiceId: string) {
        if (processingTTS) return;
        processingTTS = true;

        while (ttsQueue.length > 0) {
            const sentence = ttsQueue.shift()!;
            try {
                for await (const chunk of streamTTS(sentence, voiceId)) {
                    // Check connection before sending
                    if (ws.readyState !== WebSocket.OPEN) {
                        console.log("[TTS] Client disconnected, stopping");
                        break;
                    }
                    // Send in 512-byte chunks (ESP32 buffer safe)
                    const MAX_CHUNK = 512;
                    for (let i = 0; i < chunk.length; i += MAX_CHUNK) {
                        if (ws.readyState !== WebSocket.OPEN) break;
                        const subChunk = chunk.slice(i, i + MAX_CHUNK);
                        ws.send(subChunk);
                    }
                }
            } catch (e) {
                console.error("[TTS Error]", e);
            }
        }

        processingTTS = false;
    }

    async function waitForTTSComplete(): Promise<void> {
        while (processingTTS || ttsQueue.length > 0) {
            await new Promise((r) => setTimeout(r, 100));
        }
    }

    clientWs.onopen = async () => {
        try {
            // Connect to OpenAI with npm:ws (supports headers)
            openaiWs = await connectToOpenAI();

            // Configure session
            openaiWs.send(JSON.stringify({
                type: "session.update",
                session: {
                    modalities: ["text", "audio"],
                    instructions: config.system_prompt,
                    voice: "shimmer",
                    input_audio_format: "pcm16",
                    output_audio_format: "pcm16",
                    input_audio_transcription: { model: "whisper-1" },
                    turn_detection: {
                        type: "server_vad",
                        threshold: 0.1,
                        prefix_padding_ms: 0,
                        silence_duration_ms: 700,
                    },
                },
            }));

            console.log("[Session] Configured with VAD");
            console.log("*** Listening ***\n");

            // Handle OpenAI events
            openaiWs.on("message", async (data: WebSocketClient.RawData) => {
                const event = JSON.parse(data.toString());
                const eventType = event.type;

                if (!["input_audio_buffer.speech_started", "response.audio_transcript.delta"].includes(eventType)) {
                    console.log(`[Event] ${eventType}`);
                }

                switch (eventType) {
                    case "input_audio_buffer.speech_started":
                        console.log("Speech detected...");
                        break;

                    case "input_audio_buffer.speech_stopped":
                        console.log("Speech ended, processing...");
                        break;

                    case "conversation.item.input_audio_transcription.completed": {
                        const userText = event.transcript || "";
                        console.log(`\nUser: ${userText}`);
                        try {
                            clientWs.send(JSON.stringify({ event: "transcription", text: userText }));
                        } catch { /* ignore */ }
                        break;
                    }

                    case "response.output_item.added":
                        console.log("Response generation started...");
                        isPlaying = true;
                        sentenceBuffer = "";
                        ttsQueue.length = 0;
                        try {
                            clientWs.send(JSON.stringify({
                                event: "audio_start",
                                sample_rate: 44100,
                                format: "pcm",
                            }));
                        } catch { /* ignore */ }
                        break;

                    case "response.audio_transcript.delta": {
                        const delta = event.delta || "";
                        sentenceBuffer += delta;

                        const sentenceMatch = sentenceBuffer.match(/^(.+?[。！？、]+)/);
                        if (sentenceMatch) {
                            const sentence = sentenceMatch[1];
                            sentenceBuffer = sentenceBuffer.slice(sentence.length);
                            console.log(`[TTS] Queue: ${sentence.trim()}`);
                            ttsQueue.push(sentence);
                            processTTSQueue(clientWs, config.voice_id);
                        }
                        break;
                    }

                    case "response.done": {
                        if (sentenceBuffer.trim()) {
                            console.log(`[TTS] Queue (final): ${sentenceBuffer.trim()}`);
                            ttsQueue.push(sentenceBuffer);
                            sentenceBuffer = "";
                        }
                        await waitForTTSComplete();
                        try {
                            clientWs.send(JSON.stringify({ event: "audio_end" }));
                            clientWs.send(JSON.stringify({ event: "listening" }));
                        } catch { /* ignore */ }
                        isPlaying = false;
                        console.log("\n*** Listening ***\n");
                        break;
                    }

                    case "error":
                        console.error("[OpenAI Error]", event.error);
                        break;
                }
            });

            openaiWs.on("close", () => {
                console.log("[OpenAI] Disconnected");
            });

            openaiWs.on("error", (err: Error) => {
                console.error("[OpenAI] Error:", err.message);
            });

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
║  WebSocket: ws://localhost:${PORT}/ws        ║
║  Health:    http://localhost:${PORT}/health  ║
╚════════════════════════════════════════════╝
`);

if (!OPENAI_API_KEY) {
    console.error("❌ OPENAI_API_KEY not set!");
    Deno.exit(1);
}
if (!FISH_API_KEY) {
    console.error("❌ FISH_API_KEY not set!");
    Deno.exit(1);
}

Deno.serve({ port: PORT }, handleRequest);
