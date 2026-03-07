"""
Historical Reel Bot - 歴史的人物リール動画自動生成モジュール
"""

import os
import json
import time
import subprocess
import requests
import replicate
from io import BytesIO

HEYGEN_API_KEY = os.environ.get('HEYGEN_API_KEY', '')
LUMA_API_KEY = os.environ.get('LUMA_API_KEY', '')
REPLICATE_API_KEY = os.environ.get('REPLICATE_API_KEY', '')

USED_FIGURES_BLOB = 'used_figures.json'


# --- GCS 使用済み管理 ---

def load_used_figures(bucket_name):
    """GCS から used_figures.json をダウンロードして使用済みリストを返す。"""
    from google.cloud import storage
    try:
        client = storage.Client()
        bucket = client.bucket(bucket_name)
        blob = bucket.blob(USED_FIGURES_BLOB)
        if not blob.exists():
            print("[used_figures] No existing file in GCS. Starting fresh.")
            return []
        data = json.loads(blob.download_as_text())
        used = data.get('used', [])
        print(f"[used_figures] Loaded {len(used)} used figures.")
        return used
    except Exception as e:
        print(f"[used_figures] Error loading: {e}")
        return []


def save_used_figures(bucket_name, used_list):
    """used_list を GCS の used_figures.json に上書き保存する。"""
    from google.cloud import storage
    try:
        client = storage.Client()
        bucket = client.bucket(bucket_name)
        blob = bucket.blob(USED_FIGURES_BLOB)
        blob.upload_from_string(
            json.dumps({'used': used_list}, ensure_ascii=False, indent=2),
            content_type='application/json'
        )
        print(f"[used_figures] Saved {len(used_list)} figures to GCS.")
    except Exception as e:
        print(f"[used_figures] Error saving: {e}")


# --- AI 人物選定 ---

def select_figure_with_ai(openai_client, used_list):
    """
    GPT-4o に使用済みリストを渡し、まだ登場していない日本の歴史的人物を1人選ばせる。

    Returns:
        dict: {name_jp, name_en, era, selected_event, portrait_description, gender}
    """
    used_str = json.dumps(used_list, ensure_ascii=False)
    prompt = f"""以下のリストに含まれていない、日本で有名な歴史的人物を1人選んでください。
使用済み: {used_str}

JSON形式のみで返してください（他のテキスト不要）:
{{
  "name_jp": "織田信長",
  "name_en": "Oda Nobunaga",
  "era": "戦国時代 16世紀",
  "selected_event": "長篠の戦い（1575年）",
  "portrait_description": "Oda Nobunaga, middle-aged Japanese warlord, sharp angular face with intense eyes, clean-shaven, wearing black lacquered samurai armor (kusazuri) with golden details, upright confident posture",
  "gender": "male"
}}

portrait_description には: 人物名、年齢感、顔立ち（目・鼻・口・肌色）、髪型、服装（具体的な素材・色・装飾）、体格・姿勢 を英語で詳しく記述してください。"""

    print("[select_figure] Asking GPT-4o to select a historical figure...")
    response = openai_client.chat.completions.create(
        model="gpt-4o",
        messages=[{"role": "user", "content": prompt}],
        response_format={"type": "json_object"},
        temperature=0.9
    )
    figure = json.loads(response.choices[0].message.content)
    print(f"[select_figure] Selected: {figure['name_jp']} ({figure['era']})")
    return figure


# --- Gemini Search で肖像画URL取得 ---

