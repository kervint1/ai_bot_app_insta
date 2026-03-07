import os
import time
import base64
import requests
from flask import Flask, request
import news_satire
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
