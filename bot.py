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



# 公式記事の本文から、確認できたキャンペーン情報だけを短いリストにします。
CAMPAIGN_GROUPS = [
    ('📚 追憶の書庫', r'追憶の書庫|書庫ポイント'),
    ('💎 英雄の神殿', r'英雄の神殿|わくわくの実'),
    ('✨ ノーマルクエスト', r'ノーマルクエスト|ノマクエ'),
    ('⚔️ 絶級・降臨関連', r'絶級|超絶|爆絶|轟絶|黎絶|降臨'),
    ('🎁 その他', r'強化合成|大成功|ラック|キャンペーン|スタミナ'),
]
DATE_PATTERN = re.compile(r'(?:20\d{2}年)?\s*\d{1,2}月\d{1,2}日(?:\s*\([^)]{1,5}\))?(?:\s*[0-2]?\d:[0-5]\d)?')
ATTRIBUTE_PATTERN = re.compile(r'火属性|水属性|木属性|光属性|闇属性|全属性')


def fetch_campaigns(articles, limit=12):
    """公式ニュース本文を解析。確実に取得できた文字列だけを掲載する。"""
    found = []
    for item in articles[:limit]:
        if not re.search(r'キャンペーン|書庫|神殿|ノーマル|絶級|報酬|イベント', item['title']):
            continue
        try:
            r = requests.get(item['url'], headers=HEADERS, timeout=25)
            r.raise_for_status()
            r.encoding = 'utf-8'
            soup = BeautifulSoup(r.text, 'html.parser')
            for tag in soup(['script', 'style', 'nav', 'footer', 'header']):
                tag.decompose()
            main = soup.select_one('article, main, .newsDetail, .news_detail, #contents') or soup.body or soup
            lines = [re.sub(r'\s+', ' ', x).strip() for x in main.stripped_strings]
            lines = [x for x in lines if 5 <= len(x) <= 250]
            if not lines:
                continue
            for group, pattern in CAMPAIGN_GROUPS:
                matches = [x for x in lines if re.search(pattern, x)]
                if not matches:
                    continue
                # その他に分類するのは、他の専用カテゴリに該当しない場合だけ。
                if group == '🎁 その他' and any(re.search(p, x) for x in matches for _, p in CAMPAIGN_GROUPS[:-1]):
                    matches = [x for x in matches if not any(re.search(p, x) for _, p in CAMPAIGN_GROUPS[:-1])]
                if not matches:
                    continue
                dates = []
                for line in lines:
                    if DATE_PATTERN.search(line) and len(line) <= 140:
                        dates.extend(DATE_PATTERN.findall(line))
                dates = list(dict.fromkeys(dates))[:6]
                attrs = list(dict.fromkeys(a for line in matches for a in ATTRIBUTE_PATTERN.findall(line)))
                # 見出し/説明文は転載せず、短い抜粋で概要を表示する。
                headline = re.sub(r'\s+', ' ', matches[0])[:75]
                found.append({'group': group, 'summary': headline, 'dates': dates,
                              'attrs': attrs, 'url': item['url']})
        except requests.RequestException as exc:
            print(f"記事を取得できませんでした: {item['url']} ({exc})")
    # 同じ記事・カテゴリの重複を除外
    return list({(x['group'], x['url']): x for x in found}.values())


def post_campaigns(webhook, articles, now):
    items = fetch_campaigns(articles)
    lines = [f'**🎁 モンスト キャンペーン一覧 ({now:%Y/%m/%d})**',
             '公式ニュース本文で確認できた情報の一覧です。開催中とは限りません。']
    for group, _ in CAMPAIGN_GROUPS:
        group_items = [x for x in items if x['group'] == group]
        if not group_items:
            continue
        lines.append(f'\n**{group}**')
        for x in group_items[:4]:
            lines.append('・' + x['summary'])
            if x['dates']:
                lines.append('  📅 記事中の日付: ' + ' / '.join(x['dates']))
            if x['attrs']:
                lines.append('  🎨 記載属性: ' + '・'.join(x['attrs']))
            lines.append('  🔗 ' + x['url'])
    if not items:
        lines.append('該当するキャンペーン情報を記事本文から確認できませんでした。')
    lines.append('※ 日付は記事中に記載されたものです。開催期間や日別の対象表とは限りません。')
    # Discord の2000文字制限を超えないよう、行単位で分割する。
    chunks, current = [], ''
    for line in lines:
        if len(current) + len(line) + 1 > 1900:
            chunks.append(current)
            current = ''
        current += line + '\n'
    if current:
        chunks.append(current)
    for chunk in chunks:
        resp = requests.post(webhook, json={'content': chunk, 'allowed_mentions': {'parse': []}}, timeout=25)
        resp.raise_for_status()
        time.sleep(1)
    return len(items)

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
        post_campaigns(webhook, articles, now)
        state['last_morning'] = now.strftime('%Y-%m-%d')
    elif mode == 'campaigns':
        count = post_campaigns(webhook, articles, now)
        print(f'キャンペーン情報: {count} 件')
    elif mode == 'check':
        if not STATE.exists():
            print('初回は既存記事を登録し、速報の大量投稿を防ぎます。')
        else:
            new = [a for a in articles if a['url'] not in seen]
            if new:
                post(webhook, '🚨 モンスト公式・新着速報', new[:10], '情報元：モンスト公式サイト')
            print(f'新着: {len(new)} 件')
    else:
        raise ValueError('MODE は morning、check または campaigns')
    state['seen'] = list(dict.fromkeys([a['url'] for a in articles] + state.get('seen', [])))[:400]
    STATE.write_text(json.dumps(state, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


if __name__ == '__main__':
    try: main()
    except Exception as exc:
        print(f'ERROR: {exc}', file=sys.stderr)
        sys.exit(1)