def find_portrait_url_with_gemini(name_jp, name_en):
    """
    Gemini で Wikipedia 記事名を特定 → Wikipedia API で実際の肖像画URLを取得する。

    Returns:
        str or None: 画像の直接URL（.jpg/.png）
    """
    import re

    headers = {'User-Agent': 'HistoricalReelBot/1.0'}
    api_url = "https://en.wikipedia.org/w/api.php"

    try:
        print(f"[portrait] Querying Wikipedia API for: {name_en}")

        # Step1: pageimages で主要画像を取得（高速）
        resp = requests.get(api_url, params={
            "action": "query", "titles": name_en,
            "prop": "pageimages", "format": "json",
            "pithumbsize": 800, "pilicense": "any", "redirects": 1
        }, timeout=15, headers=headers)
        pages = resp.json().get('query', {}).get('pages', {})
        for page in pages.values():
            thumb = page.get('thumbnail', {}).get('source', '')
            if thumb:
                print(f"[portrait] Found (pageimages): {thumb}")
                return thumb

        # Step2: 全画像リストから人名を含む .jpg を探す
        resp2 = requests.get(api_url, params={
            "action": "query", "titles": name_en,
            "prop": "images", "format": "json",
            "imlimit": 30, "redirects": 1
        }, timeout=15, headers=headers)
        pages2 = resp2.json().get('query', {}).get('pages', {})
        candidate = None
        name_keywords = name_en.lower().replace(' ', '_').split('_')
        for page in pages2.values():
            for img in page.get('images', []):
                title = img['title'].lower()
                if not title.endswith('.jpg') and not title.endswith('.jpeg'):
                    continue
                # 人名を含む画像を優先
                if any(k in title for k in name_keywords):
                    candidate = img['title']
                    break

        if candidate:
            # imageinfo で直接URLを取得
            resp3 = requests.get(api_url, params={
                "action": "query", "titles": candidate,
                "prop": "imageinfo", "iiprop": "url",
                "iiurlwidth": 800, "format": "json"
            }, timeout=15, headers=headers)
            for page in resp3.json().get('query', {}).get('pages', {}).values():
                info = page.get('imageinfo', [{}])[0]
                url = info.get('thumburl') or info.get('url', '')
                if url:
                    print(f"[portrait] Found (imageinfo): {url}")
                    return url

        print("[portrait] No portrait image found via Wikipedia API.")
        return None

    except Exception as e:
        print(f"[portrait] Error: {e}")
        return None


# --- スクリプト生成 ---

def generate_reel_script(figure, openai_client):
    """
    GPT-4o でリール動画のスクリプトを生成する。

    Returns:
        dict: {hook_speech, action1_scene, action2_scene, action3_scene, luma_prompt, caption}
    """
    portrait_desc = figure.get('portrait_description', f"{figure['name_en']}, historical Japanese figure from {figure['era']}, wearing traditional clothing")

    prompt = f"""あなたはInstagramリール動画のシナリオライターです。
以下の歴史的人物の動画スクリプトをJSON形式で作成してください。

人物: {figure['name_jp']}（{figure['name_en']}）
時代: {figure['era']}
取り上げるイベント: {figure['selected_event']}
人物外見: {portrait_desc}

要件:
- hook_speech: その人物が視聴者に語りかける日本語の台詞（3秒以内、インパクト重視）
- action1_scene: Gemini画像生成用の英語シーン描写（ストーリー冒頭）
  ★15秒の動画の「出発点」となる構図。人物が遠景〜中景で全身が映り、これから動き出す直前の静止
  ★カメラアングル・人物の向き・体の向きを明示すること
  ★形式: "[人物名], [服装・体格], [具体的なポーズ・向き], [場所・時間帯], [カメラアングル], dramatic lighting"
  ★例: "standing still facing left, full body, low angle shot, misty shore at dawn"
- action2_scene: 同上（ストーリー中盤・最も激しい動きの瞬間）
  ★action1とは明らかに異なる場所・アングル・ポーズにすること（小さな動きは禁止）
  ★体全体が大きく動いている瞬間（跳躍・斬撃・疾走・転倒など）を描写
  ★形式: action1_scene と同じ構造で、全く別の構図・アングルにする
- action3_scene: 同上（ストーリー終盤・結末の瞬間）
  ★action1・action2とは異なる場所・アングル・ポーズ
  ★物語の決着を示す構図（勝利・崩れ落ちる・遠ざかるなど）
  ★形式: action1_scene と同じ構造で、全く別の構図・アングルにする

  【3シーン構成の制約】
  - 3枚は「コマ撮りアニメのキーフレーム」として機能すること
  - action1→action2→action3 で人物の位置・向き・ポーズが大きく変化していること
  - 同じポーズ・同じアングルの繰り返しは禁止
  - 合計で約15秒の激しいアクションシーンを表現できる構成にすること

- luma_prompt: Luma Dream Machine用の英語テキストプロンプト（写真間アニメーション共通）
  ★人物の外見・舞台設定を簡潔に記述し、激しいcinematic motionを指示
  ★形式: "[人物名], [服装], [舞台], fast dramatic motion, cinematic action, dynamic camera, 4K historical drama"
- caption: Instagram投稿用の日本語キャプション（絵文字あり、ハッシュタグあり、300文字以内）

JSON形式のみで返してください:
{{
  "hook_speech": "...",
  "action1_scene": "...",
  "action2_scene": "...",
  "action3_scene": "...",
  "luma_prompt": "...",
  "caption": "..."
}}"""

    print("[generate_script] Generating reel script with GPT-4o...")
    response = openai_client.chat.completions.create(
        model="gpt-4o",
        messages=[{"role": "user", "content": prompt}],
        response_format={"type": "json_object"},
        temperature=0.8
    )
    script = json.loads(response.choices[0].message.content)
    print(f"[generate_script] Hook: {script['hook_speech'][:40]}...")
    print(f"[generate_script] Action1 scene: {script['action1_scene'][:80]}...")
    print(f"[generate_script] Action2 scene: {script['action2_scene'][:80]}...")
    print(f"[generate_script] Action3 scene: {script['action3_scene'][:80]}...")
    return script


