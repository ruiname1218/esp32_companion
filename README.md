# ESP32-S3 Voice Assistant

ESP32-S3を使ったリアルタイム音声アシスタント。音声をテキストに変換し、AIで応答を生成して音声で返答します。

## 🎯 特徴

- **リアルタイム音声入力**: INMP441 MEMSマイクで高品質な音声録音
- **音声認識**: OpenAI Whisper APIによる正確な日本語認識
- **AI応答**: GPT-4o-miniによる自然な会話生成
- **音声合成**: Fish Audio TTSによる高品質な日本語音声出力
- **WebSocketストリーミング**: 低遅延のリアルタイム音声再生

## 🔧 ハードウェア

### 必要な部品
- ESP32-S3 N16R8（8MB PSRAM搭載）
- INMP441 I2S MEMSマイク
- MAX98357A I2S アンプ
- 8Ω 1Wスピーカー

### 配線

| INMP441 | ESP32 | MAX98357A | ESP32 |
|---------|-------|-----------|-------|
| SCK | GPIO4 | BCLK | GPIO15 |
| WS | GPIO5 | LRC | GPIO16 |
| SD | GPIO6 | DIN | GPIO17 |
| VDD | 3.3V | VIN | 3.3V |
| GND | GND | GND | GND |

## 📦 インストール

### サーバー

```bash
cd server
pip install -r requirements.txt

# 環境変数を設定
export OPENAI_API_KEY="your-openai-api-key"
export FISH_API_KEY="your-fish-audio-api-key"

# サーバー起動
python main.py
```

### ESP32

1. Arduino IDEで以下のライブラリをインストール:
   - **WebSockets** by Markus Sattler
   - **ArduinoJson** by Benoit Blanchon

2. `esp32_stt/config.h` を編集:
```cpp
const char* WIFI_SSID = "your-wifi-ssid";
const char* WIFI_PASSWORD = "your-wifi-password";
const char* SERVER_HOST = "192.168.x.x";  // サーバーのIPアドレス
```

3. ボード設定:
   - ボード: ESP32S3 Dev Module
   - PSRAM: OPI PSRAM
   - USB CDC On Boot: Enabled

4. ESP32に書き込み

## 🚀 使い方

1. サーバーを起動
2. ESP32をリセット
3. シリアルモニターで `r` を送信して録音開始
4. 3秒間話す
5. AIが音声で応答！

## 📁 プロジェクト構成

```
├── server/
│   ├── main.py           # FastAPIサーバー
│   └── requirements.txt  # Python依存関係
└── esp32_stt/
    ├── esp32_stt.ino     # メインスケッチ
    └── config.h          # WiFi/サーバー設定
```

## 🔑 API Keys

- **OpenAI API Key**: https://platform.openai.com/api-keys
- **Fish Audio API Key**: https://fish.audio/app/api-keys

## 📝 ライセンス

MIT License
