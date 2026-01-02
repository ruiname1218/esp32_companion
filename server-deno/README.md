# Magoo Deno Server

ESP32 AI Companionのための高性能Denoサーバー。

## 特徴

- **TypeScript**: 型安全な開発
- **エッジ対応**: Deno Deploy / Supabase Edge Functionsにデプロイ可能
- **高並行性**: Node.js同様、GILの制約なし
- **軽量**: 依存関係が少なく起動が速い

## セットアップ

### 1. Denoのインストール

```bash
# Mac/Linux
curl -fsSL https://deno.land/install.sh | sh

# Windows (PowerShell)
irm https://deno.land/install.ps1 | iex
```

### 2. 環境変数の設定

```bash
cp .env.example .env
# .env を編集してAPIキーを設定
```

### 3. 起動

```bash
# 開発モード（ホットリロード）
deno task dev

# 本番モード
deno task start
```

## ESP32の接続

ESP32の `config.h` でサーバーアドレスを設定:

```cpp
const char* SERVER_HOST = "192.168.x.x";  // このサーバーのIP
const int SERVER_PORT = 8000;
```

## デプロイ

### Deno Deploy

```bash
# Deno CLIでデプロイ
deno install -Arf jsr:@deno/deployctl
deployctl deploy --project=your-project main.ts
```

### Docker

```dockerfile
FROM denoland/deno:1.40.0
WORKDIR /app
COPY . .
CMD ["run", "-A", "main.ts"]
```

## Python版との違い

| 項目 | Python版 | Deno版 |
|------|----------|--------|
| 言語 | Python 3.11+ | TypeScript |
| 並行性 | asyncio + threading | ネイティブ非同期 |
| GIL | あり | なし |
| デプロイ | GCE / Cloud Run | Deno Deploy / Edge |
| 起動時間 | 1-2秒 | <100ms |
