"""検索テストスクリプト。eden.search を利用。"""

import sys
from pprint import pprint

import boto3

from eden.clients import (
    get_bedrock_client,
    get_bedrock_embed_client,
    get_bedrock_text_client,
    get_s3vectors_client,
)
from eden.image_util import load_image
from eden.search import search_image

PROFILE = "kkmtyyz_mfa"

session = boto3.Session(profile_name=PROFILE)
bbox_client = get_bedrock_client(session=session)
embed_client = get_bedrock_embed_client(session=session)
text_client = get_bedrock_text_client(session=session)
s3v_client = get_s3vectors_client(session=session)


def search_image_file(path):
    """画像ファイルから検索(クロップ込み)"""
    img = load_image(path)
    result = search_image(
        img,
        bbox_client=bbox_client,
        embed_client=embed_client,
        s3v_client=s3v_client,
        text_client=text_client,
    )
    return result


if __name__ == "__main__":
    result = search_image_file(sys.argv[1])
    pprint(vars(result))
