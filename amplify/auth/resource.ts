import { defineAuth } from "@aws-amplify/backend";

/**
 * 認証: Cognito User Pool (招待制)
 *
 * - セルフサインアップ無効 (管理者がCognitoコンソール or CLIでユーザー作成)
 * - ログイン: メールアドレス + パスワード
 * - 一時パスワードがメールで届き、初回ログイン時に変更
 */
export const auth = defineAuth({
  loginWith: {
    email: true,
  },
});
