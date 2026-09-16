#!/usr/bin/env python3
"""Build a personal podcast RSS library from locally produced MP3 files.

The command deliberately only creates files below ``<library-dir>/public`` for
deployment.  Its small state files stay beside that directory and must never
be uploaded with the site.
"""

import argparse
import datetime as dt
import hashlib
import html
import json
import re
import secrets
import shutil
import sys
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import urlparse


ITUNES = "http://www.itunes.com/dtds/podcast-1.0.dtd"
ATOM = "http://www.w3.org/2005/Atom"
PODCAST = "https://podcastindex.org/namespace/1.0"
ET.register_namespace("itunes", ITUNES)
ET.register_namespace("atom", ATOM)
ET.register_namespace("podcast", PODCAST)

EPISODE_ID = re.compile(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?")
TOKEN = re.compile(r"[0-9a-f]{48}")
GUID = re.compile(r"urn:uuid:[0-9a-f-]{36}")
PAGES_ASSET_LIMIT = 25 * 1024 * 1024


def parse_args():
    parser = argparse.ArgumentParser(
        description="将一个本地 MP3 加入个人 RSS 资料库（不会自动发布）。"
    )
    parser.add_argument("--library-dir", required=True, type=Path,
                        help="资料库目录；只上传其中的 public/ 到 Pages")
    parser.add_argument("--base-url", required=True,
                        help="自己的 Pages 地址，例如 https://my-feed.pages.dev")
    parser.add_argument("--audio", required=True, type=Path, help="本地 MP3")
    parser.add_argument("--episode-id", required=True,
                        help="小写 ID，如 episode-001")
    parser.add_argument("--title", required=True)
    parser.add_argument("--source-url", required=True, help="原节目公开出处")
    parser.add_argument("--duration", required=True, type=int, help="剪辑后的秒数")
    return parser.parse_args()


def fail(message):
    raise ValueError(message)


def https_url(value, label, allow_query):
    parsed = urlparse(value)
    if (parsed.scheme != "https" or not parsed.netloc or parsed.username or
            parsed.password or parsed.fragment or (not allow_query and parsed.query)):
        fail("{} 必须是有效的 https URL".format(label))
    return value.rstrip("/")


def read_json(path, fallback):
    if not path.exists():
        return fallback
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        fail("无法读取 {}：{}".format(path.name, exc))


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")


def valid_guid(value):
    return isinstance(value, str) and GUID.fullmatch(value) is not None


def load_state(path):
    state = read_json(path, {})
    if not state:
        return {"token": secrets.token_hex(24), "show_guid": "urn:uuid:" + str(uuid.uuid4())}
    if not isinstance(state, dict) or not TOKEN.fullmatch(state.get("token", "")):
        fail("state.json 的私有路径无效；拒绝覆盖现有资料库")
    if not valid_guid(state.get("show_guid")):
        fail("state.json 的节目 GUID 无效")
    return state


def validate_catalog(catalog):
    if not isinstance(catalog, dict) or not isinstance(catalog.get("episodes"), list):
        fail("catalog.json 的 episodes 必须是数组")
    ids = set()
    for item in catalog["episodes"]:
        if not isinstance(item, dict) or not EPISODE_ID.fullmatch(item.get("episode_id", "")):
            fail("catalog.json 含无效 episode_id")
        if item["episode_id"] in ids:
            fail("catalog.json 含重复 episode_id")
        if not valid_guid(item.get("guid")):
            fail("catalog.json 含无效 GUID")
        ids.add(item["episode_id"])


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_static_root(public, token):
    public.mkdir(parents=True, exist_ok=True)
    (public / "index.html").write_text(
        "<!doctype html><meta charset=\"utf-8\"><meta name=\"robots\" content=\"noindex,nofollow\"><title>未公开</title>\n",
        encoding="utf-8")
    (public / "404.html").write_text(
        "<!doctype html><meta charset=\"utf-8\"><meta name=\"robots\" content=\"noindex,nofollow\"><title>未找到</title>\n",
        encoding="utf-8")
    (public / "robots.txt").write_text("User-agent: *\nDisallow: /\n", encoding="utf-8")
    (public / "_headers").write_text(
        "/*\n  X-Robots-Tag: noindex, nofollow\n  Referrer-Policy: no-referrer\n"
        "/{}/feed.xml\n  Content-Type: application/rss+xml; charset=utf-8\n  Cache-Control: no-cache\n"
        "/{}/*.mp3\n  Content-Type: audio/mpeg\n".format(token, token), encoding="utf-8")


def write_feed(directory, state, catalog, base_url):
    token = state["token"]
    feed_url = "{}/{}/feed.xml".format(base_url, token)
    root = ET.Element("rss", {"version": "2.0"})
    channel = ET.SubElement(root, "channel")
    ET.SubElement(channel, "title").text = "我的播客精剪"
    ET.SubElement(channel, "description").text = "个人收听用的节目原声剪辑。"
    ET.SubElement(channel, "language").text = "zh-CN"
    ET.SubElement(channel, "link").text = "{}/{}/".format(base_url, token)
    ET.SubElement(channel, "{{{}}}author".format(ITUNES)).text = "节目原作者"
    ET.SubElement(channel, "{{{}}}explicit".format(ITUNES)).text = "false"
    ET.SubElement(channel, "{{{}}}block".format(ITUNES)).text = "yes"
    ET.SubElement(channel, "{{{}}}guid".format(PODCAST)).text = state["show_guid"].replace("urn:uuid:", "", 1)
    ET.SubElement(channel, "{{{}}}link".format(ATOM),
                  {"href": feed_url, "rel": "self", "type": "application/rss+xml"})
    for item in reversed(catalog["episodes"]):
        entry = ET.SubElement(channel, "item")
        ET.SubElement(entry, "title").text = item["title"]
        ET.SubElement(entry, "description").text = item["description"]
        ET.SubElement(entry, "link").text = item["source_url"]
        ET.SubElement(entry, "pubDate").text = item["pub_date"]
        ET.SubElement(entry, "enclosure", {
            "url": "{}/{}/{}.mp3".format(base_url, token, item["episode_id"]),
            "length": str(item["bytes"]), "type": "audio/mpeg"})
        ET.SubElement(entry, "guid", {"isPermaLink": "false"}).text = item["guid"]
        ET.SubElement(entry, "{{{}}}duration".format(ITUNES)).text = str(item["duration"])
        ET.SubElement(entry, "{{{}}}explicit".format(ITUNES)).text = "false"
        ET.SubElement(entry, "{{{}}}block".format(ITUNES)).text = "yes"
    ET.indent(root, space="  ")
    directory.joinpath("feed.xml").write_bytes(
        b'<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding="utf-8"))


def write_page(directory, state, catalog, base_url):
    token = state["token"]
    feed_url = "{}/{}/feed.xml".format(base_url, token)
    cards = []
    for item in reversed(catalog["episodes"]):
        cards.append('<article><h2>{}</h2><audio controls preload="metadata" src="{}.mp3"></audio><p>{}</p><p><a href="{}" rel="noreferrer">原节目出处</a></p></article>'.format(
            html.escape(item["title"]), item["episode_id"], html.escape(item["description"]),
            html.escape(item["source_url"], quote=True)))
    template = """<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex,nofollow"><meta name="referrer" content="no-referrer"><title>我的播客精剪</title><style>body{font:16px -apple-system,BlinkMacSystemFont,sans-serif;max-width:42rem;margin:auto;padding:1.25rem;line-height:1.6}audio{width:100%}article{border-top:1px solid #ddd;padding:1rem 0}code{word-break:break-all}</style><body><h1>我的播客精剪</h1><button id="copy">复制订阅链接</button><p><code id="feed">$FEED$</code></p><p>在播客应用中通过 URL 添加节目，然后粘贴上面的链接。</p>$CARDS$<script>document.getElementById('copy').onclick=async function(){let t=document.getElementById('feed').textContent;try{await navigator.clipboard.writeText(t);this.textContent='已复制'}catch(e){this.textContent='请手动复制链接'}};</script></body></html>"""
    directory.joinpath("index.html").write_text(
        template.replace("$FEED$", html.escape(feed_url)).replace("$CARDS$", "".join(cards)),
        encoding="utf-8")


def write_worker(library, state, catalog):
    media = {}
    for item in catalog["episodes"]:
        path = library / "public" / state["token"] / (item["episode_id"] + ".mp3")
        media["/{}/{}.mp3".format(state["token"], item["episode_id"])] = {
            "bytes": path.stat().st_size, "etag": '"{}"'.format(sha256(path))}
    template = Path(__file__).with_name("range_worker.mjs").read_text(encoding="utf-8")
    marker = "const MEDIA = {};"
    if template.count(marker) != 1:
        fail("range_worker.mjs 模板无效")
    (library / "public" / "_worker.js").write_text(
        template.replace(marker, "const MEDIA = {};".format(json.dumps(media, separators=(",", ":"))), 1),
        encoding="utf-8")


def main():
    opt = parse_args()
    base_url = https_url(opt.base_url, "base-url", allow_query=False)
    source_url = https_url(opt.source_url, "source-url", allow_query=True)
    if not EPISODE_ID.fullmatch(opt.episode_id):
        fail("episode-id 只能含小写字母、数字和连字符，且不能以连字符开头或结尾")
    if not opt.audio.is_file() or opt.audio.suffix.lower() != ".mp3":
        fail("audio 必须是存在的 MP3 文件")
    if opt.duration <= 0:
        fail("duration 必须大于 0")
    if opt.audio.stat().st_size > PAGES_ASSET_LIMIT:
        fail("音频超过 Cloudflare Pages 的 25 MiB 单文件上限；资料库未修改，也不会自动降低音质")

    state_path = opt.library_dir / "state.json"
    catalog_path = opt.library_dir / "catalog.json"
    state = load_state(state_path)
    catalog = read_json(catalog_path, {"episodes": []})
    validate_catalog(catalog)
    old_index = next((i for i, item in enumerate(catalog["episodes"])
                      if item["episode_id"] == opt.episode_id), None)
    old = catalog["episodes"][old_index] if old_index is not None else None
    item = {
        "episode_id": opt.episode_id,
        "guid": old["guid"] if old else "urn:uuid:" + str(uuid.uuid4()),
        "title": opt.title,
        "source_url": source_url,
        "duration": opt.duration,
        "description": "节目原声个人剪辑。原节目出处：{}".format(source_url),
        "pub_date": (old or {}).get("pub_date") or dt.datetime.now(dt.timezone.utc).strftime("%a, %d %b %Y %H:%M:%S GMT"),
    }
    # All input validation occurred before this point, so failed size/path checks
    # cannot leave a partially-created library behind.
    target = opt.library_dir / "public" / state["token"]
    target.mkdir(parents=True, exist_ok=True)
    destination = target / (opt.episode_id + ".mp3")
    if opt.audio.resolve() != destination.resolve():
        shutil.copy2(str(opt.audio), str(destination))
    item["bytes"] = destination.stat().st_size
    if old is None:
        catalog["episodes"].append(item)
    else:
        catalog["episodes"][old_index] = item
    write_json(state_path, state)
    write_json(catalog_path, catalog)
    write_static_root(opt.library_dir / "public", state["token"])
    write_feed(target, state, catalog, base_url)
    write_page(target, state, catalog, base_url)
    write_worker(opt.library_dir, state, catalog)
    print("已更新 {}；如需发布，只上传这个目录：{}".format(
        opt.library_dir / "public", opt.library_dir / "public"))


if __name__ == "__main__":
    try:
        main()
    except ValueError as exc:
        print("错误：{}".format(exc), file=sys.stderr)
        sys.exit(2)
