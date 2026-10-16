"""モンスト公式ニュースからキャンペーン概要を自動整理してDiscordへ投稿。"""
import hashlib
import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin, urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

BASE = 'https://www.monster-strike.com/news/'
STATE = Path('seen.json')
JST = ZoneInfo('Asia/Tokyo')
HEADERS = {'User-Agent': 'Mozilla/5.0 (compatible; PersonalMonstCampaignBot/1.0)'}
GROUPS = [
    ('📚 追憶の書庫', r'追憶の書庫|書庫ポイント'),
    ('💎 英雄の神殿', r'英雄の神殿|わくわくの実|神殿'),
    ('✨ ノーマルクエスト', r'ノーマルクエスト|ノマクエ'),
    ('⚔️ 絶級・降臨関連', r'絶級|轟絶|黎絶|爆絶|超絶|降臨'),
    ('🎁 その他', r'キャンペーン|確率アップ|報酬.*倍|経験値.*倍|大成功|オーブ'),
]
DATE = r'(?:20\d{2}年)?\s*\d{1,2}月\s*\d{1,2}日(?:\s*\([^)]{1,3}\))?'
PERIOD = re.compile(rf'({DATE})\s*(?:\([^)]*\))?\s*[～〜~\-－–ー]\s*({DATE})')
SINGLE = re.compile(DATE)
ATTR = re.compile(r'火属性|水属性|木属性|光属性|闇属性|全属性')


def get_soup(url):
    r = requests.get(url, headers=HEADERS, timeout=25)
    r.raise_for_status()
    r.encoding = 'utf-8'
    return BeautifulSoup(r.text, 'html.parser')


def news_links():
    soup = get_soup(BASE)
    links = []
    for a in soup.select('a[href]'):
        url = urljoin(BASE, a['href']).split('#')[0]
        if not re.search(r'/news/\d{8}_\d+\.html$', urlparse(url).path):
            continue
        title = ' '.join(a.stripped_strings)
        if len(title) >= 7 and url not in [u for u, _ in links]:
            links.append((url, title[:150]))
    if not links:
        raise RuntimeError('公式ニュース一覧を取得できません。サイト構造の変更を確認してください。')
    return links[:35]


def clean(s):
    return re.sub(r'\s+', ' ', s).strip()


def extract(url, title):
    soup = get_soup(url)
    for tag in soup.select('script,style,nav,footer,header'):
        tag.decompose()
    main = soup.select_one('article, main, .newsDetail, .news_detail, #contents') or soup.body or soup
    # 見出しや段落・表の各行を単位にして、無関係な日付の混入を抑える
    lines = []
    for node in main.select('h1,h2,h3,h4,p,li,tr'):
        if node.find_parent(['p', 'li', 'tr']) and node.name not in ('tr',):
            continue
        s = clean(node.get_text(' ', strip=True))
        if 5 <= len(s) <= 260 and (not lines or lines[-1] != s):
            lines.append(s)
    if not lines:
        return []
    results = []
    for i, line in enumerate(lines):
        # 同一行にキャンペーン名と日程がある場合、または近傍に期間がある場合だけ採用
        for group, pattern in GROUPS:
            if not re.search(pattern, line):
                continue
            if group == '🎁 その他' and any(re.search(p, line) for _, p in GROUPS[:-1]):
                continue
            neighborhood = lines[i:i+5]
            periods = []
            attrs = []
            for s in neighborhood:
                periods += [clean(a) + ' ～ ' + clean(b) for a, b in PERIOD.findall(s)]
                attrs += ATTR.findall(s)
            periods = list(dict.fromkeys(periods))[:2]
            attrs = list(dict.fromkeys(attrs))[:5]
            # 日程が特定できない記事は、誤解を招くため一覧に入れない
            if not periods:
                continue
            description = line[:85]
            if len(description) < 8:
                description = title[:85]
            results.append({'group': group, 'name': description, 'period': periods[0],
                            'attributes': '・'.join(attrs), 'url': url})
    unique = {}
    for x in results:
        unique[(x['group'], x['name'], x['period'])] = x
    return list(unique.values())[:20]


def gather():
    records = []
    # 複数の新着記事を確認。取得失敗はログに残し、別の記事は継続
    for url, title in news_links():
        if not re.search(r'キャンペーン|書庫|神殿|ノーマル|絶級|周年|報酬|経験値|強化|轟絶|黎絶', title):
            continue
        try:
            records.extend(extract(url, title))
        except requests.RequestException as exc:
            print(f'記事取得失敗: {url}: {exc}')
    return records


def send(webhook, content):
    r = requests.post(webhook, json={'content': content, 'allowed_mentions': {'parse': []}}, timeout=25)
    if r.status_code == 429:
        time.sleep(min(float(r.json().get('retry_after', 2)) + 1, 30))
        r = requests.post(webhook, json={'content': content, 'allowed_mentions': {'parse': []}}, timeout=25)
    r.raise_for_status()


def render(records, now):
    parts = [f'**🎁 {now:%Y/%m/%d} モンスト キャンペーン一覧**',
             '公式記事から開催期間を確認できた項目のみ掲載しています。\n※現在開催中かどうかは未判定です。']
    for group, _ in GROUPS:
        items = [x for x in records if x['group'] == group]
        if not items:
            continue
        parts.append(f'\n**{group}**')
        for x in items[:8]:
            parts.append(f"・{x['name']}\n  📅 {x['period']}" +
                         (f" / 対象記載: {x['attributes']}" if x['attributes'] else '') +
                         f"\n  {x['url']}")
    if not records:
        parts.append('確認できるキャンペーン日程はありませんでした。')
    parts.append('\n情報元: ' + BASE)
    # Discord 2000文字制限を考慮して行単位で分割
    chunks, chunk = [], ''
    for block in parts:
        if len(chunk) + len(block) + 2 > 1850 and chunk:
            chunks.append(chunk)
            chunk = ''
        chunk += block + '\n\n'
    if chunk:
        chunks.append(chunk)
    return chunks


def main():
    webhook = os.environ.get('DISCORD_WEBHOOK_URL', '').strip()
    if not webhook:
        raise RuntimeError('DISCORD_WEBHOOK_URL が未設定です')
    mode = os.environ.get('MODE', 'check')
    now = datetime.now(JST)
    state = json.loads(STATE.read_text(encoding='utf-8')) if STATE.exists() else {}
    if mode not in ('morning', 'check', 'campaigns'):
        raise ValueError('MODE は morning / check / campaigns')
    if mode == 'morning' and state.get('last_campaign_morning') == now.strftime('%Y-%m-%d'):
        print('本日のキャンペーン一覧は送信済み')
        return
    records = gather()
    fingerprint = hashlib.sha256(json.dumps(records, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    state['campaign_hash'] = fingerprint
    state['campaign_count'] = len(records)
    state['last_campaign_check'] = now.isoformat()
    if mode != 'check':
        for chunk in render(records, now):
            send(webhook, chunk)
            time.sleep(1)
        if mode == 'morning':
            state['last_campaign_morning'] = now.strftime('%Y-%m-%d')
    else:
        print(f'確認のみ: {len(records)}件。Discordへの速報投稿はしません。')
    STATE.write_text(json.dumps(state, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(f'処理完了: {len(records)}件')


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print(f'ERROR: {exc}', file=sys.stderr)
        sys.exit(1)
