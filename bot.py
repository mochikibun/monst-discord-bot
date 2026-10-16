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
# 公式記事でよく使われる日付・曜日・時刻の表記を吸収する。
DATE = r'(?:20\d{2}\s*[年/.-]\s*)?\d{1,2}\s*(?:月|[/.\-])\s*\d{1,2}\s*日?'
DAY = rf'{DATE}(?:\s*[（(][^）)]{{1,5}}[）)])?'
CLOCK = r'(?:\s*(?:午前|午後)?\s*\d{1,2}\s*[:：]\s*\d{2})?'
SEP = r'(?:～|〜|~|－|–|—|ー|\-|から|より|～|〜)'
PERIOD = re.compile(rf'({DAY}){CLOCK}\s*{SEP}\s*({DAY}){CLOCK}')
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

    # stripped_strings では日付と区切り記号が別々になりやすい。
    # HTMLのブロック単位で結合した文章も用意する。
    fragments = [clean(t) for t in main.stripped_strings if clean(t)]
    blocks = []
    for node in main.select('h1,h2,h3,h4,h5,p,li,tr,section,div'):
        # 親divによる巨大な重複を避ける
        if node.name in ('div', 'section') and len(node.select('div,section,p,li,tr')) > 6:
            continue
        line = clean(node.get_text(' ', strip=True))
        if 5 <= len(line) <= 900 and line not in blocks:
            blocks.append(line)
    # 断片をつないだ文章でも検出（最大数を制限して負荷を抑える）
    windows = [' '.join(fragments[i:i+16]) for i in range(0, min(len(fragments), 1200), 8)]
    texts = list(dict.fromkeys(blocks + windows))
    period_count = sum(len(PERIOD.findall(t)) for t in texts)
    print(f'記事解析: {url} / 断片 {len(fragments)}件 / ブロック {len(blocks)}件 / 期間表記 {period_count}件')

    results = []
    for line in texts:
        matches = PERIOD.findall(line)
        if not matches:
            continue
        # 同じ文章の中で対象が分かる場合のみ採用。
        # 離れた別キャンペーンの期間との誤対応を避ける。
        groups = [(g, pat) for g, pat in GROUPS if re.search(pat, line)]
        if not groups:
            continue
        if len(groups) > 1:
            groups = [g for g in groups if g[0] != '🎁 その他'] or groups
        for group, pattern in groups:
            for start, end in matches[:3]:
                # 期間の前後の近傍にキャンペーン名があるか確認する
                match_text = f'{start} ～ {end}'
                label = title[:85]
                short = line[:120]
                if len(short) <= 85 and re.search(pattern, short):
                    label = short
                attrs = '・'.join(dict.fromkeys(ATTR.findall(line)))
                results.append({'group': group, 'name': label,
                                'period': match_text, 'attributes': attrs, 'url': url})
    unique = {(r['group'], r['name'], r['period']): r for r in results}
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
    candidates = list(dict.fromkeys(candidates))[:36]
    counts = {source: sum(1 for _, _, s in candidates if s == source) for source in SITES}
    print('攻略サイト検索結果: ' + ', '.join(f'{k}={v}件' for k, v in counts.items()))
    return candidates


def gather():
    candidates = []
    try:
        candidates.extend((url, title, 'モンスト公式') for url, title in news_links())
    except (requests.RequestException, RuntimeError) as exc:
        print(f'公式ニュース一覧取得失敗: {exc}')
    candidates.extend(external_links())
    print(f'候補記事: {len(candidates)}件（公式＋攻略サイト）')
    print('情報源別: ' + ', '.join(f'{src}={sum(1 for _, _, s in candidates if s == src)}件' for src in ['モンスト公式', *SITES]))
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
