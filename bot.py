"""モンスト公式ニュースからキャンペーン概要を自動整理してDiscordへ投稿。"""
import hashlib
import xml.etree.ElementTree as ET
from datetime import date, timedelta
from urllib.parse import urlencode
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
SITES = {'GameWith': 'gamewith.jp', 'Game8': 'game8.jp', 'AppBank': 'appbank.net'}
SEARCH_TERMS = ['モンスト 書庫 キャンペーン', 'モンスト 神殿 ノマクエ キャンペーン', 'モンスト 絶級 キャンペーン']
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
# 日本語表記・スラッシュ表記の両方に対応
DATE = r'(?:20\d{2}年\s*)?\d{1,2}\s*(?:月|/)\s*\d{1,2}\s*日?'
DAY = rf'{DATE}(?:\s*[（(][月火水木金土日祝]{1,3}[）)])?'
# 「10月16日（金）0:00～10月20日（火）23:59」なども扱う
PERIOD = re.compile(rf'({DAY})(?:\s*(?:AM|PM)?\s*\d{{1,2}}:\d{{2}})?\s*(?:～|〜|~|－|–|—|ー|\-|から)\s*({DAY})')
SINGLE = re.compile(DAY)
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
    # 見出し・段落以外のdivや表内テキストも拾う。空白を残しすぎない。
    lines = []
    for text in main.stripped_strings:
        line = clean(text)
        if 3 <= len(line) <= 350 and (not lines or lines[-1] != line):
            lines.append(line)
    print(f'記事解析: {url} / テキスト断片 {len(lines)}件')
    results = []
    for i, line in enumerate(lines):
        for group, pattern in GROUPS:
            if not re.search(pattern, line):
                continue
            if group == '🎁 その他' and any(re.search(p, line) for _, p in GROUPS[:-1]):
                continue
            # 近接する日程だけを採用し、別キャンペーンの日程混入を抑える
            neighborhood = lines[i:min(i + 7, len(lines))]
            periods = []
            attrs = []
            for entry in neighborhood:
                periods.extend(f'{clean(a)} ～ {clean(b)}' for a, b in PERIOD.findall(entry))
                attrs.extend(ATTR.findall(entry))
            periods = list(dict.fromkeys(periods))
            attrs = list(dict.fromkeys(attrs))[:5]
            if not periods:
                continue
            results.append({
                'group': group, 'name': (line if len(line) >= 8 else title)[:85],
                'period': periods[0], 'attributes': '・'.join(attrs), 'url': url
            })
    unique = {}
    for record in results:
        unique[(record['group'], record['name'], record['period'])] = record
    print(f'  日程抽出: {len(unique)}件')
    return list(unique.values())[:20]


def external_links():
    """Bing公開RSS検索から候補URLを発見。各サイトが許可する範囲でのみ取得する。"""
    candidates = []
    for source, domain in SITES.items():
        for term in SEARCH_TERMS:
            try:
                rss_url = 'https://www.bing.com/search?' + urlencode({'q': f'site:{domain} {term}', 'format': 'rss'})
                response = requests.get(rss_url, headers=HEADERS, timeout=18)
                response.raise_for_status()
                root = ET.fromstring(response.content)
                for item in root.findall('.//item')[:6]:
                    link = (item.findtext('link') or '').strip()
                    title = clean(item.findtext('title') or '')
                    host = (urlparse(link).hostname or '').lower()
                    if (host == domain or host.endswith('.' + domain)) and link.startswith('https://'):
                        candidates.append((link, title, source))
            except (requests.RequestException, ET.ParseError) as exc:
                print(f'{source} 検索失敗: {exc}')
    return list(dict.fromkeys(candidates))[:36]


def gather():
    candidates = []
    try:
        candidates.extend((url, title, 'モンスト公式') for url, title in news_links())
    except (requests.RequestException, RuntimeError) as exc:
        print(f'公式ニュース一覧取得失敗: {exc}')
    candidates.extend(external_links())
    print(f'候補記事: {len(candidates)}件（公式＋攻略サイト）')
    records = []
    for url, title, source in candidates:
        if not re.search(r'キャンペーン|書庫|神殿|ノーマル|絶級|周年|報酬|経験値|強化|轟絶|黎絶|モンスト', title):
            continue
        try:
            found = extract(url, title)
            for item in found:
                item['source'] = source
            records.extend(found)
        except (requests.RequestException, ValueError) as exc:
            print(f'{source} 記事取得失敗: {url}: {exc}')
    # 同じ出典の記事内で重複している項目だけ除去
    unique = {(r['url'], r['group'], r['name'], r['period']): r for r in records}
    print(f'抽出合計: {len(unique)}件')
    return list(unique.values())


def send(webhook, content):
    r = requests.post(webhook, json={'content': content, 'allowed_mentions': {'parse': []}}, timeout=25)
    if r.status_code == 429:
        time.sleep(min(float(r.json().get('retry_after', 2)) + 1, 30))
        r = requests.post(webhook, json={'content': content, 'allowed_mentions': {'parse': []}}, timeout=25)
    r.raise_for_status()


def render(records, now):
    parts = [f'**🎁 {now:%Y/%m/%d} モンスト キャンペーン一覧**',
             '公式・攻略サイトの記事から期間表記を検出した候補です。\n※開催中かどうか、期間と対象の対応は未検証です。必ずリンク先をご確認ください。']
    for group, _ in GROUPS:
        items = [x for x in records if x['group'] == group]
        if not items:
            continue
        parts.append(f'\n**{group}**')
        for x in items[:8]:
            parts.append(f"・{x['name']}\n  📅 {x['period']}" +
                         (f" / 対象記載: {x['attributes']}" if x['attributes'] else '') +
                         f"\n  出典: {x.get('source', '不明')} / {x['url']}")
    if not records:
        parts.append('日程を読み取れる候補は見つかりませんでした。開催がないという意味ではありません。')
    parts.append('\n情報源: モンスト公式 / GameWith / Game8 / AppBank（取得できたサイトのみ）')
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
