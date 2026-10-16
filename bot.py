"""モンストのキャンペーン日程のみをDiscordへ投稿するBot。
情報源は公式キャンペーン記事を優先し、攻略サイトは補助候補として参照。
"""
import json
import os
import re
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime, date, timedelta
from pathlib import Path
from urllib.parse import urljoin, urlparse, urlencode
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup, Tag

JST = ZoneInfo('Asia/Tokyo')
OFFICIAL = 'https://www.monster-strike.com/news/campaign/'
SITES = {'GameWith': 'gamewith.jp', 'Game8': 'game8.jp', 'AppBank': 'appbank.net'}
SEARCHES = ['モンスト キャンペーン 追憶の書庫', 'モンスト 英雄の神殿 ノーマルクエスト キャンペーン']
HEADERS = {'User-Agent': 'Mozilla/5.0 (compatible; MonstCampaignCalendar/2.0)'}
STATE = Path('seen.json')
CATEGORIES = ['📚 追憶の書庫', '💎 英雄の神殿', '✨ ノーマルクエスト', '⚔️ 絶級・降臨', '🎁 その他']
DATE_RX = r'(?:(?:20\d{2})[年/.-])?\s*\d{1,2}\s*(?:月|[/.-])\s*\d{1,2}\s*日?'
DATE_PART = re.compile(DATE_RX)
DATE_RANGE = re.compile(rf'({DATE_RX})\s*(?:[（(][月火水木金土日祝]+[）)])?\s*(?:\d{{1,2}}:\d{{2}})?\s*[～〜~－–—-]\s*({DATE_RX})')
ATTR = re.compile(r'(火|水|木|光|闇)属性')
KEYS = re.compile('追憶の書庫|書庫ポイント|英雄の神殿|わくわくの実|ノーマルクエスト|ノマクエ|絶級|超絶|爆絶|轟絶|黎絶|降臨|キャンペーン|報酬|経験値|金卵|モンスポット|強化')


def clean(s):
    return re.sub(r'\s+', ' ', s).strip()


def fetch(url):
    r = requests.get(url, headers=HEADERS, timeout=22)
    r.raise_for_status()
    r.encoding = r.apparent_encoding if r.apparent_encoding and r.apparent_encoding.lower() not in ('ascii',) else 'utf-8'
    return BeautifulSoup(r.text, 'html.parser')