# --- 肖像画生成 ---

def generate_portrait_with_imagen(name_jp, name_en, figure_key, timestamp, ref_data=None):
    """
    参照肖像画を忠実に実写化する。
    Step1: Gemini 2.0 Flash image generation (image-in → image-out)
    Step2 fallback: Gemini vision でテキスト描写 → Imagen 4 で生成
    Step3 fallback: DALL-E 3

    Args:
        ref_data: 参照肖像画のバイトデータ（オーケストレーター側でダウンロード済み）

    Returns:
        str: /tmp/reel_portrait_{key}_{ts}.jpg
    """
    from google import genai as gai
    from google.genai import types as gtypes

    output_path = f"/tmp/reel_portrait_{figure_key}_{timestamp}.jpg"
    gemini_api_key = os.environ.get('GEMINI_API_KEY', '')

    client = gai.Client(api_key=gemini_api_key)
    prompt_text = (
        f"この肖像画に描かれた人物（{name_jp}、{name_en}）を、"
        f"現代のプロカメラマンが撮影したかのようなフォトリアリスティックな実写写真に変換してください。"
        f"絵画・イラスト調は完全に排除し、本物の人間を高解像度カメラで撮影したリアルな写真として生成してください。"
        f"顔の特徴・服装・姿勢はこの肖像画に忠実に再現してください。"
    )

    # --- Step 1: Gemini native image generation (image-in → image-out) ---
    if ref_data:
        print(f"[portrait] Step1: Gemini native image generation...")
        for model_name in ["gemini-2.0-flash-exp-image-generation", "gemini-2.0-flash-preview-image-generation"]:
            try:
                contents = [gtypes.Content(role="user", parts=[
                    gtypes.Part.from_bytes(data=ref_data, mime_type="image/jpeg"),
                    gtypes.Part.from_text(text=prompt_text)
                ])]
                response = client.models.generate_content(
                    model=model_name,
                    contents=contents,
                    config=gtypes.GenerateContentConfig(
                        response_modalities=["IMAGE", "TEXT"]
                    )
                )
                for part in response.candidates[0].content.parts:
                    if hasattr(part, 'inline_data') and part.inline_data:
                        image_data = part.inline_data.data
                        with open(output_path, 'wb') as f:
                            f.write(image_data)
                        print(f"[portrait] Step1 success ({model_name}): {output_path}")
                        return output_path
                print(f"[portrait] Step1 ({model_name}): no image in response")
                break
            except Exception as e:
                print(f"[portrait] Step1 ({model_name}) failed: {e}")

    # --- Step 2: Gemini vision describe → Imagen 4 generate ---
    print(f"[portrait] Step2: Gemini vision → Imagen 4...")
    try:
        # Gemini vision で肖像画を詳細描写
        if ref_data:
            vision_contents = [gtypes.Content(role="user", parts=[
                gtypes.Part.from_bytes(data=ref_data, mime_type="image/jpeg"),
                gtypes.Part.from_text(text=(
                    f"This is a historical portrait of {name_en} ({name_jp}). "
                    f"Describe in detail: facial features (eyes shape, nose, mouth, skin tone, face shape), "
                    f"hairstyle, clothing/armor/robes, posture, expression, background. "
                    f"Write as an Imagen image generation prompt in English. "
                    f"Start with: 'Photorealistic portrait of {name_en}, '"
                ))
            ])]
            vision_resp = client.models.generate_content(
                model="gemini-2.0-flash",
                contents=vision_contents
            )
            imagen_prompt = vision_resp.text.strip()
        else:
            imagen_prompt = (
                f"Photorealistic portrait of {name_en} ({name_jp}), "
                f"historical Japanese figure, faithful to traditional portrait paintings, "
                f"cinematic lighting, detailed face, 9:16 aspect ratio"
            )
        print(f"[portrait] Imagen prompt: {imagen_prompt[:120]}...")

        imagen_result = client.models.generate_images(
            model="models/imagen-4.0-generate-001",
            prompt=imagen_prompt,
            config=dict(
                number_of_images=1,
                output_mime_type="image/jpeg",
                person_generation="ALLOW_ADULT",
                aspect_ratio="9:16",
            )
        )
        if imagen_result.generated_images:
            from PIL import Image
            from io import BytesIO
            img = Image.open(BytesIO(imagen_result.generated_images[0].image.image_bytes))
            img.save(output_path)
            print(f"[portrait] Step2 success (Imagen 4): {output_path}")
            return output_path
        raise Exception("Imagen 4 returned no images")
    except Exception as e:
        print(f"[portrait] Step2 failed: {e}. Falling back to DALL-E 3...")

    # --- Step 3: DALL-E 3 fallback ---
    return _generate_portrait_with_flux_fallback(
        f"Photorealistic portrait of {name_en}, faithful to historical portrait painting, cinematic lighting",
        output_path
    )


