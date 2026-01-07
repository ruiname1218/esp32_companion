# Magoo - AI Voice Companion

ESP32-S3を使ったリアルタイム音声AIコンパニオン。OpenAI Realtime APIとFish Audio TTSで自然な会話を実現。

## 🎯 特徴

- **リアルタイム会話**: 電源ONで即座に会話開始、継続的な対話が可能
- **WiFi Provisioning**: スマホから簡単WiFi設定（Captive Portal）
- **高品質音声認識**: OpenAI Realtime API（VAD搭載）で正確な日本語認識
- **感情豊かなTTS**: Fish Audioによる高品質な音声合成
- **低遅延ストリーミング**: 文ごとにリアルタイム再生
- **クラウド管理**: Firebase連携でデバイス個別設定・会話履歴を管理

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

## 📦 セットアップ

### 1. サーバー

```bash
cd server
pip install -r requirements.txt

# 環境変数を設定
export OPENAI_API_KEY="your-openai-api-key"
export FISH_API_KEY="your-fish-audio-api-key"

# サーバー起動
python main.py
```

サーバーのIPアドレスを確認:
```bash
# Mac/Linux
ipconfig getifaddr en0

# Windows
ipconfig
```

### 2. Firebase設定（オプション）

クラウド機能（デバイス管理・会話履歴）を使う場合:

1. [Firebase Console](https://console.firebase.google.com/) でプロジェクト作成
2. Firestore Databaseを有効化
3. プロジェクト設定 → サービスアカウント → 新しい秘密鍵の生成
4. ダウンロードしたJSONを `server/serviceAccountKey.json` として保存

詳細は `server/SETUP_FIREBASE.md` を参照

### 3. ESP32

#### ライブラリのインストール
Arduino IDEで以下をインストール:
- **WebSockets** by Markus Sattler
- **ArduinoJson** by Benoit Blanchon

#### 設定ファイルの作成

`esp32_stt/config.h.example` を `config.h` にコピーして編集:

```bash
cd esp32_stt
cp config.h.example config.h
```

```cpp
// サーバーのIPアドレスを設定（必須）
const char* SERVER_HOST = "192.168.x.x";  // ← サーバーのIPに変更
const int SERVER_PORT = 8000;
```

> ⚠️ WiFi設定はconfig.hに書く必要はありません。Captive Portalで設定します。

#### ボード設定
- ボード: ESP32S3 Dev Module
- PSRAM: OPI PSRAM
- USB CDC On Boot: Enabled

#### 書き込み
ESP32に書き込み

### 4. WiFi設定（Captive Portal）

1. ESP32の電源を入れる
2. スマホのWiFi設定で **「Magoo-Setup」** に接続
3. 自動でブラウザが開き設定画面が表示される
4. 使用するWiFiのSSIDとパスワードを入力
5. 「接続」をタップ
6. ESP32が再起動して接続完了！

> 💡 **WiFi設定をやり直す場合**: ESP32の**BOOTボタンを3秒長押し**しながら電源ON

## 🚀 使い方

1. サーバーを起動: `python main.py`
2. ESP32をリセット
3. 話しかけるだけ！（VADで自動検出）
4. AIが音声で応答

## 🌐 Web管理画面

サーバー起動後、ブラウザで `http://localhost:8000` にアクセス:

- **デバイス一覧**: 接続済みMagooをリスト表示
- **会話履歴**: LINE風UIで過去の会話を確認
- **個別設定**: デバイスごとにAIの性格（プロンプト）や声を変更可能
- **コスト表示**: API利用料の概算を表示

## 📁 プロジェクト構成

```
├── server/
│   ├── main.py              # FastAPIサーバー（Realtime API + TTS）
│   ├── firebase_service.py  # Firebase連携（設定・ログ管理）
│   ├── static/index.html    # Web管理画面
│   ├── settings.json        # ローカル設定（Firebaseなし時のフォールバック）
│   ├── serviceAccountKey.json  # Firebase認証（要取得）
│   └── requirements.txt     # Python依存関係
└── esp32_stt/
    ├── esp32_stt.ino        # メインスケッチ
    ├── wifi_portal.h        # WiFi設定用Captive Portal
    ├── config.h             # サーバー設定（.gitignore対象）
    └── config.h.example     # 設定テンプレート
```

## 🔑 API Keys

- **OpenAI API Key**: https://platform.openai.com/api-keys
- **Fish Audio API Key**: https://fish.audio/app/api-keys

## 🛠️ トラブルシューティング

### WiFiに接続できない
- BOOTボタンを3秒長押しでWiFi設定をやり直し
- 5GHz WiFiは非対応、2.4GHzを使用

### 音声が認識されない
- マイクに近づいて話す
- シリアルモニターで `Free heap` を確認（100KB以上必要）

### サーバーに接続できない
- config.hのSERVER_HOSTを確認
- サーバーとESP32が同じネットワークにあるか確認
- ファイアウォールでポート8000を許可

### Firebaseエラー
- `serviceAccountKey.json` が正しい場所にあるか確認
- Firestore Databaseが有効になっているか確認
- インデックスが必要な場合はエラーメッセージのリンクから作成

## 💰 コスト目安

| サービス | 料金 | 備考 |
|----------|------|------|
| OpenAI Realtime (入力) | $0.06/分 | 音声認識 |
| OpenAI Realtime (出力) | $0.24/分 | AI応答生成 |
| Fish Audio TTS | $2/100万文字 | 音声合成 |
| Firebase Firestore | 無料枠あり | 5万読取/日、2万書込/日 |

## 📝 ライセンス

MIT License


AWS EKS（Kubernetes）とvoicevoxをTTSで使うのあり！