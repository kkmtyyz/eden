# Requirements Document

## Introduction

S3 Vectors によるベクトル検索を DynamoDB のネイティブ Vector Search (2026-08-05 GA) に完全置換する。ベンチマーク検証 (P50 -26%, 精度100%一致) に基づき、CDKインフラ・ベクトル書き込み・検索・リセット処理を DynamoDB に切り替え、S3 Vectors リソースを撤去する。フロントエンド・判定ロジック・BBox検出・OCR・埋め込み処理には変更を加えない。

## Glossary

- **VectorsTable**: DynamoDB テーブル。ベクトルデータとメタデータを格納する。PK は vector_id (String 型)
- **vector_id**: VectorsTable のパーティションキー。形式は `{object_id}##{source_file}##{rotation}` (例: `aws_mug##uploads/frame_001.jpg##90`)。object_id は物体識別子、source_file は元画像ファイルパス、rotation は回転角度 (0, 90, 180, 270)。セパレータ `##` で3要素を連結する
- **VectorIndex**: VectorsTable に設定するベクトルインデックス。1024次元、COSINE距離関数、全属性射影
- **SearchVectors_API**: DynamoDB のベクトル近傍検索 API。SearchVector (L of N 形式)、TopK を指定して類似ベクトルを取得する
- **Score**: SearchVectors_API のレスポンスに含まれるコサイン距離値。0 (同一) 〜 2 (正反対)。低いほど類似度が高い
- **CDK_Stack**: Amplify カスタムスタック (`amplify/custom/api-stack.ts`)。インフラリソースを定義する
- **register_worker**: SQSトリガーの Lambda 関数。物体登録処理として画像の埋め込みベクトルを計算しストレージに書き込む
- **search_Lambda**: 検索 API を処理する Lambda 関数。ベクトル検索を実行し3値判定を返す
- **reset_Lambda**: データリセット用 Lambda 関数。ベクトルデータを全削除する
- **Objects_Table**: 既存の DynamoDB テーブル。物体メタデータ (サムネイル、コメント) を格納する。本移行では変更しない
- **BatchWriteItem**: DynamoDB のバッチ書き込み API。1回のリクエストで最大25アイテムを書き込む
- **CfnTable**: AWS CDK の L1 コンストラクト。CloudFormation の AWS::DynamoDB::Table を直接操作する
- **sim**: 類似度スコア。`1 - Score` で算出する。既存の判定ロジックへの入力値

## Requirements

### Requirement 1: VectorsTable の CDK 定義

**User Story:** As a 開発者, I want CDK スタックで DynamoDB VectorsTable とベクトルインデックスを定義したい, so that インフラをコードで管理しデプロイを自動化できる

#### Acceptance Criteria

1. THE CDK_Stack SHALL CfnTable (L1 コンストラクト) を使用して VectorsTable を定義する
2. THE VectorsTable SHALL パーティションキーとして vector_id (String 型) を持つ
3. THE VectorsTable SHALL PAY_PER_REQUEST 課金モードで作成される
4. THE VectorsTable SHALL VectorIndex を持ち、IndexName=eden-vector-index、Dimensions=1024、DistanceFunction=COSINE、VectorAttribute={AttributeName: "vector"}、Projection={ProjectionType: ALL} で構成される
5. THE CDK_Stack SHALL VectorsTable のテーブル名を環境変数 VECTORS_TABLE として register_worker、search_Lambda、reset_Lambda に渡す

### Requirement 2: S3 Vectors リソースの撤去

**User Story:** As a 開発者, I want CDK スタックから S3 Vectors リソースを削除したい, so that 不要なリソースを廃止しコストと複雑性を削減できる

#### Acceptance Criteria

1. THE CDK_Stack SHALL S3 Vectors バケット (eden-vec-{hash}) の定義を含まない
2. THE CDK_Stack SHALL S3 Vectors インデックス (eden-index) の定義を含まない
3. THE CDK_Stack SHALL Lambda 関数に s3vectors 関連の IAM 権限を付与しない
4. THE CDK_Stack SHALL Lambda 関数の環境変数から VECTOR_BUCKET および VECTOR_INDEX を含まない

