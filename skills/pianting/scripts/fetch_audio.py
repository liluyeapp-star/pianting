#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Retrieve a free public episode using anonymous HTML, RSS or a media URL.
No browser state, platform API, JavaScript execution or third-party proxy.
"""
from __future__ import annotations
import argparse
import hashlib
import ipaddress
import json
import os
import re
import socket
import sys
import urllib.parse as urlparse
import urllib.request as request
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from pathlib import Path

UA = 'Mozilla/5.0 (compatible; Pianting/0.1; anonymous-public-media)'
EXTENSIONS = {'.mp3', '.m4a', '.mp4', '.aac', '.wav', '.ogg', '.flac'}
PAGE_LIMIT = 8 * 1024 * 1024
MEDIA_LIMIT = 1024 * 1024 * 1024
XYZ = {'www.xiaoyuzhoufm.com', 'xiaoyuzhoufm.com'}

class SourceError(ValueError):
    pass

def validate_url(url, purpose='media', resolve=True):
    p = urlparse.urlsplit(url)
    host = (p.hostname or '').lower()
    if p.scheme not in ('https', 'http') or not host or p.username or p.password:
        raise SourceError('只接受不含账号密码的 HTTP(S) 公开地址')
    if p.port not in (None, 80, 443):
        raise SourceError('不支持非标准端口')
    if host == 'localhost' or host.endswith('.localhost'):
        raise SourceError('拒绝访问本机或局域网地址')
    if host.endswith('.xiaoyuzhoufm.com') or host == 'xiaoyuzhoufm.com':
        if not (purpose == 'page' and host in XYZ and p.scheme == 'https'
                and re.fullmatch(r'/episode/[0-9a-f]{24}/?', p.path) and not p.query):
            raise SourceError('小宇宙仅允许匿名单集网页；API、登录、搜索及其他接口均不访问')
    if host == 'xyzcdn.net' or host.endswith('.xyzcdn.net'):
        if purpose != 'media' or p.scheme != 'https' or Path(p.path).suffix.lower() not in EXTENSIONS or p.query:
            raise SourceError('仅接受公开页面给出的无鉴权参数静态音频，不接受签名、接口或分片地址')
    if resolve:
        try:
            addresses = socket.getaddrinfo(host, p.port or (443 if p.scheme == 'https' else 80), type=socket.SOCK_STREAM)
        except socket.gaierror as exc:
            raise SourceError('无法解析公开音源域名') from exc
        if not addresses or any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses):
            raise SourceError('拒绝访问本机、局域网或保留网络地址')
    return p

class SafeRedirect(request.HTTPRedirectHandler):
    def __init__(self, purpose): self.purpose = purpose
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        validate_url(newurl, self.purpose)
        return super().redirect_request(req, fp, code, msg, headers, newurl)

def open_public(url, purpose):
    validate_url(url, purpose)
    # No cookies, credentials, environment proxy or persisted browser state.
    opener = request.build_opener(request.ProxyHandler({}), SafeRedirect(purpose))
    return opener.open(request.Request(url, headers={'User-Agent': UA, 'Accept-Encoding': 'identity'}), timeout=60)

def read_document(url, purpose):
    with open_public(url, purpose) as response:
        body = response.read(PAGE_LIMIT + 1)
        if len(body) > PAGE_LIMIT: raise SourceError('页面或 RSS 超过 8 MiB，停止处理')
        return body, response.headers.get('Content-Type', '')

class NextData(HTMLParser):
    def __init__(self): super().__init__(); self.active = False; self.parts = []
    def handle_starttag(self, tag, attrs):
        if tag == 'script' and dict(attrs).get('id') == '__NEXT_DATA__': self.active = True
    def handle_endtag(self, tag):
        if tag == 'script': self.active = False
    def handle_data(self, data):
        if self.active: self.parts.append(data)

def parse_episode(html, page_url):
    parser = NextData(); parser.feed(html)
    try: episode = json.loads(''.join(parser.parts))['props']['pageProps']['episode']
    except (ValueError, KeyError, TypeError):
        raise SourceError('公开 HTML 没有可识别的单集数据；不会回退 API，请用官方 RSS 或本地音频')
    eid = urlparse.urlsplit(page_url).path.rstrip('/').split('/')[-1]
    if episode.get('eid') != eid:
        raise SourceError('页面单集 ID 不匹配，停止，避免下载其他节目')
    media = episode.get('media') or {}
    source = media.get('source') or {}
    if episode.get('isPrivateMedia') is not False or episode.get('payType') != 'FREE' or source.get('mode') != 'PUBLIC':
        raise SourceError('未明确标为公开免费完整音频；付费、私密及需登录内容不处理')
    audio = source.get('url')
    enclosure = episode.get('enclosure') or {}
    if not isinstance(audio, str) or (enclosure.get('url') and enclosure['url'] != audio):
        raise SourceError('页面音频地址缺失或不一致，停止处理')
    validate_url(audio, 'media', resolve=False)
    return {'kind': 'anonymous-public-html', 'source_page': page_url,
            'title': episode.get('title', ''), 'episode_id': eid,
            'duration_seconds': episode.get('duration'), 'audio_url': audio,
            'expected_bytes': media.get('size'), 'mime_type': media.get('mimeType'),
            'evidence': 'isPrivateMedia=false; payType=FREE; media.source.mode=PUBLIC'}

def parse_rss(body, feed_url, selection):
    if b'<!DOCTYPE' in body.upper() or b'<!ENTITY' in body.upper():
        raise SourceError('RSS 含不支持的 XML 声明')
    try: root = ET.fromstring(body)
    except ET.ParseError as exc: raise SourceError('不是有效的公开 RSS；请提供单集网页、RSS 或音频直链') from exc
    items = root.findall('./channel/item')
    matches = [i for i in items if selection is None or i.findtext('guid') == selection or i.findtext('title') == selection]
    if len(matches) != 1:
        names = [i.findtext('title', '') for i in matches[:15]]
        raise SourceError('RSS 必须唯一选中一集，用 --episode 指定完整标题或 GUID。候选：' + json.dumps(names, ensure_ascii=False))
    item = matches[0]; enclosure = item.find('enclosure')
    if enclosure is None or not enclosure.get('url'): raise SourceError('该集没有音频 enclosure')
    audio = urlparse.urljoin(feed_url, enclosure.get('url'))
    validate_url(audio, 'media', resolve=False)
    return {'kind': 'public-rss', 'source_page': item.findtext('link') or feed_url,
            'feed_url': feed_url, 'title': item.findtext('title', ''), 'audio_url': audio,
            'expected_bytes': enclosure.get('length'), 'mime_type': enclosure.get('type')}

def resolve_source(url, selection=None):
    p = urlparse.urlsplit(url)
    if (p.hostname or '').lower() in XYZ:
        # Strip share tracking only from the user-provided page URL, never from media signatures.
        clean = urlparse.urlunsplit(('https', 'www.xiaoyuzhoufm.com', p.path, '', ''))
        validate_url(clean, 'page')
        body, content_type = read_document(clean, 'page')
        if 'html' not in content_type.lower(): raise SourceError('单集地址没有返回 HTML，停止处理')
        return parse_episode(body.decode('utf-8'), clean)
    validate_url(url, 'media')
    if Path(p.path).suffix.lower() in EXTENSIONS:
        return {'kind': 'direct-media', 'source_page': url, 'title': Path(p.path).name, 'audio_url': url}
    body, _ = read_document(url, 'feed')
    return parse_rss(body, url, selection)

def download(info, output_dir):
    output_dir.mkdir(parents=True, exist_ok=True)
    suffix = Path(urlparse.urlsplit(info['audio_url']).path).suffix.lower()
    if suffix not in EXTENSIONS: suffix = '.audio'
    target = output_dir / ('source' + suffix)
    manifest = output_dir / 'source.json'
    if target.exists():
        if manifest.exists():
            previous = json.loads(manifest.read_text(encoding='utf-8'))
            digest_state = hashlib.sha256()
            with target.open('rb') as current:
                for block in iter(lambda: current.read(1024 * 1024), b''): digest_state.update(block)
            digest = digest_state.hexdigest()
            if previous.get('audio_url') == info['audio_url'] and previous.get('sha256') == digest:
                print('已复用校验通过的本地音频：' + str(target)); return previous
        raise SourceError('输出音频已存在但来源或校验不匹配，请使用新的输出目录')
    temporary = output_dir / (target.name + '.part')
    if temporary.exists(): raise SourceError('存在未完成下载，请检查后移走 .part 文件再重试')
    total = 0; digest = hashlib.sha256()
    try:
        with open_public(info['audio_url'], 'media') as response:
            content_type = response.headers.get('Content-Type', '').lower()
            if not (content_type.startswith(('audio/', 'video/')) or 'octet-stream' in content_type):
                raise SourceError('音频地址返回的不是媒体文件，停止处理')
            length = response.headers.get('Content-Length')
            if length and int(length) > MEDIA_LIMIT: raise SourceError('音频超过 1 GiB 限制')
            with temporary.open('xb') as f:
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk: break
                    total += len(chunk)
                    if total > MEDIA_LIMIT: raise SourceError('音频超过 1 GiB 限制')
                    f.write(chunk); digest.update(chunk)
            if total == 0 or (length and total != int(length)): raise SourceError('音频为空或下载未完成')
            expected = info.get('expected_bytes')
            if expected and int(expected) != total: raise SourceError('下载大小与单集声明不一致；保留原源说明并停止')
        os.replace(str(temporary), str(target))
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    result = dict(info, local_file=target.name, bytes=total, sha256=digest.hexdigest())
    manifest.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return result

def main():
    parser = argparse.ArgumentParser(description='偏听则明：匿名公开网页 / RSS / 媒体直链获取音频；永不调用小宇宙 API')
    parser.add_argument('source', help='公开小宇宙单集网页、RSS 或媒体直链')
    parser.add_argument('--output-dir', required=True, type=Path)
    parser.add_argument('--episode', help='RSS 中要下载的完整单集标题或 GUID')
    parser.add_argument('--metadata-only', action='store_true', help='仅解析公开页面，不下载音频')
    args = parser.parse_args()
    info = resolve_source(args.source, args.episode)
    if args.metadata_only:
        args.output_dir.mkdir(parents=True, exist_ok=True)
        manifest = args.output_dir / 'source.json'
        if manifest.exists(): raise SourceError('输出目录已有来源记录，请另选目录以保留下载校验信息')
        manifest.write_text(json.dumps(info, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        print('公开音源已识别；未下载音频')
    else:
        result = download(info, args.output_dir)
        print('音频已保存：' + str(args.output_dir / result['local_file']))
        print('SHA256: ' + result['sha256'])

if __name__ == '__main__':
    try: main()
    except (SourceError, OSError, ValueError) as exc:
        print('错误：' + str(exc), file=sys.stderr); sys.exit(2)
