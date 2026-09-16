#!/usr/bin/env python3
"""Create a coarse keep-segments list from a Feishu transcript text export."""

import argparse
import json
import math
import re
import sys
from pathlib import Path


# Feishu mixes identified names (for example, 李雨白) with anonymous labels
# such as Speaker 1.  A header is therefore any non-empty name followed by its
# millisecond timestamp on an otherwise separate line.
HEADER = re.compile(r"^(.+?)\s+(\d{1,2}:\d{2}:\d{2}\.\d{3})\s*$")


def parse_time(value):
    hours, minutes, seconds = value.split(":")
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def arguments():
    parser = argparse.ArgumentParser(description="从飞书逐字稿 TXT 粗略生成某位说话人的保留片段")
    parser.add_argument("--transcript", required=True, type=Path, help="飞书导出的 TXT 逐字稿")
    parser.add_argument("--speaker", required=True, help="要保留的说话人，如 Speaker 3")
    parser.add_argument("--duration", required=True, type=float, help="原始音频总时长（秒）")
    parser.add_argument("--output", required=True, type=Path, help="输出 JSON 文件")
    return parser.parse_args()


def error(message):
    raise ValueError(message)


def read_blocks(path):
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except FileNotFoundError:
        error(f"找不到逐字稿：{path}")
    headers = []
    for index, line in enumerate(lines):
        match = HEADER.match(line.strip())
        if match:
            headers.append((index, match.group(1).strip(), parse_time(match.group(2))))
    if not headers:
        error("未识别到飞书逐字稿说话人段落；需要形如 `姓名 00:02:19.630` 或 `Speaker 3 00:02:19.630` 的独立行")
    blocks = []
    for number, (index, speaker, start) in enumerate(headers):
        next_index = headers[number + 1][0] if number + 1 < len(headers) else len(lines)
        # Keep every text line through the next header, including intentional
        # line breaks, while trimming only outer blank lines from the block.
        text = "\n".join(lines[index + 1:next_index]).strip()
        blocks.append({"speaker": speaker, "start": start, "text": text})
    return blocks


def main():
    args = arguments()
    if not math.isfinite(args.duration) or args.duration <= 0:
        error("duration 必须大于 0")
    blocks = read_blocks(args.transcript)
    if not any(block["speaker"] == args.speaker for block in blocks):
        error(f"逐字稿中未找到说话人：{args.speaker}")
    if any(block["start"] >= args.duration for block in blocks):
        error("逐字稿起点超出原始音频时长")
    result = []
    for index, block in enumerate(blocks):
        end = blocks[index + 1]["start"] if index + 1 < len(blocks) else args.duration
        if end <= block["start"]:
            error("说话人段落起点未按时间递增，无法估计结束点")
        if block["speaker"] != args.speaker:
            continue
        # Consecutive target blocks have no intervening speaker, so keep them together.
        if index > 0 and blocks[index - 1]["speaker"] == args.speaker and result:
            result[-1]["end"] = end
            result[-1]["text"] = "\n".join(filter(None, [result[-1]["text"], block["text"]]))
        else:
            result.append({"start": block["start"], "end": end, "text": block["text"], "quality": "coarse"})
    if not result:
        error(f"未生成任何 {args.speaker} 片段")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"已生成 {len(result)} 个粗略片段：{args.output}")
    print("注意：结束点按下一个任意说话人段落起点估计，必须结合原音频人工核对边界。", file=sys.stderr)


if __name__ == "__main__":
    try:
        main()
    except ValueError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        sys.exit(2)
