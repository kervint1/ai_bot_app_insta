import os
import time
import base64
import requests
from flask import Flask, request
import news_satire
import historical_reel
from openai import OpenAI
from google.cloud import storage
from google import genai
from google.genai import types

app = Flask(__name__)

PAGE_ACCESS_TOKEN = os.environ.get('INSTA_PAGE_ACCESS_TOKEN', '')
BUSINESS_ACCOUNT_ID = os.environ.get('INSTA_BUSINESS_ACCOUNT_ID', '')
FACEBOOK_PAGE_ACCESS_TOKEN = os.environ.get('FACEBOOK_PAGE_ACCESS_TOKEN', '')
FACEBOOK_PAGE_ID = os.environ.get('FACEBOOK_PAGE_ID', '')
openai = OpenAI(api_key=os.environ.get('OPENAI_TOKEN', ''))
THREADS_API_TOKEN = os.environ.get('THREADS_API_TOKEN', '')
THREADS_USER_ID = os.environ.get('THREADS_USER_ID', '')
CLOUD_STORAGE_BUCKET_NAME = os.environ.get('CLOUD_STORAGE_BUCKET_NAME', '')


# --- Endpoints ---

@app.route('/news_satire_post_insta', methods=['GET'])
def news_satire_post_insta():
    """
    Generate a news satire image using news_satire module,
    upload to Google Cloud Storage, and post to Instagram (Feed + Story).

    Query Parameters:
        category: News category (optional, random if not specified)
    """
    print("--- Starting News Satire Post to Instagram ---")

    category = request.args.get('category', None)
    print(f"Category: {category if category else 'random (all genres, excluding politics)'}")

    test_mode = os.environ.get('TEST_MODE', 'true').lower() == 'true'
    print(f"TEST_MODE: {test_mode}")

    try:
        genai_client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))

        print("Generating satire image with news_satire module...")
        result = news_satire.generate_satire_image(
            category=category,
            openai_client=openai,
            gemini_chat_func=gemini_chat_with_image,
            genai_client=genai_client,
            save_to_tmp=False
        )

        print(f"Image generated: {result['image_path']}")
        print(f"News: {result['news']['title']}")
        print(f"Caption: {result['caption'][:100]}...")

        current_time_string = str(int(time.time()))
        print("Uploading image to Google Cloud Storage...")
        image_url = upload_to_bucket(current_time_string, result['image_path'], CLOUD_STORAGE_BUCKET_NAME)
        print(f"Image uploaded to GCS: {image_url}")

        if not test_mode:
            print("Posting to Instagram (Feed + Story)...")
            exec_instagram_post(image_url, result['caption'])
            print("Instagram post complete.")

            if FACEBOOK_PAGE_ACCESS_TOKEN and FACEBOOK_PAGE_ID:
                try:
                    print("Posting to Facebook Page...")
                    exec_facebook_post(image_url, result['caption'])
                    print("Facebook post complete.")
                except Exception as e:
                    print(f"Facebook post failed (skipping): {e}")
            else:
                print("[SKIP] Facebook credentials not configured")
        else:
            print("[TEST_MODE] Skipping Instagram and Facebook posts")

        print("Removing temporary image file...")
        remove_img_file(result['image_path'])

        print("--- Finished News Satire Post to Instagram ---")

        return {
            "status": "success",
            "test_mode": test_mode,
            "news_title": result['news']['title'],
            "news_url": result['news']['url'],
            "news_category": result['news']['category'],
            "satire_concept": result['satire_concept'],
            "caption": result['caption'],
            "image_url": image_url if not test_mode else None
        }, 200

    except Exception as e:
        print(f"Error in news_satire_post_insta: {e}")
        import traceback
        traceback.print_exc()
        return {"status": "error", "error": str(e)}, 500


