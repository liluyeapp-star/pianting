#!/usr/bin/env python3
"""Render selected spoken-audio clips from an original recording.

``segments`` is a JSON array of ordered, non-overlapping objects with seconds
in the original recording: [{"start": 12.5, "end": 34.2, "text": "optional"}].
This renderer only follows that approved list; it does not identify speakers.
"""

import argparse
import csv
import json
import math
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
import soundfile as sf


SAMPLE_RATE = 48_000


def arguments():
    parser = argparse.ArgumentParser(
        description="按已审核的片段清单渲染谈话音频；输入会转换为 48kHz 单声道。"
    )
    parser.add_argument("--source", required=True, type=Path, help="原始音频文件")
    parser.add_argument("--segments", required=True, type=Path, help="保留片段 JSON 文件")
    parser.add_argument("--output-dir", required=True, type=Path, help="输出 guest.mp3、preview.mp3 和时间映射.csv 的目录")
    parser.add_argument("--overwrite", action="store_true", help="允许覆盖输出目录中的既有交付文件")
    parser.add_argument("--title", help="写入 MP3 title 元数据（可选）")
    parser.add_argument("--artist", help="写入 MP3 artist 元数据（可选）")
    parser.add_argument("--work-dir", type=Path, default=Path.cwd() / "work", help="临时文件目录，默认当前目录下 work/")
    parser.add_argument("--gap-ms", type=float, default=200, help="片段间静音毫秒数，默认 200")
    parser.add_argument("--fade-ms", type=float, default=4, help="片段边缘淡入淡出毫秒数，默认 4")
    parser.add_argument("--preview-seconds", type=float, default=300, help="试听目标秒数，默认 300")
    return parser.parse_args()


def invalid(message):
    raise ValueError(message)