def _generate_portrait_with_flux_fallback(prompt, output_path):
    """Imagen 失敗時の DALL-E 3 フォールバック"""
    from openai import OpenAI
    client = OpenAI(api_key=os.environ.get('OPENAI_TOKEN', ''))
    response = client.images.generate(
        model="dall-e-3",
        prompt=prompt,
        size="1024x1792",
        quality="standard",
        n=1
    )
    image_data = requests.get(response.data[0].url, timeout=60).content
    with open(output_path, 'wb') as f:
        f.write(image_data)
    print(f"[dalle_fallback] Portrait saved: {output_path}")
    return output_path


def generate_action_photo_with_gemini(ref_data, figure, scene_description, segment, timestamp):
    """
    Gemini でアクションシーン写真を生成する。
    肖像画の参照データを使い、フォトリアリスティックな実写写真を生成する。

    Args:
        ref_data: 参照肖像画バイトデータ（None 可）
        figure: 人物情報 dict（name_jp, name_en を使用）
        scene_description: 英語のシーン描写テキスト
        segment: セグメント名（'action1', 'action2', 'action3'）
        timestamp: タイムスタンプ文字列

    Returns:
        str: /tmp/reel_action_photo_{segment}_{ts}.jpg
    """
    from google import genai as gai
    from google.genai import types as gtypes

    output_path = f"/tmp/reel_action_photo_{segment}_{timestamp}.jpg"
    gemini_api_key = os.environ.get('GEMINI_API_KEY', '')
    name_jp = figure['name_jp']
    name_en = figure['name_en']

    print(f"[action_photo] Generating {segment} with Gemini: {scene_description[:80]}...")

    prompt_text = (
        f"この写真の人物（{name_jp}、{name_en}）が以下のシーンにいる実写写真を生成してください。\n"
        f"人物の顔立ち・服装・体型はこの写真に忠実に再現し、全身が映るようにしてください。\n"
        f"フォトリアリスティックな実写写真として生成してください。\n\n"
        f"シーン: {scene_description}"
    )

    client = gai.Client(api_key=gemini_api_key)

    # Gemini native image generation (image-in → image-out)
    if ref_data:
        for model_name in ["gemini-2.0-flash-exp-image-generation", "gemini-2.0-flash-preview-image-generation"]:
            try:
                contents = [gtypes.Content(role="user", parts=[
                    gtypes.Part.from_bytes(data=ref_data, mime_type="image/jpeg"),
                    gtypes.Part.from_text(text=prompt_text)
                ])]
                response = client.models.generate_content(
                    model=model_name,
                    contents=contents,
                    config=gtypes.GenerateContentConfig(
                        response_modalities=["IMAGE", "TEXT"]
                    )
                )
                for part in response.candidates[0].content.parts:
                    if hasattr(part, 'inline_data') and part.inline_data:
                        with open(output_path, 'wb') as f:
                            f.write(part.inline_data.data)
                        print(f"[action_photo] Generated ({model_name}): {output_path}")
                        return output_path
                print(f"[action_photo] {model_name}: no image in response")
                break
            except Exception as e:
                print(f"[action_photo] {model_name} failed: {e}")

    # Fallback: DALL-E 3
    print(f"[action_photo] Falling back to DALL-E 3 for {segment}...")
    fallback_prompt = (
        f"Photorealistic full-body photo of {name_en}, {scene_description}, "
        f"cinematic lighting, 9:16 aspect ratio, historical drama"
    )
    return _generate_portrait_with_flux_fallback(fallback_prompt, output_path)


# --- TTS 音声生成 ---

