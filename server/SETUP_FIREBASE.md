# Firebase Setup Guide

ToC向けクラウド運用のため、Firebaseプロジェクトを作成して認証情報を取得してください。

## 手順

### 1. Firebaseプロジェクトの作成
1. [Firebase Console](https://console.firebase.google.com/) にアクセス
2. 「プロジェクトを追加」をクリック
3. プロジェクト名を入力（例: `magoo-cloud`）して作成

### 2. Firestoreの有効化
1. 左メニューから **Build > Firestore Database** を選択
2. 「データベースの作成」をクリック
3. ロケーションを選択（例: `asia-northeast1` (Tokyo)）
4. セキュリティルールはひとまず「テストモードで開始」を選択（後で設定します）

### 3. サービスアカウントキーの取得
サーバーがFirebaseにアクセスするための鍵を取得します。

1. 左上の設定アイコン（歯車） > 「プロジェクトの設定」
2. **「サービスアカウント」** タブを選択
3. **「新しい秘密鍵の生成」** をクリック
4. ダウンロードされたJSONファイルを `serviceAccountKey.json` という名前に変更
5. `server/serviceAccountKey.json` に配置してください

## 必要なファイル配置

```
server/
  ├── main.py
  ├── ...
  └── serviceAccountKey.json  <-- ここに配置
```

配置したら教えてください！実装を進めます。
