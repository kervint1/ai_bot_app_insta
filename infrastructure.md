# Instagram AI 自動投稿ボット：プロジェクト設定まとめ

このドキュメントは、Stability AI、OpenAI、Google Geminiを連携させたInstagram自動投稿システムの構成情報をまとめたものです。

---

## 1. Instagram / Meta API 設定
Instagram プロアカウントとプログラムを連携させるための核心的な ID です。

* **Facebook Page ID**: `969546979567674`
* **Instagram Business Account ID**: `17841479701106710`
* **必要な権限 (Permissions)**: 
    * `instagram_basic`
    * `instagram_content_publish`
    * `pages_read_engagement`

---

## 2. Google Cloud (GCP) 設定
画像の保存とプログラムの実行基盤です。

### **Cloud Storage (GCS)**
* **バケット名**: `kervin-insta-images-2026`
* **公開アクセス**: 「公開 (allUsers に対する Storage オブジェクト閲覧者)」に設定済み。
* **役割**: Instagram APIが投稿用画像を読み取るための公開URLを発行します。

### **Cloud Run (サービス)**
* **サービス名**: `ai-bot-app-insta`
* **URL**: `https://ai-bot-app-insta-49614307198.asia-northeast1.run.app`
* **セキュリティ**: 「認証が必要 (IAM)」に設定し、Google内部通信のみを許可。

### **Cloud Scheduler (タイマー)**
* **頻度**: `0 9 * * *` (毎日午前9時 日本時間)
* **ターゲット**: HTTP GET
* **認証方法**: OIDCトークン (Default compute service account を使用)

---

## 3. 使用 AI プロバイダー
それぞれの役割に応じて以下のモデルを使い分けています。

| プロバイダー | 役割 | 使用モデル/エンジン |
| :--- | :--- | :--- |
| **Stability AI** | メインの画像生成 | `stable-diffusion-xl-1024-v1-0` |
| **OpenAI** | プロンプト補助・画像生成 | `gpt-4o-mini`, `dall-e-3` |
| **Google Gemini** | 画像解析・キャプション作成 | `gemini-2.5-flash` |

---

## 4. ローカル環境・管理ファイル
* **`.env`**: APIキー、トークン、IDを管理する秘匿情報ファイル。
* **`google_credentials.json`**: Google Cloud操作用の認証鍵ファイル。
* **`main.py`**: ボットの本体プログラム（Flask Webサーバー）。
* **`Dockerfile`**: クラウド実行用の環境設定ファイル。

---

## 5. 実行用エンドポイント (URL パス)
Scheduler やブラウザから呼び出す際のパスです。

* **Stability AI 実行**: `/stability_post_insta`
* **OpenAI (DALL-E) 実行**: `/openai_post_insta`
* **Google Imagen 実行**: `/imagen_post_insta`

---

## 6. セキュリティと権限 (IAM)
* **Cloud Run 起動元**: Scheduler のアカウントに `roles/run.invoker` を付与。
* **Storage 管理者**: Cloud Run のアカウントに `roles/storage.objectAdmin` を付与。