def generate_tts_audio(text, prefix, timestamp, openai_client, gender="male"):
    """
    OpenAI TTS で音声ファイルを生成する。

    Returns:
        str: /tmp/reel_{prefix}_{ts}.mp3
    """
    voice = "nova" if gender == "female" else "onyx"
    output_path = f"/tmp/reel_{prefix}_{timestamp}.mp3"

    print(f"[tts] Generating audio: '{text[:40]}...' (voice={voice})")
    response = openai_client.audio.speech.create(
        model="tts-1",
        voice=voice,
        input=text,
        speed=0.9
    )
    response.stream_to_file(output_path)
    print(f"[tts] Audio saved: {output_path}")
    return output_path


# --- HeyGen ---

HEYGEN_PHOTO_ID_BLOB = 'heygen_talking_photo_id.json'


def _load_heygen_photo_id(bucket_name):
    """GCS から前回アップロードした talking_photo_id を取得する。"""
    from google.cloud import storage
    try:
        client = storage.Client()
        blob = client.bucket(bucket_name).blob(HEYGEN_PHOTO_ID_BLOB)
        if not blob.exists():
            return None
        return json.loads(blob.download_as_text()).get('talking_photo_id')
    except Exception as e:
        print(f"[heygen] Could not load previous photo id: {e}")
        return None


def _save_heygen_photo_id(bucket_name, talking_photo_id):
    """GCS に今回アップロードした talking_photo_id を保存する。"""
    from google.cloud import storage
    try:
        client = storage.Client()
        blob = client.bucket(bucket_name).blob(HEYGEN_PHOTO_ID_BLOB)
        blob.upload_from_string(
            json.dumps({'talking_photo_id': talking_photo_id}),
            content_type='application/json'
        )
        print(f"[heygen] Saved talking_photo_id to GCS: {talking_photo_id}")
    except Exception as e:
        print(f"[heygen] Could not save photo id: {e}")


def _delete_heygen_photo_id(talking_photo_id):
    """HeyGen から指定の talking photo を削除する。"""
    try:
        res = requests.delete(
            f'https://api.heygen.com/v2/talking_photo/{talking_photo_id}',
            headers={'X-Api-Key': HEYGEN_API_KEY},
            timeout=30
        )
        print(f"[heygen] Deleted previous talking photo {talking_photo_id}: {res.status_code}")
    except Exception as e:
        print(f"[heygen] Delete error (non-fatal): {e}")


def upload_to_heygen(file_path, file_type):
    """
    HeyGen にファイルをアップロードして asset ID を返す。

    Args:
        file_type: 'image' (talking_photo) or 'audio'

    Returns:
        str: HeyGen talking_photo_id or audio asset ID
    """
    print(f"[heygen] Uploading {file_type}: {file_path}")
    with open(file_path, 'rb') as f:
        file_data = f.read()

    if file_type == 'image':
        # 画像は raw バイナリで talking_photo エンドポイントにアップロード
        response = requests.post(
            'https://upload.heygen.com/v1/talking_photo',
            headers={
                'X-Api-Key': HEYGEN_API_KEY,
                'Content-Type': 'image/jpeg'
            },
            data=file_data,
            timeout=60
        )
        if response.status_code != 200:
            raise Exception(f"HeyGen talking_photo upload failed: {response.text}")
        asset_id = response.json()['data']['talking_photo_id']
    else:
        # 音声は raw バイナリで asset エンドポイントにアップロード
        response = requests.post(
            'https://upload.heygen.com/v1/asset',
            headers={
                'X-Api-Key': HEYGEN_API_KEY,
                'Content-Type': 'audio/mpeg'
            },
            data=file_data,
            timeout=60
        )
        if response.status_code != 200:
            raise Exception(f"HeyGen audio upload failed: {response.text}")
        asset_id = response.json()['data']['id']

    print(f"[heygen] Uploaded asset ID: {asset_id}")
    return asset_id