@app.route('/test_heygen_upload', methods=['GET'])
def test_heygen_upload():
    """HeyGen の画像アップロード（talking_photo）と音声アップロードをテスト"""
    import historical_reel as hr
    import tempfile, os

    try:
        # テスト用: 公開されている人物画像をダウンロードして使用
        # (Wikimedia Commons パブリックドメインの肖像画)
        test_img_url = "https://randomuser.me/api/portraits/men/10.jpg"
        img_data = requests.get(test_img_url, timeout=30).content
        img_path = '/tmp/test_heygen_img.jpg'
        with open(img_path, 'wb') as f:
            f.write(img_data)

        photo_id = hr.upload_to_heygen(img_path, 'image')
        os.remove(img_path)

        return {"status": "success", "talking_photo_id": photo_id}, 200
    except Exception as e:
        return {"status": "error", "error": str(e)}, 500


@app.route('/test_portrait_search', methods=['GET'])
def test_portrait_search():
    """Gemini Search で肖像画URLを取得 → Flux img2img → ローカル保存してURLを返す"""
    import historical_reel as hr

    ts = str(int(time.time()))
    used_list = hr.load_used_figures(CLOUD_STORAGE_BUCKET_NAME)

    try:
        figure = hr.select_figure_with_ai(openai, used_list)
        print(f"Selected: {figure['name_jp']}")

        portrait_url = hr.find_portrait_url_with_gemini(figure['name_jp'], figure['name_en'])
        if not portrait_url:
            return {"status": "error", "error": "Gemini could not find portrait URL", "figure": figure}, 500

        # 参照元の肖像画をローカル保存
        ref_data = requests.get(portrait_url, timeout=30, headers={'User-Agent': 'Mozilla/5.0'}).content
        save_dir = '/app/satire_test_images'
        os.makedirs(save_dir, exist_ok=True)
        ref_path = f"{save_dir}/ref_portrait_{ts}.jpg"
        with open(ref_path, 'wb') as f:
            f.write(ref_data)

        # Flux img2img で実写化してローカル保存
        figure_key = figure['name_en'].replace(' ', '_').lower()
        flux_path = hr.generate_portrait_with_imagen(
            figure['name_jp'], figure['name_en'],
            figure_key, ts,
            ref_data=ref_data
        )
        import shutil
        flux_save_path = f"{save_dir}/flux_portrait_{ts}.jpg"
        shutil.copy(flux_path, flux_save_path)

        return {
            "status": "success",
            "figure": figure['name_jp'],
            "portrait_url_found": portrait_url,
            "ref_saved": f"./satire_test_images/ref_portrait_{ts}.jpg",
            "flux_saved": f"./satire_test_images/flux_portrait_{ts}.jpg"
        }, 200

    except Exception as e:
        import traceback; traceback.print_exc()
        return {"status": "error", "error": str(e)}, 500


@app.route('/test_flux', methods=['GET'])
def test_flux():
    """Replicate Flux.1 で肖像画生成テスト（GCSアップロードまで）"""
    import historical_reel as hr

    ts = str(int(time.time()))
    try:
        portrait_path = hr.generate_portrait_with_flux(
            "A Japanese samurai warrior in traditional armor, historical portrait, photorealistic",
            "test", ts
        )
        portrait_url = upload_to_bucket(f"test_portrait_{ts}.jpg", portrait_path, CLOUD_STORAGE_BUCKET_NAME)
        if os.path.exists(portrait_path):
            os.remove(portrait_path)
        return {"status": "success", "portrait_url": portrait_url}, 200
    except Exception as e:
        return {"status": "error", "error": str(e)}, 500


@app.route('/test_luma', methods=['GET'])
def test_luma():
    """Luma Dream Machine で動画生成テスト（画像URLが必要: ?image_url=...）"""
    import historical_reel as hr

    image_url = request.args.get('image_url', '')
    if not image_url:
        return {"status": "error", "error": "image_url query param required. Run /test_flux first."}, 400

    ts = str(int(time.time()))
    try:
        video_path = hr.create_luma_action_video(
            image_url, image_url,
            "A Japanese samurai warrior walking through ancient castle, cinematic style, fast dramatic motion",
            "test_action", ts
        )
        video_url = upload_to_bucket(f"test_luma_{ts}.mp4", video_path, CLOUD_STORAGE_BUCKET_NAME)
        if os.path.exists(video_path):
            os.remove(video_path)
        return {"status": "success", "video_url": video_url}, 200
    except Exception as e:
        return {"status": "error", "error": str(e)}, 500


