"""
News Satire Bot - ニュースベース風刺画生成モジュール
"""

import os
import random
import time
import json
import re
import requests
from datetime import datetime, timedelta
from io import BytesIO

# 環境変数
NEWSAPI_KEY = os.environ.get('NEWSAPI_KEY', '')
TEST_MODE = os.environ.get('TEST_MODE', 'true').lower() == 'true'

# カテゴリリスト（政治を除外）
NEWS_CATEGORIES = ['business', 'technology', 'entertainment', 'science', 'health']

# 政治的なキーワード（記事除外用）
POLITICAL_KEYWORDS = [
    'president', 'congress', 'senate', 'election', 'campaign', 'vote', 'voting',
    'democrat', 'republican', 'political', 'politics', 'government', 'parliament',
    'minister', 'legislation', 'bill', 'law', 'policy', 'white house', 'capitol'
]

# フォールバックニュース（APIエラー時に使用）
FALLBACK_NEWS = {
    'title': 'New Technology Promises to Change Everything',
    'description': 'Another groundbreaking technology announced that will revolutionize daily life, according to company press release.',
    'url': 'https://example.com',
    'category': 'technology'
}


def fetch_news_from_newsapi(category=None, days_ago=1):
    """
    NewsAPI.org から指定カテゴリの昨日のトップニュースを取得

    Args:
        category: ニュースカテゴリ ('business', 'technology', etc.)
                 None の場合は全ジャンルから最も注目度が高いニュースを取得（政治除く）
        days_ago: 何日前のニュースを取得するか（デフォルト: 1）

    Returns:
        dict: {'title': str, 'description': str, 'url': str, 'category': str}

    Raises:
        Exception: APIキー未設定時
    """
    if not NEWSAPI_KEY:
        raise Exception("NEWSAPI_KEY environment variable is not set")

    # 対象日付を計算
    target_date = datetime.now() - timedelta(days=days_ago)
    date_string = target_date.strftime('%Y-%m-%d')

    # NewsAPI リクエスト
    url = 'https://newsapi.org/v2/top-headlines'
    params = {
        'country': 'us',
        'from': date_string,
        'pageSize': 20,  # 政治記事をスキップするため多めに取得
        'apiKey': NEWSAPI_KEY
    }

    # カテゴリが指定されている場合のみ追加
    if category:
        params['category'] = category
        print(f"[NewsAPI] Fetching news: category={category}, date={date_string}")
    else:
        print(f"[NewsAPI] Fetching top news from all categories: date={date_string}")

    try:
        response = requests.get(url, params=params, timeout=10)

        if response.status_code == 429:
            print("[NewsAPI] Rate limit exceeded. Using fallback news.")
            return FALLBACK_NEWS

        if response.status_code != 200:
            raise Exception(f"NewsAPI error: {response.text}")

        data = response.json()
        articles = data.get('articles', [])

        if not articles:
            print("[NewsAPI] No articles found. Using fallback news.")
            return FALLBACK_NEWS

        # 政治的なキーワードを含まない記事を選択
        selected = None
        for article in articles:
            title = article.get('title', '').lower()
            description = article.get('description', '').lower()

            # 政治的なキーワードチェック
            is_political = any(keyword in title or keyword in description
                             for keyword in POLITICAL_KEYWORDS)

            if not is_political:
                selected = article
                break

        # すべて政治記事だった場合はフォールバック
        if selected is None:
            print("[NewsAPI] All articles are political. Using fallback news.")
            return FALLBACK_NEWS

        # カテゴリを推定（指定がない場合）
        detected_category = category if category else 'general'

        news_article = {
            'title': selected.get('title', ''),
            'description': selected.get('description', ''),
            'url': selected.get('url', ''),
            'category': detected_category
        }

        print(f"[NewsAPI] Selected: {news_article['title'][:60]}...")
        return news_article

    except Exception as e:
        print(f"[NewsAPI] Error: {e}. Using fallback news.")
        return FALLBACK_NEWS


