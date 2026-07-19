"""boto3クライアント生成ヘルパー。

Lambda環境ではデフォルトセッション(IAMロール)を使い、
ローカル開発ではsessionを外部から注入する。
"""

import boto3

from eden import config


def get_bedrock_client(
    region: str | None = None,
    session: boto3.Session | None = None,
):
    """Bedrock Runtime クライアントを返す。"""
    s = session or boto3.Session()
    return s.client("bedrock-runtime", region_name=region or config.BBOX_REGION)


def get_bedrock_embed_client(
    region: str | None = None,
    session: boto3.Session | None = None,
):
    """Bedrock Runtime クライアント (Embeddings用、us-east-1デフォルト)。"""
    s = session or boto3.Session()
    return s.client("bedrock-runtime", region_name=region or config.EMBED_REGION)


def get_bedrock_text_client(
    region: str | None = None,
    session: boto3.Session | None = None,
):
    """Bedrock Runtime クライアント (テキスト抽出用、東京デフォルト)。"""
    s = session or boto3.Session()
    return s.client("bedrock-runtime", region_name=region or config.TEXT_REGION)


def get_s3vectors_client(
    region: str | None = None,
    session: boto3.Session | None = None,
):
    """S3 Vectors クライアントを返す。"""
    s = session or boto3.Session()
    return s.client("s3vectors", region_name=region or config.VECTOR_REGION)


def get_rekognition_client(
    region: str | None = None,
    session: boto3.Session | None = None,
):
    """Rekognition クライアントを返す。"""
    s = session or boto3.Session()
    return s.client("rekognition", region_name=region or config.REKOGNITION_REGION)