@app.route('/historical_reel_post_insta', methods=['GET'])
def historical_reel_post_insta():
    """
    AIが歴史的人物を動的選定し、リール動画を生成してInstagramに投稿する。
    かぶり防止: 使用済み人物リストをGCSで管理。
    """
    print("--- Starting Historical Reel Post to Instagram ---")

    test_mode = os.environ.get('TEST_MODE', 'true').lower() == 'true'
    print(f"TEST_MODE: {test_mode}")

    try:
        print("Generating historical reel...")
        result = historical_reel.generate_historical_reel(
            openai_client=openai,
            upload_func=upload_to_bucket,
            bucket_name=CLOUD_STORAGE_BUCKET_NAME
        )

        figure = result['figure']
        caption = result['caption']
        video_path = result['video_path']
        portrait_gcs_url = result['portrait_gcs_url']

        print(f"Reel generated for: {figure['name_jp']} ({figure['era']})")
        print(f"Caption: {caption[:100]}...")

        if not test_mode:
            current_time_string = str(int(time.time()))
            print("Uploading final video to GCS...")
            video_url = upload_to_bucket(
                f"reel_final_{current_time_string}.mp4",
                video_path,
                CLOUD_STORAGE_BUCKET_NAME
            )
            print(f"Video uploaded to GCS: {video_url}")

            print("Posting Reel to Instagram...")
            exec_instagram_reel_post(video_url, caption)
            print("Instagram Reel post complete.")
        else:
            video_url = None
            print("[TEST_MODE] Skipping Instagram Reel post")
            # TEST_MODE: 確認用に動画を satire_test_images にコピー
            import shutil
            save_dir = '/app/satire_test_images'
            os.makedirs(save_dir, exist_ok=True)
            saved_video = f"{save_dir}/reel_test_{int(time.time())}.mp4"
            shutil.copy2(video_path, saved_video)
            print(f"[TEST_MODE] Video saved for review: {saved_video}")

        # 一時ファイル削除
        for f in result['temp_files']:
            if os.path.exists(f):
                os.remove(f)
                print(f"Removed temp file: {f}")

        print("--- Finished Historical Reel Post to Instagram ---")

        return {
            "status": "success",
            "test_mode": test_mode,
            "figure": figure['name_jp'],
            "era": figure['era'],
            "event": figure['selected_event'],
            "caption": caption,
            "portrait_gcs_url": portrait_gcs_url,
            "video_url": video_url
        }, 200

    except Exception as e:
        print(f"Error in historical_reel_post_insta: {e}")
        import traceback
        traceback.print_exc()
        return {"status": "error", "error": str(e)}, 500


@app.route('/test_news_satire', methods=['GET'])
def test_news_satire():
    """
    ローカルテスト用: 風刺画生成のみ（Instagram投稿なし）

    Query Parameters:
        category: ニュースカテゴリ（省略時はランダム）
    """
    print("--- Starting News Satire Test ---")

    category = request.args.get('category', None)

    try:
        genai_client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))

        result = news_satire.generate_satire_image(
            category=category,
            openai_client=openai,
            gemini_chat_func=gemini_chat_with_image,
            genai_client=genai_client,
            save_to_tmp=True
        )

        print("--- Test Complete ---")

        return {"status": "success", "test_mode": True, **result}, 200

    except Exception as e:
        print(f"Error in test_news_satire: {e}")
        import traceback
        traceback.print_exc()
        return {"status": "error", "error": str(e)}, 500


# --- Utility Functions ---

def upload_to_bucket(blob_name, file_path, bucket_name):
    storage_client = storage.Client()
    bucket = storage_client.bucket(bucket_name)
    blob = bucket.blob(blob_name)
    blob.upload_from_filename(file_path)
    return blob.public_url