### Requirement 3: ベクトル書き込みの DynamoDB 移行

**User Story:** As a システム, I want 物体登録時のベクトル書き込みを DynamoDB に行いたい, so that ベクトルとメタデータを同一テーブルで管理しデータ同期問題を解消できる

#### Acceptance Criteria

1. WHEN 物体登録処理が埋め込みベクトルを生成した場合, THE register_worker SHALL VectorsTable に BatchWriteItem でベクトルアイテムを書き込む
2. THE register_worker SHALL 各ベクトルアイテムを以下の属性で構成する: vector_id (S型, "{object_id}##{source_file}##{rotation}" 形式), object_id (S型), source_file (S型), rotation (N型), vector (L型, List of N)
3. THE register_worker SHALL 25件以下のアイテムを1回の BatchWriteItem リクエストで送信する
4. WHEN text_tokens が非空リストの場合, THE register_worker SHALL アイテムに text_tokens (SS型) を含める
5. WHEN text_tokens が空リストまたは None の場合, THE register_worker SHALL アイテムに text_tokens 属性を含めない
6. IF BatchWriteItem で UnprocessedItems が返却された場合, THEN THE register_worker SHALL 未処理アイテムを再送する
7. THE register_worker SHALL S3 Vectors クライアントへの書き込み処理を含まない

### Requirement 4: ベクトル検索の DynamoDB 移行

**User Story:** As a システム, I want 検索処理で DynamoDB SearchVectors API を使用したい, so that レイテンシを改善しインフラ構成を簡素化できる

#### Acceptance Criteria

1. WHEN ベクトル検索が実行される場合, THE search_Lambda SHALL DynamoDB の SearchVectors API を呼び出す
2. THE search_Lambda SHALL SearchVector パラメータを [{"N": str(v)} for v in vector] 形式 (AttributeValue の L of N) で構築する
3. THE search_Lambda SHALL TopK パラメータに 20 を指定する
4. THE search_Lambda SHALL TableName に環境変数 VECTORS_TABLE の値を使用する
5. THE search_Lambda SHALL IndexName に "eden-vector-index" を指定する
6. WHEN SearchResults を受信した場合, THE search_Lambda SHALL 各結果の Score を cosine distance としてそのまま使用し sim = 1 - Score で類似度を算出する
7. WHEN SearchResults を受信した場合, THE search_Lambda SHALL 各結果の Item から object_id (S型) と text_tokens (SS型) をメタデータとして抽出する
8. THE search_Lambda SHALL 正規化したヒットリストを既存の compute_scores および judge_verdict に渡す
9. THE search_Lambda SHALL S3 Vectors クライアントへのクエリ処理を含まない

### Requirement 5: eden/search.py の query_vectors 関数書き換え

**User Story:** As a 開発者, I want eden/search.py の query_vectors 関数を DynamoDB SearchVectors を使う実装に書き換えたい, so that 検索モジュールが DynamoDB ベースで動作する

#### Acceptance Criteria

1. THE query_vectors 関数 SHALL DynamoDB クライアントの search_vectors メソッドを呼び出す
2. THE query_vectors 関数 SHALL SearchVector を [{"N": str(v)} for v in vector] 形式で渡す
3. THE query_vectors 関数 SHALL レスポンスの SearchResults を既存の戻り値形式 (distance と metadata を含む辞書のリスト) に正規化する
4. THE query_vectors 関数 SHALL Score の値をそのまま distance フィールドに設定する
5. THE query_vectors 関数 SHALL 各 Item から object_id と text_tokens を抽出し metadata フィールドに格納する
6. THE query_vectors 関数 SHALL テーブル名を config.VECTORS_TABLE から取得する
7. THE query_vectors 関数 SHALL S3 Vectors クライアントを使用しない

### Requirement 6: config.py の設定変更