def create_ken_burns_speech_video(portrait_path, audio_path, segment_name, timestamp):
    """
    FFmpeg で肖像画 + TTS音声 + Ken Burns ズームエフェクトの動画を生成する。

    Returns:
        str: /tmp/reel_{segment}_{ts}.mp4
    """
    output_path = f"/tmp/reel_{segment_name}_{timestamp}.mp4"
    print(f"[ffmpeg] Creating Ken Burns speech video: {segment_name}...")

    cmd = [
        'ffmpeg', '-y',
        '-loop', '1', '-i', portrait_path,
        '-i', audio_path,
        '-filter_complex',
        '[0:v]scale=720:1280:force_original_aspect_ratio=increase,'
        'crop=720:1280,'
        'zoompan=z=\'min(zoom+0.0008,1.3)\':x=\'iw/2-(iw/zoom/2)\':y=\'ih/2-(ih/zoom/2)\':'
        'd=\'25*8\':s=720x1280:fps=25,'
        'setsar=1[v]',
        '-map', '[v]', '-map', '1:a',
        '-c:v', 'libx264', '-c:a', 'aac',
        '-shortest',
        '-pix_fmt', 'yuv420p',
        '-movflags', '+faststart',
        output_path
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise Exception(f"FFmpeg Ken Burns failed: {result.stderr[-500:]}")
    print(f"[ffmpeg] Ken Burns video saved: {output_path}")
    return output_path


def create_heygen_talking_video(portrait_asset_id, audio_asset_id, segment_name, timestamp,
                                portrait_path=None, audio_path=None):
    """
    HeyGen でトーキングヘッド動画を生成する。
    失敗時は FFmpeg Ken Burns にフォールバック。

    Returns:
        str: /tmp/reel_{segment}_{ts}.mp4
    """
    output_path = f"/tmp/reel_{segment_name}_{timestamp}.mp4"
    print(f"[heygen] Creating talking video: {segment_name}...")

    # アップロード失敗で asset_id が None の場合は即フォールバック
    if not portrait_asset_id or not audio_asset_id:
        print(f"[heygen] No asset IDs. Using FFmpeg Ken Burns fallback.")
        if portrait_path and audio_path:
            return create_ken_burns_speech_video(portrait_path, audio_path, segment_name, timestamp)
        raise Exception("No HeyGen assets and no fallback paths provided.")

    try:
        payload = {
            "video_inputs": [
                {
                    "character": {
                        "type": "talking_photo",
                        "talking_photo_id": portrait_asset_id
                    },
                    "voice": {
                        "type": "audio",
                        "audio_asset_id": audio_asset_id
                    }
                }
            ],
            "dimension": {"width": 720, "height": 1280}
        }
        response = requests.post(
            'https://api.heygen.com/v2/video/generate',
            headers={
                'X-Api-Key': HEYGEN_API_KEY,
                'Content-Type': 'application/json'
            },
            json=payload,
            timeout=60
        )
        if response.status_code != 200:
            raise Exception(f"HeyGen video create failed: {response.text}")

        video_id = response.json()['data']['video_id']
        print(f"[heygen] Video ID: {video_id}. Polling for completion...")

        start = time.time()
        while time.time() - start < 300:
            time.sleep(5)
            status_response = requests.get(
                f'https://api.heygen.com/v1/video_status.get?video_id={video_id}',
                headers={'X-Api-Key': HEYGEN_API_KEY},
                timeout=30
            )
            if status_response.status_code != 200:
                continue
            data = status_response.json().get('data', {})
            status = data.get('status')
            print(f"[heygen] Status: {status}")
            if status == 'completed':
                video_url = data['video_url']
                video_data = requests.get(video_url, timeout=120).content
                with open(output_path, 'wb') as f:
                    f.write(video_data)
                print(f"[heygen] Video saved: {output_path}")
                return output_path
            elif status in ('failed', 'error'):
                raise Exception(f"HeyGen video failed: {data}")

        raise Exception("HeyGen video generation timed out after 300 seconds")

    except Exception as e:
        print(f"[heygen] Failed: {e}. Falling back to FFmpeg Ken Burns...")
        if portrait_path and audio_path:
            return create_ken_burns_speech_video(portrait_path, audio_path, segment_name, timestamp)
        raise


# --- Luma Dream Machine ---

def create_luma_action_video(frame0_url, frame1_url, luma_prompt, segment_name, timestamp, duration="9s"):
    """
    Luma Dream Machine ray-3.14 でアクション動画を生成してダウンロードする。
    frame0 → frame1 のキーフレーム補間アニメーション。

    Returns:
        str: /tmp/reel_{segment}_{ts}.mp4
    """
    output_path = f"/tmp/reel_{segment_name}_{timestamp}.mp4"
    print(f"[luma] Creating action video: {segment_name} (frame0={frame0_url[:60]}... frame1={frame1_url[:60]}...)")

    payload = {
        "model": "ray-2",
        "prompt": luma_prompt,
        "keyframes": {
            "frame0": {
                "type": "image",
                "url": frame0_url
            },
            "frame1": {
                "type": "image",
                "url": frame1_url
            }
        },
        "duration": duration,
        "aspect_ratio": "9:16"
    }
    response = requests.post(
        'https://api.lumalabs.ai/dream-machine/v1/generations',
        headers={
            'Authorization': f'Bearer {LUMA_API_KEY}',
            'Content-Type': 'application/json'
        },
        json=payload,
        timeout=60
    )
    if response.status_code not in (200, 201):
        raise Exception(f"Luma generation create failed: {response.text}")

    generation_id = response.json()['id']
    print(f"[luma] Generation ID: {generation_id}. Polling for completion...")

    # ポーリング
    start = time.time()
    while time.time() - start < 300:
        time.sleep(5)
        status_response = requests.get(
            f'https://api.lumalabs.ai/dream-machine/v1/generations/{generation_id}',
            headers={'Authorization': f'Bearer {LUMA_API_KEY}'},
            timeout=30
        )
        if status_response.status_code != 200:
            continue
        data = status_response.json()
        state = data.get('state')
        print(f"[luma] State: {state}")
        if state == 'completed':
            video_url = data['assets']['video']
            video_data = requests.get(video_url, timeout=120).content
            with open(output_path, 'wb') as f:
                f.write(video_data)
            print(f"[luma] Video saved: {output_path}")
            return output_path
        elif state in ('failed', 'error'):
            raise Exception(f"Luma generation failed: {data}")

    raise Exception("Luma generation timed out after 300 seconds")


# --- FFmpeg 結合 ---

def concatenate_videos_with_ffmpeg(video_paths, output_path):
    """
    FFmpeg で複数の動画を縦型（720x1280）に結合する。

    Returns:
        str: output_path
    """
    list_file = output_path.replace('.mp4', '_list.txt')
    with open(list_file, 'w') as f:
        for path in video_paths:
            f.write(f"file '{path}'\n")

    print(f"[ffmpeg] Concatenating {len(video_paths)} videos -> {output_path}")
    cmd = [
        'ffmpeg', '-y',
        '-f', 'concat', '-safe', '0', '-i', list_file,
        '-c:v', 'libx264', '-c:a', 'aac',
        '-vf', 'scale=720:1280:force_original_aspect_ratio=decrease,pad=720:1280:(ow-iw)/2:(oh-ih)/2,setsar=1',
        '-r', '30', '-b:v', '4M', '-movflags', '+faststart', '-pix_fmt', 'yuv420p',
        output_path
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise Exception(f"FFmpeg failed: {result.stderr}")

    os.remove(list_file)
    print(f"[ffmpeg] Final video saved: {output_path}")
    return output_path


# --- オーケストレーター ---

def generate_historical_reel(openai_client, upload_func, bucket_name):
    """
    歴史的人物リール動画を生成するオーケストレーター関数。

    Args:
        openai_client: OpenAI クライアント
        upload_func: GCSアップロード関数 (blob_name, file_path, bucket_name) -> public_url
        bucket_name: GCS バケット名

    Returns:
        dict: {video_path, portrait_gcs_url, figure, script, caption, temp_files}
    """
    ts = str(int(time.time()))
    temp_files = []

    # Step 1: 使用済みリスト取得
    print("\n=== Step 1: Load used figures ===")
    used_list = load_used_figures(bucket_name)

    # Step 2: AI が人物選定
    print("\n=== Step 2: AI selects historical figure ===")
    figure = select_figure_with_ai(openai_client, used_list)
    figure_key = figure['name_en'].replace(' ', '_').lower()

    # Step 3: スクリプト生成
    print("\n=== Step 3: Generate reel script ===")
    script = generate_reel_script(figure, openai_client)

    # Step 4: Gemini Search で有名肖像画URLを取得
    print("\n=== Step 4: Find portrait URL with Gemini Search ===")
    portrait_image_url = find_portrait_url_with_gemini(figure['name_jp'], figure['name_en'])

    # Step 4b: ref_data をオーケストレーター側でダウンロード（generate_portrait と action_photo で共有）
    print("\n=== Step 4b: Download reference portrait image ===")
    ref_data = None
    if portrait_image_url:
        try:
            r = requests.get(portrait_image_url, timeout=30, headers={'User-Agent': 'Mozilla/5.0'})
            if len(r.content) >= 1000:
                ref_data = r.content
                print(f"[portrait] Reference image downloaded: {len(ref_data)} bytes")
            else:
                print(f"[portrait] Reference image too small, skipping")
        except Exception as e:
            print(f"[portrait] Failed to download reference: {e}")

    # Step 4c: 肖像画を実写化
    print("\n=== Step 4c: Generate photorealistic portrait with Imagen ===")
    portrait_path = generate_portrait_with_imagen(
        figure['name_jp'], figure['name_en'],
        figure_key, ts,
        ref_data=ref_data
    )
    temp_files.append(portrait_path)

    # Step 4d: 肖像画を GCS にアップロード
    print("\n=== Step 4d: Upload portrait to GCS ===")
    portrait_gcs_url = upload_func(f"reel_portrait_{figure_key}_{ts}.jpg", portrait_path, bucket_name)
    print(f"[gcs] Portrait URL: {portrait_gcs_url}")

    # Step 5: TTS 音声生成（hook のみ）
    print("\n=== Step 5: Generate TTS audio ===")
    gender = figure.get('gender', 'male')
    hook_audio_path = generate_tts_audio(script['hook_speech'], 'hook_audio', ts, openai_client, gender)
    temp_files.append(hook_audio_path)

    # Step 6: HeyGen にアップロード → 失敗時は portrait_asset_id=None でスキップ
    print("\n=== Step 6: Upload to HeyGen (fallback to FFmpeg if quota exceeded) ===")
    portrait_asset_id = None
    hook_audio_asset_id = None
    try:
        prev_photo_id = _load_heygen_photo_id(bucket_name)
        if prev_photo_id:
            _delete_heygen_photo_id(prev_photo_id)
        portrait_asset_id = upload_to_heygen(portrait_path, 'image')
        _save_heygen_photo_id(bucket_name, portrait_asset_id)
        hook_audio_asset_id = upload_to_heygen(hook_audio_path, 'audio')
        print("[heygen] All assets uploaded successfully.")
    except Exception as e:
        print(f"[heygen] Upload failed: {e}. Will use FFmpeg Ken Burns for speech segments.")

    # Step 6b: HeyGen で hook 動画生成（アップロード失敗時は直接 FFmpeg Ken Burns）
    print("\n=== Step 6b: Create hook talking video (HeyGen or FFmpeg fallback) ===")
    hook_video_path = create_heygen_talking_video(
        portrait_asset_id, hook_audio_asset_id, 'hook', ts,
        portrait_path=portrait_path, audio_path=hook_audio_path
    )
    temp_files.append(hook_video_path)

    # Step 6.5: Gemini でアクション写真 x3 生成（実写化 portrait を参照元として使用）
    print("\n=== Step 6.5: Generate action photos with Gemini ===")
    with open(portrait_path, 'rb') as f:
        portrait_ref_data = f.read()
    action_photo1_path = generate_action_photo_with_gemini(
        portrait_ref_data, figure, script['action1_scene'], 'action1', ts
    )
    action_photo2_path = generate_action_photo_with_gemini(
        portrait_ref_data, figure, script['action2_scene'], 'action2', ts
    )
    action_photo3_path = generate_action_photo_with_gemini(
        portrait_ref_data, figure, script['action3_scene'], 'action3', ts
    )
    temp_files.extend([action_photo1_path, action_photo2_path, action_photo3_path])

    # Step 6.6: アクション写真を GCS にアップロード
    print("\n=== Step 6.6: Upload action photos to GCS ===")
    action1_gcs_url = upload_func(f"reel_action_photo_action1_{ts}.jpg", action_photo1_path, bucket_name)
    action2_gcs_url = upload_func(f"reel_action_photo_action2_{ts}.jpg", action_photo2_path, bucket_name)
    action3_gcs_url = upload_func(f"reel_action_photo_action3_{ts}.jpg", action_photo3_path, bucket_name)
    print(f"[gcs] Action photo 1: {action1_gcs_url}")
    print(f"[gcs] Action photo 2: {action2_gcs_url}")
    print(f"[gcs] Action photo 3: {action3_gcs_url}")

    # Step 7: Luma アクション動画 x2（photo1→photo2, photo2→photo3）
    print("\n=== Step 7: Create Luma action videos ===")
    luma1_path = create_luma_action_video(
        action1_gcs_url, action2_gcs_url, script['luma_prompt'], 'luma1', ts, duration="9s"
    )
    luma2_path = create_luma_action_video(
        action2_gcs_url, action3_gcs_url, script['luma_prompt'], 'luma2', ts, duration="9s"
    )
    temp_files.extend([luma1_path, luma2_path])

    # Step 8: FFmpeg で結合（hook → luma1 → luma2）
    print("\n=== Step 8: Concatenate with FFmpeg ===")
    final_path = f"/tmp/reel_final_{ts}.mp4"
    concatenate_videos_with_ffmpeg(
        [hook_video_path, luma1_path, luma2_path],
        final_path
    )
    temp_files.append(final_path)

    # Step 9: 使用済みリスト更新
    print("\n=== Step 9: Save used figures ===")
    used_list.append(figure['name_jp'])
    save_used_figures(bucket_name, used_list)

    print("\n=== Historical Reel Generation Complete ===")
    return {
        'video_path': final_path,
        'portrait_gcs_url': portrait_gcs_url,
        'figure': figure,
        'script': script,
        'caption': script['caption'],
        'temp_files': temp_files
    }
