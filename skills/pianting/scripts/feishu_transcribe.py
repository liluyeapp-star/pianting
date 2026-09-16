#!/usr/bin/env python3
"""Upload lawful local audio/video to Feishu Minutes and export its transcript.

This wrapper deliberately has no downloader.  It only accepts a local media
file supplied by the person running it, then asks their already-configured
``lark-cli`` to upload it to their own Drive and create a Minutes item.

Examples:
  python3 feishu_transcribe.py transcribe episode.mp3 --title "第 12 期" \
    --work-dir ./work/episode-12 --output ./out/episode-12-feishu.txt
  python3 feishu_transcribe.py resume https://example.feishu.cn/minutes/obcnxxx \
    --work-dir ./work/episode-12 --output ./out/episode-12-feishu.txt
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.parse
from pathlib import Path
from typing import Any, Dict, Iterable, Optional, Sequence, Tuple


SUPPORTED_EXTENSIONS = {
    ".wav", ".mp3", ".m4a", ".aac", ".ogg", ".wma", ".amr",
    ".avi", ".wmv", ".mov", ".mp4", ".m4v", ".mpeg", ".flv",
}
MAX_BYTES = 6 * 1024 * 1024 * 1024
STATE_NAME = "feishu-transcribe-state.json"


class ToolError(RuntimeError):
    pass


class LarkError(ToolError):
    """A CLI error with whether the server definitely rejected the request."""

    def __init__(self, message: str, definitely_rejected: bool = False):
        super().__init__(message)
        self.definitely_rejected = definitely_rejected


def json_from_output(text: str) -> Dict[str, Any]:
    """Read a CLI JSON envelope even when a notifier wrote surrounding text."""
    decoder = json.JSONDecoder()
    for match in re.finditer(r"\{", text):
        try:
            value, _ = decoder.raw_decode(text[match.start():])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    return {}


def nested_values(value: Any, key: str) -> Iterable[Any]:
    if isinstance(value, dict):
        for current_key, current_value in value.items():
            if current_key == key:
                yield current_value
            yield from nested_values(current_value, key)
    elif isinstance(value, list):
        for item in value:
            yield from nested_values(item, key)


def first_nested(value: Any, keys: Sequence[str]) -> Optional[Any]:
    for key in keys:
        for found in nested_values(value, key):
            if found not in (None, "", []):
                return found
    return None


def safe_name(value: str) -> str:
    value = re.sub(r'[\\/:*?"<>|]', " ", value)
    value = re.sub(r"\s+", " ", value).strip(" .")
    return (value or "feishu-transcript")[:120]


def minute_token(value: str) -> str:
    parsed = urllib.parse.urlparse(value)
    candidate = parsed.path.rstrip("/").split("/")[-1] if parsed.scheme else value
    candidate = candidate.split("?", 1)[0].strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{12,80}", candidate):
        raise ToolError("无法识别妙记 token；请传入完整妙记链接或 token")
    return candidate


def state_path(work_dir: Path) -> Path:
    return work_dir / STATE_NAME


def load_state(work_dir: Path) -> Dict[str, Any]:
    path = state_path(work_dir)
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ToolError("状态文件不是合法 JSON：{}".format(path)) from error
    if not isinstance(value, dict):
        raise ToolError("状态文件格式无效：{}".format(path))
    return value


def save_state(work_dir: Path, state: Dict[str, Any]) -> None:
    work_dir.mkdir(parents=True, exist_ok=True)
    path = state_path(work_dir)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(str(temporary), str(path))


def cli_program() -> str:
    configured = os.environ.get("LARK_CLI")
    if configured:
        return configured
    found = shutil.which("lark-cli")
    if not found:
        raise ToolError("找不到 lark-cli。请先按 技能内 references/飞书配置.md 完成安装和授权，再重试。")
    return found


def run_lark(arguments: Sequence[str], cwd: Path) -> Tuple[Dict[str, Any], subprocess.CompletedProcess]:
    try:
        process = subprocess.run(
            [cli_program(), *arguments], cwd=str(cwd), capture_output=True, text=True, timeout=900
        )
    except subprocess.TimeoutExpired as error:
        raise ToolError("lark-cli 请求超时，服务器端是否已接收本次请求无法确定。请不要盲目重试写入操作。") from error
    combined = "\n".join(part for part in (process.stdout, process.stderr) if part)
    payload = json_from_output(combined)
    if process.returncode != 0 or payload.get("ok") is False:
        raw_error = payload.get("error") if isinstance(payload, dict) else None
        if isinstance(raw_error, dict):
            message = raw_error.get("message") or raw_error.get("hint") or combined.strip() or "lark-cli 调用失败"
            error_text = " ".join(str(raw_error.get(key, "")) for key in ("type", "subtype", "message", "hint", "code", "missing_scopes"))
        elif isinstance(raw_error, str):
            message = raw_error or combined.strip() or "lark-cli 调用失败"
            error_text = raw_error
        else:
            message = combined.strip() or "lark-cli 调用失败"
            error_text = combined
        lower = error_text.lower()
        rejected_for_auth = any(marker in lower for marker in (
            "missing_scope", "missing scope", "permission_denied", "permission denied",
            "unauthorized", "authorization", "forbidden", "auth", "scope", "权限不足", "无权限", "未授权",
        ))
        if rejected_for_auth:
            message = "飞书认证或权限不足：{}。本工具不会自动登录；请按 技能内 references/飞书配置.md 手动完成授权后重试。".format(message)
        raise LarkError(str(message), definitely_rejected=rejected_for_auth)
    if payload.get("ok") is not True:
        raise ToolError("lark-cli 没有返回可确认的成功结果")
    return payload, process


def validate_media(media: Path) -> Path:
    media = media.expanduser().resolve()
    if not media.is_file():
        raise ToolError("找不到本地音视频：{}".format(media))
    if media.suffix.lower() not in SUPPORTED_EXTENSIONS:
        raise ToolError("飞书妙记不支持该格式：{}".format(media.suffix or "无扩展名"))
    if media.stat().st_size > MAX_BYTES:
        raise ToolError("音视频超过飞书妙记 6GB 上限")
    return media


def media_fingerprint(media: Path) -> Tuple[int, str]:
    """A content fingerprint prevents a replaced file from reusing its state."""
    digest = hashlib.sha256()
    with media.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return media.stat().st_size, digest.hexdigest()


def ensure_same_input(state: Dict[str, Any], media: Path, size: int, sha256: str) -> None:
    previous = state.get("input_path")
    if previous and Path(str(previous)).resolve() != media.resolve():
        raise ToolError("此 --work-dir 已关联另一份音频。请为新节目使用新的 --work-dir，避免误复用上传状态。")
    if previous and (state.get("input_bytes") != size or state.get("input_sha256") != sha256):
        raise ToolError("该路径下的音频内容已经变化。请为新版文件使用新的 --work-dir，避免复用旧的飞书上传状态。")


def make_minute(media: Path, title: str, work_dir: Path) -> Dict[str, Any]:
    media = media.resolve()
    state = load_state(work_dir)
    size, sha256 = media_fingerprint(media)
    ensure_same_input(state, media, size, sha256)
    state.setdefault("input_path", str(media))
    state.setdefault("input_bytes", size)
    state.setdefault("input_sha256", sha256)
    state.setdefault("title", title)
    state.setdefault("created_at", time.strftime("%Y-%m-%dT%H:%M:%S%z"))

    if state.get("minute_token"):
        return state
    if state.get("stage") in {"minute_creation_started", "minute_creation_unknown"}:
        raise ToolError(
            "上次创建妙记时没有取得 minute_token，无法安全重试以免重复创建。"
            "请在飞书妙记中找到该条目后，用 resume <妙记链接> 继续。"
        )

    file_token = state.get("file_token")
    if not file_token:
        if state.get("stage") == "drive_upload_started":
            raise ToolError(
                "上次上传云空间时中断，服务器端是否已收到文件无法确定。"
                "请在飞书云空间确认后，以 resume-upload --file-token <file_token> 接续；不要盲目重传。"
            )
        # lark-cli only accepts relative paths for --file.  Running in the
        # media directory keeps the wrapper portable without copying the file.
        state["stage"] = "drive_upload_started"
        save_state(work_dir, state)
        try:
            payload, _ = run_lark(
                ["drive", "+upload", "--as", "user", "--file", "./" + media.name,
                 "--name", title + media.suffix.lower(), "--format", "json"], media.parent
            )
        except LarkError as error:
            if error.definitely_rejected:
                # The API rejected this request before accepting an upload, so
                # after the user grants the stated scope the same command is safe.
                state["stage"] = "ready_to_upload"
                save_state(work_dir, state)
            raise
        file_token = first_nested(payload, ["file_token"])
        if not isinstance(file_token, str):
            raise ToolError("飞书云空间上传完成，但没有返回 file_token；无法安全继续。")
        state.update({"file_token": file_token, "stage": "drive_uploaded"})
        save_state(work_dir, state)

    # Persist intent *before* the write. If the process is interrupted after
    # the server accepts it, a blind retry could create a duplicate Minute.
    state["stage"] = "minute_creation_started"
    save_state(work_dir, state)
    try:
        payload, _ = run_lark([
            "minutes", "+upload", "--as", "user", "--file-token", str(file_token), "--format", "json",
        ], work_dir)
    except LarkError as error:
        # A permission/auth rejection did not create a Minute. Keep the Drive
        # token and allow a retry after the user authorizes the missing scope.
        state["stage"] = "drive_uploaded" if error.definitely_rejected else "minute_creation_unknown"
        save_state(work_dir, state)
        raise
    token = first_nested(payload, ["minute_token"])
    url = first_nested(payload, ["minute_url", "url"])
    if not isinstance(token, str) and isinstance(url, str):
        token = minute_token(url)
    if not isinstance(token, str):
        state["stage"] = "minute_creation_unknown"
        save_state(work_dir, state)
        raise ToolError("妙记创建请求返回后没有 minute_token；请在飞书妙记中确认后用 resume 继续。")
    state.update({"minute_token": token, "minute_url": url, "stage": "minute_created"})
    save_state(work_dir, state)
    return state


def find_transcript(payload: Dict[str, Any], work_dir: Path) -> Optional[Path]:
    for value in nested_values(payload, "transcript_file"):
        if not isinstance(value, str):
            continue
        candidate = Path(value)
        if not candidate.is_absolute():
            candidate = work_dir / candidate
        if candidate.is_file() and candidate.stat().st_size:
            return candidate.resolve()
    candidates = sorted((work_dir / "transcript").glob("**/transcript.txt"), key=lambda item: item.stat().st_mtime, reverse=True)
    return candidates[0].resolve() if candidates else None


def export_transcript(state: Dict[str, Any], work_dir: Path, output: Path, timeout: int, interval: int) -> Dict[str, Any]:
    token = state.get("minute_token")
    if not isinstance(token, str):
        raise ToolError("状态中没有 minute_token；请用 resume <妙记链接> 继续。")
    if timeout <= 0 or interval <= 0:
        raise ToolError("timeout 和 interval 必须大于 0")
    transcript_dir = work_dir / "transcript"
    transcript_dir.mkdir(parents=True, exist_ok=True)
    deadline = time.time() + timeout
    last_error = ""
    while time.time() < deadline:
        try:
            payload, _ = run_lark([
                "minutes", "+detail", "--as", "user", "--minute-tokens", token,
                "--transcript", "--overwrite", "--output-dir", "./transcript", "--format", "json",
            ], work_dir)
            transcript = find_transcript(payload, work_dir)
            if transcript:
                output = output.expanduser().resolve()
                output.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(str(transcript), str(output))
                state.update({"stage": "transcript_exported", "transcript_file": str(transcript), "output": str(output)})
                save_state(work_dir, state)
                return {"ok": True, "minute_token": token, "minute_url": state.get("minute_url"), "output": str(output)}
            last_error = "妙记尚未生成逐字稿"
        except ToolError as error:
            # Creation is asynchronous. Keep polling ordinary not-ready errors,
            # but never mask authentication/permission failures.
            text = str(error)
            if "认证或权限不足" in text or "quota" in text.lower() or "额度" in text:
                raise
            last_error = text
        time.sleep(interval)
    raise ToolError("等待逐字稿超时（{} 秒）：{}。稍后可用同一 --work-dir 执行 resume 继续。".format(timeout, last_error))


def command_transcribe(args: argparse.Namespace) -> Dict[str, Any]:
    media = validate_media(Path(args.input))
    title = safe_name(args.title or media.stem)
    state = make_minute(media, title, args.work_dir)
    return export_transcript(state, args.work_dir, args.output, args.timeout, args.interval)


def command_resume(args: argparse.Namespace) -> Dict[str, Any]:
    token = minute_token(args.minute)
    state = load_state(args.work_dir)
    known = state.get("minute_token")
    if known and known != token:
        raise ToolError("此 --work-dir 已关联另一条妙记。请使用原来的妙记链接，或为它新建 --work-dir。")
    state.update({"minute_token": token, "minute_url": args.minute if urllib.parse.urlparse(args.minute).scheme else state.get("minute_url"), "stage": "minute_created"})
    save_state(args.work_dir, state)
    return export_transcript(state, args.work_dir, args.output, args.timeout, args.interval)


def command_resume_upload(args: argparse.Namespace) -> Dict[str, Any]:
    media = validate_media(Path(args.input))
    size, sha256 = media_fingerprint(media)
    state = load_state(args.work_dir)
    ensure_same_input(state, media, size, sha256)
    if state.get("minute_token"):
        raise ToolError("此 --work-dir 已有 minute_token；请使用 resume <妙记链接>，不要重新创建。")
    state.update({
        "input_path": str(media), "input_bytes": size, "input_sha256": sha256,
        "title": safe_name(args.title or state.get("title") or media.stem),
        "file_token": args.file_token, "stage": "drive_uploaded",
    })
    save_state(args.work_dir, state)
    state = make_minute(media, state["title"], args.work_dir)
    return export_transcript(state, args.work_dir, args.output, args.timeout, args.interval)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    for name, help_text in (("transcribe", "上传本地音视频并导出逐字稿"), ("resume", "从已有妙记继续导出逐字稿"), ("resume-upload", "用已确认的 Drive file_token 继续创建妙记")):
        child = commands.add_parser(name, help=help_text)
        if name in {"transcribe", "resume-upload"}:
            child.add_argument("input", help="用户合法取得的本地音视频文件")
            child.add_argument("--title", help="飞书妙记标题，默认文件名")
            if name == "resume-upload":
                child.add_argument("--file-token", required=True, help="已在飞书云空间确认存在的文件 token")
        else:
            child.add_argument("minute", help="妙记 URL 或 minute_token")
        child.add_argument("--work-dir", required=True, type=Path, help="本次任务的状态与临时文件目录")
        child.add_argument("--output", required=True, type=Path, help="导出的飞书 TXT 逐字稿路径")
        child.add_argument("--timeout", type=int, default=1800, help="等待逐字稿的最长秒数，默认 1800")
        child.add_argument("--interval", type=int, default=20, help="轮询间隔秒数，默认 20")
    return parser


def main() -> int:
    try:
        args = build_parser().parse_args()
        if args.command == "transcribe":
            result = command_transcribe(args)
        elif args.command == "resume-upload":
            result = command_resume_upload(args)
        else:
            result = command_resume(args)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (ToolError, OSError, subprocess.SubprocessError) as error:
        print(json.dumps({"ok": False, "error": str(error)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