def generate_satire_concept_with_gpt4o(news_article, openai_client):
    """
    GPT-4o で風刺コンセプトとImagen用プロンプトを生成

    Args:
        news_article: fetch_news_from_newsapi の戻り値
        openai_client: OpenAI クライアント（main.pyから渡される）

    Returns:
        tuple: (imagen_prompt, satire_concept)
    """
    prompt = f"""You are a creative satirical illustrator specializing in 19th-century editorial cartoons.

NEWS ARTICLE:
Title: {news_article['title']}
Description: {news_article['description']}
Category: {news_article['category']}

TASK:
1. Identify the main contradiction, irony, or absurdity in this news
2. Create a visual metaphor to represent this satirically
3. Generate an Imagen prompt for a modern political cartoon

CONSTRAINTS:
- Art style: "Modern newspaper comic strip style, bold outlines, flat colors with halftone dot patterns, pop art aesthetic, expressive cartoon characters"
- Avoid explicit political figures (focus on concepts/symbols)
- Use symbolic imagery (scales for justice, clocks for time, animals for behaviors)
- Keep it thought-provoking but family-friendly

OUTPUT FORMAT (JSON):
{{
  "satire_concept": "Brief explanation of the satirical angle",
  "visual_metaphor": "Description of the symbolic scene",
  "imagen_prompt": "Complete Imagen prompt in English"
}}

Example for tech layoff news:
{{
  "satire_concept": "The contradiction between AI efficiency promises and human job losses",
  "visual_metaphor": "A mechanical arm labeled 'AI' sweeping workers off a factory floor while executives count coins",
  "imagen_prompt": "Modern newspaper comic strip style, bold outlines, flat colors with halftone dot patterns, pop art aesthetic: A large mechanical arm with 'AI' engraved on it sweeps factory workers off the floor like dust, while well-dressed businessmen in top hats count gold coins in the background, dramatic lighting, expressive cartoon characters, symbolic imagery"
}}"""

    print("[GPT-4o] Generating satire concept...")

    try:
        response = openai_client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.8,
            max_tokens=500
        )

        ai_response = response.choices[0].message.content

        # JSONパース
        try:
            result = json.loads(ai_response)
            imagen_prompt = result.get('imagen_prompt', '')
            satire_concept = result.get('satire_concept', '')

            if not imagen_prompt:
                raise ValueError("imagen_prompt is empty")

            print(f"[GPT-4o] Concept: {satire_concept}")
            return imagen_prompt, satire_concept

        except (json.JSONDecodeError, ValueError) as e:
            # JSONパース失敗時は正規表現で抽出
            print(f"[GPT-4o] JSON parse failed: {e}. Trying regex extraction...")
            match = re.search(r'"imagen_prompt":\s*"([^"]+)"', ai_response)

            if match:
                imagen_prompt = match.group(1)
                print(f"[GPT-4o] Extracted via regex: {imagen_prompt[:80]}...")
                return imagen_prompt, "Satire concept extraction failed"
            else:
                raise Exception("Could not extract Imagen prompt from GPT-4o response")

    except Exception as e:
        print(f"[GPT-4o] Error: {e}")
        # フォールバック: 汎用風刺プロンプト
        fallback_prompt = f"Modern newspaper comic strip style, bold outlines, flat colors with halftone dot patterns, pop art aesthetic: {news_article['title']}, symbolic imagery, expressive cartoon characters"
        print(f"[GPT-4o] Using fallback prompt")
        return fallback_prompt, "Fallback concept due to error"


