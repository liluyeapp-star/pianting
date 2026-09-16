import importlib.util
import json
import unittest
from pathlib import Path
from unittest.mock import patch
SCRIPT = Path(__file__).parents[1] / 'skills/pianting/scripts/fetch_audio.py'
spec = importlib.util.spec_from_file_location('fetch_audio', SCRIPT)
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
PAGE = 'https://www.xiaoyuzhoufm.com/episode/' + 'a' * 24
MEDIA = 'https://media.xyzcdn.net/demo/episode.m4a'
def page(**changes):
    episode = dict(eid='a'*24,title='测试对谈',duration=30,isPrivateMedia=False,payType='FREE',media={'source':{'mode':'PUBLIC','url':MEDIA},'size':100},enclosure={'url':MEDIA})
    episode.update(changes)
    return '<html><script id="__NEXT_DATA__" type="application/json">'+json.dumps({'props':{'pageProps':{'episode':episode}}})+'</script></html>'
class SourceTests(unittest.TestCase):
    def test_free_public_episode(self):
        result=m.parse_episode(page(),PAGE)
        self.assertEqual(result['audio_url'],MEDIA)
        self.assertEqual(result['duration_seconds'],30)
    def test_paid_private_or_missing_evidence_stop(self):
        for changes in ({'payType':'PAID'},{'isPrivateMedia':True},{'isPrivateMedia':None},{'media':{'source':{'mode':'PRIVATE','url':MEDIA}}}):
            with self.subTest(changes=changes),self.assertRaises(m.SourceError):m.parse_episode(page(**changes),PAGE)
    def test_wrong_episode_or_missing_static_data_stop(self):
        for html in (page(eid='b'*24),'<html>login</html>'):
            with self.assertRaises(m.SourceError):m.parse_episode(html,PAGE)
    def test_api_urls_and_auth_media_forbidden(self):
        for u,purpose in [('https://api.xiaoyuzhoufm.com/episode/get','media'),('https://www.xiaoyuzhoufm.com/_next/data/build/episode.json','page'),(MEDIA+'?token=abc','media'),('https://localhost/test.mp3','media'),('https://user:pass@example.com/a.mp3','media')]:
            with self.subTest(url=u),self.assertRaises(m.SourceError):m.validate_url(u,purpose,resolve=False)
    def test_redirect_cannot_enter_api(self):
        handler=m.SafeRedirect('media')
        with self.assertRaises(m.SourceError):handler.redirect_request(None,None,302,'',{},'https://api.xiaoyuzhoufm.com/episode/get')
    def test_private_network_rejected(self):
        with patch.object(m.socket,'getaddrinfo',return_value=[(2,1,6,'',('127.0.0.1',443))]),self.assertRaises(m.SourceError):
            m.validate_url('https://example.com/a.mp3')
    def test_rss_requires_unique_episode(self):
        body=b'<rss><channel><item><title>A</title><guid>one</guid><enclosure url="https://example.com/a.mp3" /></item><item><title>B</title><guid>two</guid><enclosure url="https://example.com/b.mp3" /></item></channel></rss>'
        with self.assertRaises(m.SourceError):m.parse_rss(body,'https://example.com/feed',None)
        self.assertEqual(m.parse_rss(body,'https://example.com/feed','two')['title'],'B')
if __name__=='__main__':unittest.main()
