# モンスト Discord 自動投稿（無料構成）

## 初期設定
1. Discordで投稿したいチャンネルを開き「チャンネルの編集」→「連携サービス」→「ウェブフック」→「新しいウェブフック」→URLをコピー。URLは他人に見せないでください。
2. GitHubで **Private** リポジトリを新規作成。
3. このZIPを解凍し、`bot.py`、`requirements.txt`、`.github/workflows/monst.yml` をリポジトリにアップロード。`.github` は隠しフォルダなので注意。
4. リポジトリの **Settings → Secrets and variables → Actions → New repository secret** で、名前を `DISCORD_WEBHOOK_URL`、値をDiscordのWebhook URLに設定。
5. **Settings → Actions → General → Workflow permissions** で `Read and write permissions` を有効にする（リポジトリの権限設定により変更不可の場合あり）。
6. **Actions → モンストDiscord自動投稿 → Run workflow** で `morning` を選択してテスト。
7. `check` を一度手動実行して新着の基準を記録。以降は定期実行。

## できること・制限
- 公式サイト `https://www.monster-strike.com/news/` の最新記事タイトルとURLを投稿します。
- 日本時間の毎朝6時台に朝刊、約30分おきに新着チェック。GitHub Actionsの都合で時刻は遅れる場合があります。
- 速報は**掲載記事の新着検知**であり、即時プッシュではありません。
- 「降臨スケジュール」の**正確な時刻一覧**は取得しません。公式ニュースのクエスト関連記事のみです。
- 「書庫キャンペーン」は公式ニュースに掲載された関連タイトルのみです。ゲーム内日替わり対象一覧は未対応です。
- 初回チェック時は過去記事を速報として大量投稿しません。
- 公式サイトのHTML構造が変更されると取得できなくなる可能性があります。
- GitHub無料枠・実行ポリシーの変更、定期実行の自動停止、実行遅延に注意してください。
- Webhook URLはGitHub Secrets以外に書かないでください。

## トラブル時
- Actionsのログで `ERROR` を確認。
- `公式サイトの解析結果が0件` の場合、公式サイトのHTML構造に合わせて `fetch_articles()` の抽出条件を修正してください。
- 投稿されない場合はWebhookのURL、Actionsの有効化、書き込み権限を確認してください。