def gemini_chat_with_image(image_path, prompt_text):
    print(f"Generating Gemini caption for image: {image_path}")
    try:
        with open(image_path, "rb") as img_file:
            image_bytes = img_file.read()
        encoded_image = base64.b64encode(image_bytes)

        contents = [
            types.Content(
                role="user",
                parts=[
                    types.Part.from_bytes(mime_type="image/jpeg", data=base64.b64decode(encoded_image)),
                    types.Part.from_text(text=prompt_text)
                ],
            )
        ]

        generate_content_config = types.GenerateContentConfig(response_mime_type="text/plain")
        genai_client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))

        response = ""
        for chunk in genai_client.models.generate_content_stream(
            model="gemini-2.5-flash",
            contents=contents,
            config=generate_content_config,
        ):
            response += chunk.text or ""
        print(f"Gemini response: {response}")
        return response

    except Exception as e:
        print(f"Error during image + text Gemini request: {e}")
        return f"Error: {e}"


def wait_for_media_ready(media_id, access_token, timeout=120, poll_interval=2):
    print(f"Waiting for media container {media_id} to be ready...")
    start_time = time.time()
    while time.time() - start_time < timeout:
        status_url = f"https://graph.facebook.com/v22.0/{media_id}?fields=status_code&access_token={access_token}"
        response = requests.get(status_url)
        if response.status_code != 200:
            time.sleep(poll_interval)
            continue

        status = response.json().get("status_code")
        print(f"Current media status: {status}")

        if status == "FINISHED":
            print("Media is ready for publishing.")
            return True
        elif status in ("ERROR", "EXPIRED"):
            raise Exception(f"Media container failed with status: {status}")

        time.sleep(poll_interval)

    raise Exception(f"Media container not ready after {timeout} seconds.")


def wait_for_threads_media_ready(media_id, access_token, timeout=120, poll_interval=2):
    print(f"Waiting for Threads media container {media_id} to be ready...")
    start_time = time.time()
    while time.time() - start_time < timeout:
        status_url = f"https://graph.threads.net/v1.0/{media_id}?fields=status&access_token={access_token}"
        response = requests.get(status_url)
        if response.status_code != 200:
            time.sleep(poll_interval)
            continue

        status = response.json().get("status")
        print(f"Current Threads media status: {status}")

        if status == "FINISHED":
            print("Threads media is ready for publishing.")
            return True
        elif status == "ERROR":
            raise Exception(f"Threads media container failed with status: {status}")

        time.sleep(poll_interval)

    raise Exception(f"Threads media container not ready after {timeout} seconds.")


def exec_instagram_post(image_url, caption):
    print("Executing Instagram post...")

    # Feed post
    url = f"https://graph.facebook.com/v22.0/{BUSINESS_ACCOUNT_ID}/media"
    response = requests.post(url, params={'access_token': PAGE_ACCESS_TOKEN, 'image_url': image_url, 'caption': caption})
    print(f"Media container status: {response.status_code} {response.text}")
    if response.status_code != 200:
        raise Exception(f"Failed to upload image: {response.text}")
    media_id = response.json()['id']

    wait_for_media_ready(media_id, PAGE_ACCESS_TOKEN)

    url = f"https://graph.facebook.com/v22.0/{BUSINESS_ACCOUNT_ID}/media_publish"
    response = requests.post(url, params={'access_token': PAGE_ACCESS_TOKEN, 'creation_id': media_id})
    print(f"Publish post status: {response.status_code} {response.text}")
    if response.status_code != 200:
        raise Exception(f"Failed to publish photo: {response.text}")

    # Story post
    url = f"https://graph.facebook.com/v22.0/{BUSINESS_ACCOUNT_ID}/media"
    response = requests.post(url, params={'access_token': PAGE_ACCESS_TOKEN, 'image_url': image_url, 'media_type': 'STORIES'})
    print(f"Story container status: {response.status_code} {response.text}")
    if response.status_code != 200:
        raise Exception(f"Failed to upload image for story: {response.text}")
    media_id = response.json()['id']

    wait_for_media_ready(media_id, PAGE_ACCESS_TOKEN)

    url = f"https://graph.facebook.com/v22.0/{BUSINESS_ACCOUNT_ID}/media_publish"
    response = requests.post(url, params={'access_token': PAGE_ACCESS_TOKEN, 'creation_id': media_id})
    print(f"Publish story status: {response.status_code} {response.text}")
    if response.status_code != 200:
        raise Exception(f"Failed to publish story: {response.text}")

    print('Instagram post and story published successfully!')


