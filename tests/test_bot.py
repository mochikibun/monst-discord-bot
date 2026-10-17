import copy
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch
from bs4 import BeautifulSoup
import bot

D = date(2026, 10, 19)

def record(**kw):
    r = dict(category=bot.CATEGORIES[0], name='書庫ポイント消費0', start='2026-10-19', end='2026-10-20', days=[], source='公式')
    r.update(kw)
    return r

class Tests(unittest.TestCase):
    def setUp(self):
        bot.ISSUES.clear()

    def test_today_and_week(self):
        records=[record(),record(start='2026-10-25',end='2026-10-25'),record(start='2026-10-26',end='2026-10-26')]
        self.assertEqual(len(bot.selected(records,D)),1)
        self.assertEqual(len(bot.selected(records,D,True)),2)

    def test_split_utf16(self):
        text='🔥'*3000+'a'*2500
        chunks=bot.split_messages(text)
        self.assertEqual(''.join(chunks),text)
        self.assertTrue(all(len(c.encode('utf-16-le'))//2<=1800 for c in chunks))

    def test_dates(self):
        self.assertIsNone(bot.date_value('2月30日',2026))
        self.assertEqual(bot.date_value('2027年1月2日',2026),date(2027,1,2))

    def test_year_rollover(self):
        html='<h2>追憶の書庫 ポイント消費0</h2><p>2026年12月30日〜1月2日</p><table><tr><td>1月1日</td><td>火属性</td></tr></table>'
        with patch.object(bot,'fetch',return_value=BeautifulSoup(html,'html.parser')):
            r=bot.extract_article('https://www.monster-strike.com/news/20261225_3.html','公式',date(2026,12,30))
        self.assertEqual(r[0]['end'],'2027-01-02')
        self.assertEqual(r[0]['days'],[('2027-01-01','火')])

    def test_rate_limit_and_safe_error(self):
        from types import SimpleNamespace
        limited=SimpleNamespace(status_code=429,json=lambda:{'retry_after':1})
        ok=SimpleNamespace(status_code=200)
        with patch.object(bot.requests,'post',side_effect=[limited,ok]) as post,patch.object(bot.time,'sleep'):
            bot.send('https://discord.com/api/webhooks/123/test-token','hello')
            self.assertEqual(post.call_count,2)
            self.assertEqual(post.call_args.kwargs['json']['allowed_mentions'],{'parse':[]})
        with patch.object(bot.requests,'post',side_effect=bot.requests.RequestException('secret-token')):
            with self.assertRaisesRegex(RuntimeError,'Discord通信失敗') as exc:
                bot.send('https://discord.com/api/webhooks/123/test-token','hello')
            self.assertNotIn('secret-token',str(exc.exception))

    def test_official_priority(self):
        with patch.object(bot,'official_urls',return_value=['a']),patch.object(bot,'external_urls',return_value=[('b','Game8')]),patch.object(bot,'extract_article',side_effect=[[record()],[record(source='Game8',days=[('2026-10-19','火')])]]):
            self.assertEqual(bot.gather(D)[0]['source'],'公式')

    def test_section_and_attribute(self):
        html='<main><h2>追憶の書庫のポイント消費0！</h2><p>2026年10月19日〜2026年10月25日</p><table><tr><td>10月19日</td><td>火属性</td></tr></table><h2>別の情報</h2><p>10月20日 水属性</p></main>'
        with patch.object(bot,'fetch',return_value=BeautifulSoup(html,'html.parser')):
            r=bot.extract_article('https://www.monster-strike.com/news/20261015_3.html','公式',D)
        self.assertEqual(r[0]['days'],[('2026-10-19','火')])

    def test_partial_resume_and_legacy_state(self):
        with tempfile.TemporaryDirectory(dir='.') as d, patch.object(bot,'STATE',Path(d)/'seen.json'),patch.object(bot.time,'sleep'):
            state={'legacy':{'keep':1}}
            with patch.object(bot,'render',return_value=['one','two']),patch.object(bot,'send',side_effect=[None,RuntimeError('fail')]):
                with self.assertRaises(RuntimeError): bot.publish(state,[],D,False,'dummy')
            self.assertEqual(state['deliveries']['daily:2026-10-19']['sent'],1)
            with patch.object(bot,'send') as send:
                bot.publish(state,[],D,False,'dummy')
                send.assert_called_once_with('dummy','two')
                bot.publish(state,[],D,False,'dummy')
                self.assertEqual(send.call_count,1)
            self.assertEqual(state['legacy'],{'keep':1})

    def test_warning_and_conflict(self):
        bot.issue('未確認')
        text=''.join(bot.render([record(days=[('2026-10-19','火'),('2026-10-19','水')])],D))
        self.assertIn('属性未確認',text)
        self.assertIn('⚠ 未確認',text)

    def test_check_and_monday(self):
        for mode,count in [('check',0),('morning',2),('weekly',1)]:
            with patch.dict(bot.os.environ,{'MODE':mode,'DISCORD_WEBHOOK_URL':'dummy'}),patch.object(bot,'gather',return_value=[]),patch.object(bot,'STATE',Path('nonexistent-state-test.json')),patch.object(bot,'save_state'),patch.object(bot,'publish') as pub,patch.object(bot,'datetime') as dt:
                dt.now.return_value=__import__('datetime').datetime(2026,10,19,6,tzinfo=bot.JST)
                bot.main()
                self.assertEqual(pub.call_count,count)

if __name__=='__main__': unittest.main()
