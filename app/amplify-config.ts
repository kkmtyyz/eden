import { Amplify } from "aws-amplify";

/**
 * Amplify の設定を行う。
 * amplify_outputs.json から自動生成された設定を読み込む。
 */
export function configureAmplify() {
  try {
    // amplify_outputs.json は npx ampx sandbox 実行時に自動生成される
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    const outputs = require("../amplify_outputs.json");
    Amplify.configure(outputs);
  } catch {
    // 開発初期は amplify_outputs.json が存在しない場合がある
    console.warn(
      "amplify_outputs.json not found. Run 'npx ampx sandbox' to generate it."
    );
  }
}

/**
 * カスタム API エンドポイントを取得する。
 * amplify_outputs.json の custom.apiEndpoint に格納されている。
 */
export function getApiEndpoint(): string | null {
  try {
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    const outputs = require("../amplify_outputs.json");
    return outputs?.custom?.apiEndpoint ?? null;
  } catch {
    return null;
  }
}