def generate_satire_caption_with_gemini(image_path, news_article, satire_concept, gemini_chat_func):
    """
    Gemini で風刺画用キャプション生成

    Args:
        image_path: 生成した画像のパス
        news_article: 元のニュース記事
        satire_concept: GPT-4oが生成した風刺コンセプト
        gemini_chat_func: Gemini chat関数（main.pyから渡される）

    Returns:
        str: Instagram用キャプション
    """
    prompt = f"""Analyze this satirical illustration based on the following news:

NEWS: {news_article['title']}
DESCRIPTION: {news_article['description']}
SATIRE CONCEPT: {satire_concept}

Create an Instagram caption IN ENGLISH with this EXACT structure:

1. TITLE (1 line, catchy title summarizing the satire)
2. ENGAGEMENT (2-3 lines):
   - Ask viewers a simple question about their feelings
   - Encourage them to comment with one-word reactions: "Excited", "Worried", "Hopeful", "Skeptical", etc.
3. NEWS SUMMARY (2-3 lines, factual summary of the news)
4. HASHTAGS: #satire #editorialcartoon #news{news_article['category']} #socialsatire

IMPORTANT:
- Write ENTIRELY in English
- Use simple, accessible language
- Make the question easy to answer with one word or short phrase
- News summary should be objective and informative
- Total length: 250-350 characters

Example format:
[TITLE]
AI vs. Human Jobs: The New Reality

[ENGAGEMENT]
How does this satirical illustration make you feel?
Drop a one-word comment: "Excited", "Worried", "Mixed", etc.

[NEWS SUMMARY]
Tech giants announce massive layoffs while celebrating record AI-driven profits. Thousands lose jobs as automation efficiency reaches new heights.

#satire #editorialcartoon #newstech #socialsatire
"""

    print("[Gemini] Generating caption...")
    caption = gemini_chat_func(image_path, prompt)

    # キャプションが長すぎる場合は切り詰め
    if len(caption) > 500:
        caption = caption[:497] + "..."

    print(f"[Gemini] Caption: {caption[:80]}...")
    return caption


def generate_satire_image(
    category=None,
    openai_client=None,
    gemini_chat_func=None,
    genai_client=None,
    save_to_tmp=True
):
    """
    ニュース風刺画を生成する統合関数

    Args:
        category: ニュースカテゴリ（Noneでランダム）
        openai_client: OpenAI クライアント
        gemini_chat_func: Gemini chat関数
        genai_client: Google GenAI クライアント（Imagen用）
        save_to_tmp: Trueの場合 /app/satire_test_images に保存

    Returns:
        dict: {
            'image_path': str,
            'caption': str,
            'news': dict,
            'satire_concept': str,
            'imagen_prompt': str
        }
    """
    # 1. ニュース取得
    print("\n=== Step 1: Fetching News ===")
    news_article = fetch_news_from_newsapi(category=category)

    # 2. 風刺コンセプト生成
    print("\n=== Step 2: Generating Satire Concept ===")
    imagen_prompt, satire_concept = generate_satire_concept_with_gpt4o(
        news_article, openai_client
    )
    print(f"[GPT-4o] Imagen prompt: {imagen_prompt[:100]}...")

    # 3. Imagen で画像生成
    print("\n=== Step 3: Generating Image with Imagen ===")
    result = genai_client.models.generate_images(
        model="models/imagen-4.0-generate-001",
        prompt=imagen_prompt,
        config=dict(
            number_of_images=1,
            output_mime_type="image/jpeg",
            person_generation="ALLOW_ADULT",
            aspect_ratio="1:1",
        ),
    )
    print("[Imagen] Image generation complete.")

    if not result.generated_images:
        raise Exception("No images generated by Imagen")

    image_data = result.generated_images[0].image.image_bytes

    # 画像を PIL Image として読み込み
    from PIL import Image
    img = Image.open(BytesIO(image_data))

    # 画像保存
    current_time = int(time.time())

    if save_to_tmp:
        # Dockerコンテナの場合、/app がホスト側にマウントされている
        # ホスト側からアクセスできるようにするため、/app 配下に保存
        save_dir = '/app/satire_test_images'
        os.makedirs(save_dir, exist_ok=True)
        image_path = f"{save_dir}/satire_test_{current_time}.jpg"
    else:
        # 本番モード用（GCSアップロード前の一時保存）
        business_account_id = os.environ.get('INSTA_BUSINESS_ACCOUNT_ID', 'unknown')
        image_path = f"/tmp/satire_{business_account_id}_{current_time}.jpg"

    img.save(image_path)
    print(f"[Imagen] Image saved: {image_path}")
    if save_to_tmp:
        print(f"[Imagen] Host path: ./satire_test_images/satire_test_{current_time}.jpg")

    # 4. キャプション生成
    print("\n=== Step 4: Generating Caption ===")
    caption = generate_satire_caption_with_gemini(
        image_path, news_article, satire_concept, gemini_chat_func
    )

    print("\n=== Satire Generation Complete ===\n")

    return {
        'image_path': image_path,
        'caption': caption,
        'news': news_article,
        'satire_concept': satire_concept,
        'imagen_prompt': imagen_prompt
    }