**User Story:** As a 開発者, I want config.py をDynamoDB ベースの設定に更新したい, so that 不要な S3 Vectors 設定を除去し新しいテーブル名設定を追加できる

#### Acceptance Criteria

1. THE config モジュール SHALL 環境変数 EDEN_VECTORS_TABLE からテーブル名を読み込む VECTORS_TABLE 設定を提供する
2. THE config モジュール SHALL VECTOR_BUCKET 設定を含まない
3. THE config モジュール SHALL VECTOR_INDEX 設定を含まない
4. THE config モジュール SHALL VECTOR_REGION 設定を保持する (DynamoDB のリージョン指定に使用するため)

### Requirement 7: データリセット処理の DynamoDB 移行

**User Story:** As a 運用担当者, I want リセット処理で DynamoDB VectorsTable のデータを全削除したい, so that テスト環境のリセットを S3 Vectors なしで実行できる

#### Acceptance Criteria

1. WHEN リセット処理が実行される場合, THE reset_Lambda SHALL VectorsTable の全アイテムをスキャンして削除する
2. THE reset_Lambda SHALL BatchWriteItem (DeleteRequest) を使用してアイテムを25件ずつ削除する
3. THE reset_Lambda SHALL S3 Vectors インデックスの削除処理を含まない
4. THE reset_Lambda SHALL Objects_Table と Jobs テーブルの削除処理を従来通り実行する
5. WHEN 確認パラメータ (confirm: true) が渡されない場合, THE reset_Lambda SHALL リセット処理を実行しない

### Requirement 8: IAM 権限の更新

**User Story:** As a 開発者, I want Lambda 関数の IAM 権限を DynamoDB ベースに更新したい, so that 最小権限の原則に従い必要な操作のみを許可する

#### Acceptance Criteria

1. THE CDK_Stack SHALL register_worker に VectorsTable への dynamodb:BatchWriteItem 権限を付与する
2. THE CDK_Stack SHALL search_Lambda に VectorsTable への dynamodb:SearchVectors 権限を付与する
3. THE CDK_Stack SHALL reset_Lambda に VectorsTable への dynamodb:Scan および dynamodb:BatchWriteItem 権限を付与する
4. THE CDK_Stack SHALL Lambda 関数に s3vectors:QueryVectors、s3vectors:PutVectors、s3vectors:DeleteVectors 等の S3 Vectors 権限を付与しない

### Requirement 9: ゴーストベクトル防御の維持

**User Story:** As a システム, I want DynamoDB 移行後もゴーストベクトル防御を維持したい, so that 削除済み物体が検索結果に表示されることを防止できる

#### Acceptance Criteria

1. WHEN 検索結果の top-1 object_id が Objects_Table に存在しない場合, THE search_Lambda SHALL 判定を red に降格する
2. WHEN DynamoDB VectorsTable から検索結果が返されない場合, THE search_Lambda SHALL verdict を red、reason を "no_hits" として返す

### Requirement 10: 既存動作の保全

**User Story:** As a ユーザー, I want 移行後も検索・登録の動作が変わらないことを保証したい, so that 既存機能に影響なく性能改善の恩恵を受けられる

#### Acceptance Criteria

1. THE search_Lambda SHALL 移行前と同一の3値判定 (green/yellow/red) を返す
2. THE search_Lambda SHALL compute_scores 関数 (文字一致ブースト +0.05、文字不一致ペナルティ -0.05) を変更なく使用する
3. THE search_Lambda SHALL judge_verdict 関数 (GREEN_SIM=0.80, GREEN_MARGIN=0.10, YELLOW_SIM=0.72) を変更なく使用する
4. THE register_worker SHALL 4方向回転 (0, 90, 180, 270) の埋め込みベクトルを生成する処理を変更なく実行する
5. THE register_worker SHALL OCR テキスト抽出と text_tokens 正規化の処理を変更なく実行する
6. FOR ALL 有効なベクトルアイテム, VectorsTable に書き込んだ後に SearchVectors で検索した場合, 同じ object_id の結果が返される (ラウンドトリップ特性)
