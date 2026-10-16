import json, os, re, sys, time
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urljoin, urlparse
from zoneinfo import ZoneInfo
import requests
from bs4 import BeautifulSoup

BASE = 'https://www.monster-strike.com/news/'
STATE = Path('seen.json')
JST = ZoneInfo('Asia/Tokyo')
HEADERS = {'User-Agent': 'MonstDiscordNewsBot/1.0 (personal notification)'}


def category(title):
    if re.search(r'獣神化|真獣神化|アップデート|モンストニュース', title): return '🧬 獣神化・ニュース'
    if re.search(r'コラボ', title): return '🤝 コラボ'
    if re.search(r'書庫|キャンペーン|ポイント', title): return '🎁 書庫・キャンペーン'
    if re.search(r'降臨|黎絶|轟絶|超究極|クエスト', title): return '⚔️ クエスト関連'
    return '📢 イベント・その他'


def fetch_articles():
    r = requests.get(BASE, headers=HEADERS, timeout=25)
    r.raise_for_status()
    r.encoding = 'utf-8'
    soup = BeautifulSoup(r.text, 'html.parser')
    items = []
    for a in soup.select('a[href]'):
        href = urljoin(BASE, a['href'])
        path = urlparse(href).path
        if not re.search(r'/news/\d{8}[_/-]|/news/\d{4}/\d{2}/', path):
            continue
        title = ' '.join(a.stripped_strings)
        title = re.sub(r'^(CHECK\s*)+', '', title).strip()
        title = re.sub(r'\s+(イベント|コラボ|キャンペーン|アップデート|NEW|追記|追加情報)(\s|$).*$', '', title).strip()
        if not title or len(title) < 7: continue
        items.append({'url': href.split('#')[0], 'title': title[:180], 'category': category(title)})
    unique = {x['url']: x for x in items}
    if not unique:
        raise RuntimeError('公式サイトの解析結果が0件です。HTML構造の変更を確認してください。')
    return list(unique.values())[:50]


def post(webhook, title, articles, footer):
    lines = [f'**{title}**', '']
    for item in articles:
        lines.append(f"{item['category']} **{item['title']}**\n{item['url']}")
    lines.append('')
    lines.append(footer)
    text = '\n\n'.join(lines)
    # Discord content limit: 2000 chars; split at article boundaries.
    chunks, current = [], f'**{title}**\n\n'
    for item in articles:
        line = f"{item['category']} **{item['title']}**\n{item['url']}\n\n"
        if len(current) + len(line) + len(footer) > 1900:
            chunks.append(current)
            current = ''
        current += line
    chunks.append(current + '\n' + footer)
    for chunk in chunks:
        resp = requests.post(webhook, json={'content': chunk, 'allowed_mentions': {'parse': []}}, timeout=25)
        if resp.status_code == 429:
            time.sleep(float(resp.json().get('retry_after', 2)) + 1)
            resp = requests.post(webhook, json={'content': chunk, 'allowed_mentions': {'parse': []}}, timeout=25)
        resp.raise_for_status()
        time.sleep(1)


def main():
    webhook = os.environ.get('DISCORD_WEBHOOK_URL', '').strip()
    if not webhook: raise RuntimeError('DISCORD_WEBHOOK_URL が設定されていません')
    mode = os.environ.get('MODE', 'check')
    now = datetime.now(JST)
    articles = fetch_articles()
    state = json.loads(STATE.read_text(encoding='utf-8')) if STATE.exists() else {'seen': [], 'last_morning': ''}
    seen = set(state.get('seen', []))
    if mode == 'morning':
        if state.get('last_morning') == now.strftime('%Y-%m-%d'):
            print('本日の朝刊は送信済み'); return
        post(webhook, f'🌅 {now:%Y/%m/%d} モンスト朝刊', articles[:10],
             '公式サイトの最新掲載記事です。開催中・本日の降臨時刻を保証するものではありません。\n' + BASE)
        state['last_morning'] = now.strftime('%Y-%m-%d')
    elif mode == 'check':
        if not STATE.exists():
            print('初回は既存記事を登録し、速報の大量投稿を防ぎます。')
        else:
            new = [a for a in articles if a['url'] not in seen]
            if new:
                post(webhook, '🚨 モンスト公式・新着速報', new[:10], '情報元：モンスト公式サイト')
            print(f'新着: {len(new)} 件')
    else:
        raise ValueError('MODE は morning または check')
    state['seen'] = list(dict.fromkeys([a['url'] for a in articles] + state.get('seen', [])))[:400]
    STATE.write_text(json.dumps(state, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


if __name__ == '__main__':
    try: main()
    except Exception as exc:
        print(f'ERROR: {exc}', file=sys.stderr)
        sys.exit(1)
