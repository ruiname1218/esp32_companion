# Deno Deploy デプロイガイド

## ステップ 1: GitHubアカウント準備

1. https://github.com にアクセス
2. アカウントがなければ「Sign up」で作成
3. 既にあればログイン

---

## ステップ 2: リポジトリをGitHubにプッシュ

ターミナルで以下を実行：

```bash
cd /Users/suzukirui/Downloads/companion_1227

# Gitリポジトリ初期化（まだの場合）
git init
git add .
git commit -m "Initial commit"

# GitHubで新しいリポジトリを作成後、以下を実行
git remote add origin https://github.com/あなたのユーザー名/magoo.git
git push -u origin main
```

---

## ステップ 3: Deno Deployにサインアップ

1. https://dash.deno.com にアクセス
2. 「Sign in with GitHub」をクリック
3. GitHubでログイン・認証を許可

---

## ステップ 4: 新しいプロジェクト作成

1. ダッシュボードで「New Project」をクリック
2. 「Deploy from GitHub」を選択
3. 先ほどプッシュしたリポジトリを選択
4. 設定：
   - **Entry point**: `server-deno/main.ts`
   - **Environment Variables**:
     - `OPENAI_API_KEY` = あなたのOpenAIキー
     - `FISH_API_KEY` = あなたのFish Audioキー
5. 「Deploy」をクリック

---

## ステップ 5: デプロイ完了確認

デプロイ成功後、URLが表示されます：
```
https://あなたのプロジェクト名.deno.dev
```

ブラウザでアクセスして確認：
```
https://あなたのプロジェクト名.deno.dev/health
→ {"status":"ok"} と表示されればOK！
```

---

## ステップ 6: ESP32のSSL対応設定

`esp32_stt/config.h` を編集して本番環境用に変更：

```cpp
// ============================================
// Deno Deploy 本番環境設定
// ============================================

// サーバー設定
const char* SERVER_HOST = "あなたのプロジェクト名.deno.dev";
const int SERVER_PORT = 443;  // HTTPSポート

// SSL有効化（Deno Deploy必須！）
#define USE_SSL
```

**ポイント**:
- `USE_SSL` を定義すると、ESP32は `wss://`（SSL付きWebSocket）で接続します
- ローカルテストに戻す場合は `#define USE_SSL` をコメントアウト

---

## ステップ 7: ESP32に書き込み

1. Arduino IDEでプロジェクトを開く
2. ボード設定: ESP32S3 Dev Module
3. 「アップロード」ボタンで書き込み

---

## 完了！🎉

これでESP32がDeno Deploy上のサーバーに接続できます。

### 管理画面

- **ログ確認**: https://dash.deno.com → プロジェクト → Logs
- **環境変数変更**: Settings → Environment Variables
- **デプロイ履歴**: Deployments タブ

### トラブルシューティング

| 問題 | 対策 |
|------|------|
| 接続できない | シリアルモニターで「Using SSL」と表示されているか確認 |
| SSL handshake失敗 | ルーターの時刻設定を確認（ESP32の時刻同期に影響） |
| タイムアウト | ポート443が開いているか確認 |