def date_value(s, default_year):
    m = re.search(r'(?:(20\d{2})[年/.-])?\s*(\d{1,2})\s*(?:月|[/.-])\s*(\d{1,2})', s)
    if not m:
        return None
    try:
        return date(int(m.group(1) or default_year), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def category(text):
    if re.search('追憶の書庫|書庫ポイント', text): return CATEGORIES[0]
    if re.search('英雄の神殿|わくわくの実.*(?:排出|報酬)|感謝の間', text): return CATEGORIES[1]
    if re.search('ノーマルクエスト|ノマクエ', text): return CATEGORIES[2]
    if re.search('絶級|超絶|爆絶|轟絶|黎絶|降臨', text): return CATEGORIES[3]
    return CATEGORIES[4] if KEYS.search(text) else None


def label(heading):
    h = clean(heading).replace('！', '').replace('!', '')
    if '追憶の書庫' in h and ('消費' in h or 'ポイント' in h): return '書庫ポイント消費0'
    if 'ノーマルクエスト' in h and ('経験値' in h or 'EXP' in h):
        mult = re.search(r'\d+(?:\.\d+)?倍', h)
        return '獲得経験値' + (mult.group() if mult else 'アップ')
    if '英雄の神殿' in h:
        if '2個' in h: return 'ノーコン報酬 わくわくの実2個'
        return '英雄の神殿キャンペーン'
    if 'モンスポット' in h and '学び' in h: return '学びのパワー出現率アップ'
    if '金卵' in h: return '金卵排出率アップ'
    if '絶級' in h or '超爆轟黎' in h: return '絶級・降臨キャンペーン'
    # 記事タイトルの流用は禁止。内容が特定できないものは表示しない。
    return None


def section_text(node):
    """見出しから次の同レベル以上の見出しまでの内容を取得。"""
    parts = []
    level = int(node.name[1]) if node.name and re.fullmatch(r'h[1-6]', node.name) else 3
    for nxt in node.next_elements:
        if nxt is node: continue
        if isinstance(nxt, Tag) and re.fullmatch(r'h[1-6]', nxt.name or '') and int(nxt.name[1]) <= level:
            break
        if isinstance(nxt, Tag) and nxt.name in ('tr', 'p', 'li', 'div'):
            # divの大きなブロックによる別セクション混入を防止
            if nxt.name == 'div': continue
            s = clean(nxt.get_text(' ', strip=True))
            if s and (not parts or parts[-1] != s): parts.append(s)
        if len(parts) > 180: break
    return parts


def extract_article(url, source, today):
    soup = fetch(url)
    for tag in soup.select('script,style,nav,footer,header'):
        tag.decompose()
    main = soup.select_one('article, main, .newsDetail, .news_detail, #contents') or soup.body or soup
    records = []
    headings = main.select('h2,h3,h4')
    print(f'[{source}] 見出し数={len(headings)} URL={url}')
    for heading in headings:
        title = clean(heading.get_text(' ', strip=True))
        cat = category(title)
        name = label(title)
        if not cat or not name: continue
        lines = section_text(heading)
        if not lines: continue
        blob = '\n'.join(lines)
        ranges = DATE_RANGE.findall(blob)
        periods = []
        for a, b in ranges:
            start = date_value(a, today.year)
            end = date_value(b, start.year if start else today.year)
            if start and end and end < start and not re.search('20\d{2}', b):
                try: end = end.replace(year=end.year + 1)
                except ValueError: continue
            if start and end and start <= end and end >= today and start <= today + timedelta(days=120):
                periods.append((start, end))
        # 日付・属性の対応が表で確認できる場合のみ、日別属性を紐付ける
        daily = []
        for line in lines:
            matches = DATE_PART.findall(line)
            attrs = ATTR.findall(line)
            if len(matches) == 1 and len(attrs) == 1:
                d = date_value(matches[0], today.year)
                if d and today <= d <= today + timedelta(days=120):
                    daily.append((d, attrs[0]))
        if not periods: continue
        for start, end in sorted(set(periods)):
            days = [(d, a) for d, a in daily if start <= d <= end]
            records.append({'category': cat, 'name': name, 'start': start.isoformat(),
                            'end': end.isoformat(), 'days': [(d.isoformat(), a) for d, a in days],
                            'source': source})
    print(f'[{source}] 確認できた日程={len(records)}件')
    return records


def official_urls():
    soup = fetch(OFFICIAL)
    out = []
    for a in soup.select('a[href]'):
        url = urljoin(OFFICIAL, a.get('href', '')).split('#')[0]
        if re.search(r'/news/20\d{6}_\d+\.html$', urlparse(url).path) and url not in out:
            out.append(url)
    return out[:20]


def external_urls():
    found = []
    for source, domain in SITES.items():
        for term in SEARCHES:
            try:
                u = 'https://www.bing.com/search?' + urlencode({'q': f'site:{domain} {term}', 'format': 'rss'})
                r = requests.get(u, headers=HEADERS, timeout=12)
                r.raise_for_status()
                root = ET.fromstring(r.content)
                for item in root.findall('.//item')[:4]:
                    link = clean(item.findtext('link') or '')
                    host = (urlparse(link).hostname or '').lower()
                    if (host == domain or host.endswith('.' + domain)) and link.startswith('https://'):
                        found.append((link, source))
            except (requests.RequestException, ET.ParseError) as e:
                print(f'[{source}] 検索できませんでした: {e}')
    return list(dict.fromkeys(found))[:18]


def gather(today):
    candidates = []
    try:
        candidates += [(u, '公式') for u in official_urls()]
    except requests.RequestException as e:
        print(f'[公式] 一覧取得失敗: {e}')
    candidates += external_urls()
    print('候補件数:', len(candidates), '内訳:', {s: sum(src == s for _, src in candidates) for s in ['公式', *SITES]})
    records = []
    for url, source in candidates:
        try:
            records.extend(extract_article(url, source, today))
        except (requests.RequestException, ValueError) as e:
            print(f'[{source}] 記事取得失敗: {url}: {e}')
    # 公式を優先。同一カテゴリ・内容・期間を重複表示しない。
    records.sort(key=lambda x: (x['source'] != '公式', x['start'], x['name']))
    unique = {}
    for r in records:
        key = (r['category'], r['name'], r['start'], r['end'])
        if key not in unique or (not unique[key]['days'] and r['days']): unique[key] = r
    print('一覧掲載:', len(unique), '件')
    return list(unique.values())


def render(records, today):
    lines = [f'🎁 **{today:%Y/%m/%d} モンスト キャンペーン一覧**']
    for cat in CATEGORIES:
        items = [r for r in records if r['category'] == cat]
        if not items: continue
        lines.append('\n**' + cat + '**')
        for r in items[:10]:
            a, b = date.fromisoformat(r['start']), date.fromisoformat(r['end'])
            lines.append(f'・{r["name"]}｜{a:%m/%d}〜{b:%m/%d}')
            for ds, attr in r['days']:
                d = date.fromisoformat(ds)
                if d >= today:
                    emoji = {'火':'🔥','水':'💧','木':'🌳','光':'✨','闇':'🌙'}[attr]
                    lines.append(f'  {d:%m/%d} {emoji}{attr}属性')
    if len(lines) == 1:
        lines.append('確認できた開催中・開催予定の日程はありません。')
    lines.append('\n※日程は取得時点の情報です。')
    chunks, buf = [], ''
    for line in lines:
        if len(buf) + len(line) + 1 > 1800 and buf:
            chunks.append(buf.rstrip()); buf = ''
        buf += line + '\n'
    if buf: chunks.append(buf.rstrip())
    return chunks


def send(webhook, content):
    r = requests.post(webhook, json={'content': content, 'allowed_mentions': {'parse': []}}, timeout=25)
    r.raise_for_status()


def main():
    mode = os.environ.get('MODE', 'morning')
    if mode not in ('morning', 'campaigns', 'check'): raise ValueError('不明なMODE')
    now = datetime.now(JST)
    state = json.loads(STATE.read_text(encoding='utf-8')) if STATE.exists() else {}
    if mode == 'morning' and state.get('last_campaign_morning') == now.date().isoformat():
        print('本日の投稿済み'); return
    records = gather(now.date())
    if mode != 'check':
        webhook = os.environ.get('DISCORD_WEBHOOK_URL', '').strip()
        if not webhook: raise RuntimeError('DISCORD_WEBHOOK_URL が未設定です')
        for content in render(records, now.date()):
            send(webhook, content); time.sleep(1)
        if mode == 'morning': state['last_campaign_morning'] = now.date().isoformat()
    state['campaign_count'] = len(records)
    state['last_campaign_check'] = now.isoformat()
    STATE.write_text(json.dumps(state, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print('処理完了:', len(records), '件')


if __name__ == '__main__':
    try: main()
    except Exception as exc:
        print(f'ERROR: {exc}', file=sys.stderr)
        sys.exit(1)
