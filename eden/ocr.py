"""文字抽出: VLM(Nova Lite)によるOCR + NFKC正規化・トークン化。"""

import re
import unicodedata

from PIL import Image

from eden import config
from eden.clients import get_bedrock_text_client
from eden.image_util import jpeg_bytes

TEXT_PROMPT = """This image shows a physical object.
If there is any text, logo, or printed/engraved characters visible on the object, transcribe them exactly as written.
If there is no visible text, output exactly: NONE
Output ONLY the transcribed text (or NONE), no explanation, no extra formatting."""


def extract_text(
    img: Image.Image,
    *,
    client=None,
    model_id: str | None = None,
) -> str:
    """VLMで物体表面の文字を抽出する。

    文字が検出されなかった場合は空文字列を返す。

    Args:
        img: 入力画像 (クロップ済み推奨)
        client: bedrock-runtime クライアント (Noneならデフォルト生成)
        model_id: 使用するモデルID (Noneなら config.TEXT_MODEL_ID)

    Returns:
        抽出されたテキスト (文字無しの場合は空文字列)
    """
    client = client or get_bedrock_text_client()
    model_id = model_id or config.TEXT_MODEL_ID

    resp = client.converse(
        modelId=model_id,
        messages=[
            {
                "role": "user",
                "content": [
                    {"image": {"format": "jpeg", "source": {"bytes": jpeg_bytes(img)}}},
                    {"text": TEXT_PROMPT},
                ],
            }
        ],
        inferenceConfig={"temperature": 0.0, "maxTokens": 200},
    )
    text = resp["output"]["message"]["content"][0]["text"].strip()
    if text.upper() == "NONE":
        return ""
    return text


def normalize_text(text: str) -> list[str]:
    """全角半角統一・小文字化・記号除去してトークン化する。

    Args:
        text: 生テキスト (extract_textの出力)

    Returns:
        ソート済みユニークトークンのリスト (空文字列入力なら空リスト)
    """
    if not text:
        return []
    text = unicodedata.normalize("NFKC", text)
    text = text.lower()
    # 英数字・ひらがな・カタカナ・漢字以外は区切りとして除去
    text = re.sub(r"[^\w\u3040-\u309f\u30a0-\u30ff\u4e00-\u9fff]+", " ", text)
    tokens = sorted(set(t for t in text.split() if t))
    return tokens