def exec_instagram_reel_post(video_url, caption):
    print("Executing Instagram Reel post...")

    url = f"https://graph.facebook.com/v22.0/{BUSINESS_ACCOUNT_ID}/media"
    params = {
        'access_token': PAGE_ACCESS_TOKEN,
        'video_url': video_url,
        'media_type': 'REELS',
        'caption': caption,
        'share_to_feed': 'true'
    }
    response = requests.post(url, params=params)
    print(f"Reel container status: {response.status_code} {response.text}")
    if response.status_code != 200:
        raise Exception(f"Failed to create Reel container: {response.text}")
    media_id = response.json()['id']

    wait_for_media_ready(media_id, PAGE_ACCESS_TOKEN, timeout=300, poll_interval=5)

    url = f"https://graph.facebook.com/v22.0/{BUSINESS_ACCOUNT_ID}/media_publish"
    response = requests.post(url, params={'access_token': PAGE_ACCESS_TOKEN, 'creation_id': media_id})
    print(f"Reel publish status: {response.status_code} {response.text}")
    if response.status_code != 200:
        raise Exception(f"Failed to publish Reel: {response.text}")

    print(f"Instagram Reel published! Post ID: {response.json()['id']}")


def exec_facebook_post(image_url, caption):
    print("Executing Facebook Page post...")
    url = f"https://graph.facebook.com/v22.0/{FACEBOOK_PAGE_ID}/photos"
    params = {'access_token': FACEBOOK_PAGE_ACCESS_TOKEN, 'url': image_url, 'message': caption}
    response = requests.post(url, params=params)
    print(f"Facebook post status: {response.status_code} {response.text}")
    if response.status_code != 200:
        raise Exception(f"Failed to post to Facebook Page: {response.text}")
    post_id = response.json().get('id', '')
    print(f"Successfully posted to Facebook Page. Post ID: {post_id}")
    return post_id


def exec_threads_post(image_url, text=''):
    print("Executing Threads post...")
    if len(text) > 500:
        text = text[:500]

    url = f"https://graph.threads.net/v1.0/{THREADS_USER_ID}/threads"
    params = {'access_token': THREADS_API_TOKEN, 'media_type': 'IMAGE', 'image_url': image_url}
    if text:
        params['text'] = text
    response = requests.post(url, params=params)
    print(f"Threads container status: {response.status_code} {response.text}")
    if response.status_code != 200:
        raise Exception(f"Failed to create media container on Threads: {response.text}")
    container_id = response.json()['id']

    if not wait_for_threads_media_ready(container_id, THREADS_API_TOKEN):
        raise Exception("Media container was not ready in time.")

    url = f"https://graph.threads.net/v1.0/{THREADS_USER_ID}/threads_publish"
    params = {'access_token': THREADS_API_TOKEN, 'creation_id': container_id}
    response = requests.post(url, params=params)
    print(f"Threads publish status: {response.status_code} {response.text}")
    if response.status_code != 200:
        raise Exception(f"Failed to publish post to Threads: {response.text}")
    print(f"Successfully published Threads post with ID: {response.json()['id']}")


def remove_img_file(image_path):
    if os.path.exists(image_path):
        os.remove(image_path)
        print(f"{image_path} has been deleted.")
    else:
        print(f"{image_path} does not exist.")


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port)
