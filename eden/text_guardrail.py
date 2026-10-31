"""テキストガードレール: Bedrock Guardrails ApplyGuardrail API を使用。

Standard Tier を使用し、日本語を含む多言語のコンテンツフィルタリングに対応。
モデル呼び出し不要のスタンドアロン API で、コメント保存前のバリデーションに使用する。
"""

import logging
from dataclasses import dataclass

import boto3

from eden import config

logger = logging.getLogger(__name__)


@dataclass
class TextGuardrailResult:
    """テキストガードレールの判定結果。"""
    passed: bool
    action: str  # "NONE" or "GUARDRAIL_INTERVENED"
    reason: str | None = None
    policy: str | None = None
    details: list | None = None


def check_text_guardrail(
    text: str,
    *,
    client=None,
    source: str = "INPUT",
) -> TextGuardrailResult:
    """ApplyGuardrail API でテキストをチェックする。

    Args:
        text: チェック対象のテキスト
        client: bedrock-runtime クライアント (None なら自動生成)
        source: "INPUT" or "OUTPUT" (ユーザー入力なので通常 INPUT)

    Returns:
        TextGuardrailResult: passed=True ならクリーン、False なら NG
    """
    if not config.TEXT_GUARDRAIL_ID:
        # ガードレール未設定時はスキップ (開発環境用)
        logger.warning("[text-guardrail] GUARDRAIL_ID 未設定のためスキップ")
        return TextGuardrailResult(passed=True, action="NONE")

    if not text or not text.strip():
        return TextGuardrailResult(passed=True, action="NONE")

    if client is None:
        client = boto3.client("bedrock-runtime", region_name=config.TEXT_GUARDRAIL_REGION)

    logger.info("[text-guardrail] チェック開始: text='%s' (len=%d)", text[:30], len(text))

    try:
        response = client.apply_guardrail(
            guardrailIdentifier=config.TEXT_GUARDRAIL_ID,
            guardrailVersion=config.TEXT_GUARDRAIL_VERSION,
            source=source,
            content=[{"text": {"text": text}}],
        )
    except Exception as e:
        # ガードレール呼び出し失敗時はパスさせる (可用性優先)
        logger.error("[text-guardrail] API呼び出し失敗: %s", e)
        return TextGuardrailResult(passed=True, action="NONE", reason="guardrail_unavailable")

    action = response.get("action", "NONE")

    if action == "NONE":
        logger.info("[text-guardrail] クリーン: text='%s'", text[:30])
        return TextGuardrailResult(passed=True, action=action)

    # GUARDRAIL_INTERVENED: NG
    assessments = response.get("assessments", [])
    reason, policy, details = _extract_violation_info(assessments)

    logger.warning(
        "[text-guardrail] NG検出: text='%s', policy=%s, reason=%s",
        text[:30], policy, reason,
    )

    return TextGuardrailResult(
        passed=False,
        action=action,
        reason=reason,
        policy=policy,
        details=details,
    )


def _extract_violation_info(assessments: list) -> tuple[str | None, str | None, list]:
    """assessments から抵触ポリシーと理由を抽出する。"""
    all_details = []
    first_reason = None
    first_policy = None

    for assessment in assessments:
        # contentPolicy (HATE, INSULTS, SEXUAL, VIOLENCE, MISCONDUCT)
        content_policy = assessment.get("contentPolicy", {})
        for filter_item in content_policy.get("filters", []):
            detail = {
                "policy": "contentPolicy",
                "type": filter_item.get("type"),
                "confidence": filter_item.get("confidence"),
                "action": filter_item.get("action"),
            }
            all_details.append(detail)
            if first_reason is None and filter_item.get("action") == "BLOCKED":
                first_reason = filter_item.get("type")
                first_policy = "contentPolicy"

        # wordPolicy (カスタム禁止語)
        word_policy = assessment.get("wordPolicy", {})
        for word_item in word_policy.get("customWords", []):
            detail = {
                "policy": "wordPolicy",
                "match": word_item.get("match"),
                "action": word_item.get("action"),
            }
            all_details.append(detail)
            if first_reason is None:
                first_reason = f"blocked_word: {word_item.get('match')}"
                first_policy = "wordPolicy"

        # topicPolicy (拒否トピック)
        topic_policy = assessment.get("topicPolicy", {})
        for topic_item in topic_policy.get("topics", []):
            detail = {
                "policy": "topicPolicy",
                "name": topic_item.get("name"),
                "action": topic_item.get("action"),
            }
            all_details.append(detail)
            if first_reason is None:
                first_reason = f"denied_topic: {topic_item.get('name')}"
                first_policy = "topicPolicy"

    return first_reason, first_policy, all_details