def ffmpeg(command):
    try:
        subprocess.run(command, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    except FileNotFoundError:
        invalid("未找到 ffmpeg，请安装后重试")
    except subprocess.CalledProcessError as exc:
        invalid(f"FFmpeg 处理失败：{exc.stderr.strip()}")


def read_segments(path, source_frames):
    try:
        entries = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        invalid(f"找不到片段清单：{path}")
    except json.JSONDecodeError as exc:
        invalid(f"片段清单不是合法 JSON：{exc}")
    if not isinstance(entries, list) or not entries:
        invalid("片段清单必须是非空 JSON 数组")

    clips, previous_end = [], 0
    for number, entry in enumerate(entries, 1):
        if not isinstance(entry, dict):
            invalid(f"第 {number} 项必须是对象")
        try:
            start_seconds, end_seconds = float(entry["start"]), float(entry["end"])
        except (KeyError, TypeError, ValueError):
            invalid(f"第 {number} 项需要数字 start 和 end")
        if not math.isfinite(start_seconds) or not math.isfinite(end_seconds):
            invalid(f"第 {number} 项的时间必须是有限数字")
        start, end = round(start_seconds * SAMPLE_RATE), round(end_seconds * SAMPLE_RATE)
        if start < 0 or end <= start or end > source_frames:
            invalid(f"第 {number} 项超出原音频范围或时长无效：{start_seconds}–{end_seconds}")
        if start < previous_end:
            invalid(f"第 {number} 项与前一项重叠或未按时间排序")
        text = entry.get("text", "")
        if text is not None and not isinstance(text, str):
            invalid(f"第 {number} 项的 text 必须是字符串")
        clips.append((start, end, text or ""))
        previous_end = end
    return clips


def encode(wav, destination, title, artist):
    command = ["ffmpeg", "-nostdin", "-y", "-i", str(wav), "-c:a", "libmp3lame", "-b:a", "128k"]
    if title:
        command.extend(["-metadata", f"title={title}"])
    if artist:
        command.extend(["-metadata", f"artist={artist}"])
    ffmpeg(command + [str(destination)])


def write_map(destination, rows):
    with destination.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.writer(file)
        writer.writerow(["输出开始秒", "输出结束秒", "原音频开始秒", "原音频结束秒", "说明或文本"])
        writer.writerows(rows)


def main():
    args = arguments()
    if not all(math.isfinite(v) for v in (args.gap_ms, args.fade_ms, args.preview_seconds)) or args.gap_ms < 0 or args.fade_ms < 0 or args.preview_seconds <= 0:
        invalid("gap-ms、fade-ms 不能为负，preview-seconds 必须大于 0")
    if not args.source.is_file():
        invalid(f"找不到原始音频：{args.source}")
    args.work_dir.mkdir(parents=True, exist_ok=True)
    args.output_dir.mkdir(parents=True, exist_ok=True)

    for name in ("guest.mp3", "preview.mp3", "时间映射.csv"):
        target_file = args.output_dir / name
        if target_file.resolve() == args.source.resolve():
            invalid("输出文件不能与原音频相同")
        if target_file.exists() and not args.overwrite:
            invalid("输出文件已存在，请换目录或明确使用 --overwrite：" + str(target_file))

    with tempfile.TemporaryDirectory(prefix="guest_render_", dir=args.work_dir) as directory:
        temporary = Path(directory)
        decoded, full_wav, preview_wav = temporary / "source.wav", temporary / "guest.wav", temporary / "preview.wav"
        ffmpeg(["ffmpeg", "-nostdin", "-y", "-i", str(args.source), "-ar", str(SAMPLE_RATE), "-ac", "1", "-c:a", "pcm_s16le", str(decoded)])
        source_info = sf.info(decoded)
        if source_info.samplerate != SAMPLE_RATE or source_info.channels != 1:
            invalid("音频无法解码为 48kHz 单声道")
        clips = read_segments(args.segments, source_info.frames)
        gap_frames = round(args.gap_ms * SAMPLE_RATE / 1000)
        fade_frames = round(args.fade_ms * SAMPLE_RATE / 1000)
        rows, clip_ends, output_start = [], [], 0
        with sf.SoundFile(decoded) as source, sf.SoundFile(full_wav, mode="w", samplerate=SAMPLE_RATE, channels=1, subtype="PCM_16") as output:
            for index, (start, end, text) in enumerate(clips):
                length = end - start
                fade = min(fade_frames, length // 2)
                source.seek(start)
                position = 0
                while position < length:
                    clip = source.read(min(65536, length - position), dtype="float32")
                    if not len(clip): invalid("原音频读取中断")
                    if fade:
                        frame = np.arange(position, position + len(clip))
                        gain = np.minimum(np.minimum(frame / fade, (length - 1 - frame) / fade), 1.0)
                        clip *= np.maximum(gain, 0)
                    output.write(clip)
                    position += len(clip)
                output_end = output_start + length
                rows.append((f"{output_start / SAMPLE_RATE:.6f}", f"{output_end / SAMPLE_RATE:.6f}",
                             f"{start / SAMPLE_RATE:.6f}", f"{end / SAMPLE_RATE:.6f}", text))
                clip_ends.append(output_end)
                output_start = output_end
                if index < len(clips) - 1 and gap_frames:
                    remaining = gap_frames
                    while remaining:
                        count = min(65536, remaining)
                        output.write(np.zeros(count, dtype="float32")); remaining -= count
                    gap_end = output_start + gap_frames
                    rows.append((f"{output_start / SAMPLE_RATE:.6f}", f"{gap_end / SAMPLE_RATE:.6f}", "", "", "片段间隔"))
                    output_start = gap_end
        target = round(args.preview_seconds * SAMPLE_RATE)
        preview_end = min(clip_ends, key=lambda frame: abs(frame - target))
        with sf.SoundFile(full_wav) as full, sf.SoundFile(preview_wav, mode="w", samplerate=SAMPLE_RATE, channels=1, subtype="PCM_16") as preview:
            remaining = preview_end
            while remaining:
                samples = full.read(min(65536, remaining), dtype="float32")
                preview.write(samples); remaining -= len(samples)
        encode(full_wav, args.output_dir / "guest.mp3", args.title, args.artist)
        encode(preview_wav, args.output_dir / "preview.mp3", args.title, args.artist)
        write_map(args.output_dir / "时间映射.csv", rows)

    print(f"已生成：{args.output_dir / 'guest.mp3'}")
    print(f"已生成：{args.output_dir / 'preview.mp3'}（{preview_end / SAMPLE_RATE:.3f} 秒）")
    print(f"已生成：{args.output_dir / '时间映射.csv'}")


if __name__ == "__main__":
    try:
        main()
    except ValueError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        sys.exit(2)
