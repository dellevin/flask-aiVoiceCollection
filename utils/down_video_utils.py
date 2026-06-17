# -*- coding: utf-8 -*-
"""
down-video 视频下载工具函数
"""
import os
import re
import subprocess
from urllib.parse import urlparse

from config import TWITTER_COOKIE, BILIBILI_COOKIE, INSTAGRAM_COOKIE, YOUTUBE_COOKIE

# 可选依赖
try:
    import yt_dlp
    YTDLP_AVAILABLE = True
except ImportError:
    YTDLP_AVAILABLE = False


def check_ffmpeg():
    """检查 FFmpeg 是否可用"""
    try:
        result = subprocess.run(['ffmpeg', '-version'], capture_output=True, text=True, timeout=5)
        return result.returncode == 0
    except Exception:
        return False


def check_deno():
    """检查 Deno 是否可用"""
    try:
        result = subprocess.run(['deno', '--version'], capture_output=True, text=True, timeout=5, shell=True)
        if result.returncode == 0:
            return True
    except Exception:
        pass
    # fallback: 检查默认安装路径
    deno_path = os.path.join(os.path.expanduser('~'), '.deno', 'bin', 'deno.exe')
    if os.path.isfile(deno_path):
        try:
            result = subprocess.run([deno_path, '--version'], capture_output=True, text=True, timeout=5)
            return result.returncode == 0
        except Exception:
            pass
    return False


def is_twitter_url(url):
    """检查是否是 Twitter/X 链接"""
    domain = urlparse(url).netloc.lower()
    return 'x.com' in domain or 'twitter.com' in domain


def is_bilibili_url(url):
    """检查是否是 Bilibili 链接"""
    domain = urlparse(url).netloc.lower()
    return 'bilibili.com' in domain or 'b23.tv' in domain


def is_instagram_url(url):
    """检查是否是 Instagram 链接"""
    domain = urlparse(url).netloc.lower()
    return 'instagram.com' in domain


def is_youtube_url(url):
    """检查是否是 YouTube 链接"""
    domain = urlparse(url).netloc.lower()
    return 'youtube.com' in domain or 'youtu.be' in domain


def _clean_title(title):
    """
    清理视频标题，移除特殊字符，使文件名在文件系统中安全可用
    """
    if not title:
        return "video"

    # 移除或替换特殊字符
    # Windows 文件名不允许的字符
    cleaned = re.sub(r'[<>:"/\\|?*]', '_', title)
    # 控制字符
    cleaned = re.sub(r'[\x00-\x1f\x7f]', '', cleaned)
    # 移除 emoji 和其他 Unicode 特殊字符（保留中文、英文、数字、下划线）
    # 匹配非中文、非英文、非数字、非下划线的字符
    cleaned = re.sub(r'[^一-鿿㐀-䶿\w]', '_', cleaned)
    # 将多个连续下划线合并为一个
    cleaned = re.sub(r'_+', '_', cleaned)
    # 移除首尾空格和点（Windows不允许）
    cleaned = cleaned.strip('_. ')
    # 限制长度（保留扩展名空间）
    cleaned = cleaned[:150]
    # 如果清理后为空，使用默认名
    if not cleaned:
        return "video"

    return cleaned


def _find_files_by_title(output_dir, safe_title):
    """根据安全标题查找目录中的相关文件"""
    return [
        f for f in os.listdir(output_dir)
        if f.startswith(safe_title) and os.path.isfile(os.path.join(output_dir, f))
    ]


def _merge_mp4_m4a(output_dir, safe_title):
    """
    查找同名的 .mp4 和 .m4a 并用 FFmpeg 合并。
    返回 (merged: bool, message: str, filepath: str)
    """
    files = _find_files_by_title(output_dir, safe_title)
    mp4_files = [f for f in files if f.endswith('.mp4')]
    m4a_files = [f for f in files if f.endswith('.m4a')]

    if not mp4_files or not m4a_files:
        return False, "未检测到需要合并的 .mp4 + .m4a", ""

    mp4_files.sort(key=len)
    m4a_files.sort(key=len)
    mp4_path = os.path.join(output_dir, mp4_files[0])
    m4a_path = os.path.join(output_dir, m4a_files[0])

    merged_name = safe_title + "_merged.mp4"
    merged_path = os.path.join(output_dir, merged_name)
    counter = 1
    while os.path.exists(merged_path):
        merged_name = f"{safe_title}_merged_{counter}.mp4"
        merged_path = os.path.join(output_dir, merged_name)
        counter += 1

    cmd = ['ffmpeg', '-y', '-i', mp4_path, '-i', m4a_path, '-c', 'copy', merged_path]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if result.returncode == 0 and os.path.exists(merged_path):
            try:
                os.remove(mp4_path)
                os.remove(m4a_path)
            except Exception:
                pass
            return True, f"已合并为 {merged_name}", merged_path
        else:
            return False, f"FFmpeg 合并失败: {result.stderr[:200]}", ""
    except Exception as e:
        return False, f"合并异常: {str(e)}", ""


class DownloadProgressLogger:
    """捕获yt-dlp下载进度的回调类"""

    def __init__(self, progress_callback=None):
        self.progress_callback = progress_callback

    def debug(self, msg):
        pass

    def warning(self, msg):
        pass

    def error(self, msg):
        if self.progress_callback:
            self.progress_callback({'type': 'error', 'message': msg})

    def info(self, msg):
        if self.progress_callback:
            self.progress_callback({'type': 'info', 'message': msg})

    def download_progress(self, d):
        if d['status'] == 'downloading' and self.progress_callback:
            percent = d.get('_percent_str', '0%').replace('%', '').strip()
            try:
                percent = float(percent)
            except ValueError:
                percent = 0
            speed = d.get('_speed_str', 'N/A')
            eta = d.get('_eta_str', 'N/A')
            filename = d.get('filename', '').split('\\')[-1].split('/')[-1]
            self.progress_callback({
                'type': 'progress',
                'percent': percent,
                'speed': speed,
                'eta': eta,
                'filename': filename
            })
        elif d['status'] == 'finished' and self.progress_callback:
            self.progress_callback({
                'type': 'progress',
                'percent': 100,
                'speed': '',
                'eta': '',
                'filename': ''
            })


def download_video(video_url, output_dir, platform, progress_callback=None, proxy_url=None):
    """
    通用下载函数，返回 (success: bool, message: str, title: str, filepath: str)
    支持进度回调
    """
    if not YTDLP_AVAILABLE:
        return False, "yt-dlp 未安装，视频下载功能不可用。", "", ""

    os.makedirs(output_dir, exist_ok=True)

    # 先提取视频信息以获取标题
    temp_opts = {
        'quiet': True,
        'no_warnings': True,
        'skip_download': True,
    }

    if platform == 'twitter':
        cookie_file = TWITTER_COOKIE
        if proxy_url:
            temp_opts['proxy'] = proxy_url
    elif platform == 'bilibili':
        cookie_file = BILIBILI_COOKIE
    elif platform == 'instagram':
        cookie_file = INSTAGRAM_COOKIE
        if proxy_url:
            temp_opts['proxy'] = proxy_url
    elif platform == 'youtube':
        cookie_file = YOUTUBE_COOKIE
        if proxy_url:
            temp_opts['proxy'] = proxy_url
    else:
        return False, "不支持的平台", "", ""

    if os.path.exists(cookie_file):
        temp_opts['cookiefile'] = cookie_file
    else:
        return False, f"Cookie 文件 '{cookie_file}' 未找到", "", ""

    try:
        # 创建进度日志器
        progress_logger = DownloadProgressLogger(progress_callback)

        # 获取视频信息
        with yt_dlp.YoutubeDL(temp_opts) as ydl:
            info = ydl.extract_info(video_url, download=False)
            if not info:
                return False, "未能获取视频信息", "", ""

        # 清理标题作为安全文件名
        title = info.get('title', 'Unknown Title')
        safe_title = _clean_title(title)

        # 使用清理后的标题构建输出模板
        ydl_opts = {
            'outtmpl': os.path.join(output_dir, f'{safe_title}.%(ext)s'),
            'ignoreerrors': True,
            'progress_hooks': [progress_logger.download_progress] if progress_callback else [],
            'logger': progress_logger if progress_callback else None,
            'format': 'bestvideo+bestaudio/best',
        }

        if platform == 'twitter':
            if proxy_url:
                ydl_opts['proxy'] = proxy_url
            ydl_opts['merge_output_format'] = 'mp4'
            ydl_opts['postprocessors'] = [{
                'key': 'FFmpegVideoConvertor',
                'preferedformat': 'mp4',
            }]
        elif platform == 'bilibili':
            ydl_opts['merge_output_format'] = 'mp4'
        elif platform == 'instagram':
            if proxy_url:
                ydl_opts['proxy'] = proxy_url
            ydl_opts['merge_output_format'] = 'mp4'
        elif platform == 'youtube':
            if proxy_url:
                ydl_opts['proxy'] = proxy_url
            ydl_opts['merge_output_format'] = 'mp4'

        if os.path.exists(cookie_file):
            ydl_opts['cookiefile'] = cookie_file

        # 执行下载
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([video_url])

        # 查找下载的文件
        files = _find_files_by_title(output_dir, safe_title)

        if not files:
            return False, "下载完成但未找到输出文件", title, ""

        filepath = ""
        candidate_files = [f for f in files if f.endswith('.mp4')]
        if candidate_files:
            merged_candidates = [f for f in candidate_files if '_merged' in f]
            if merged_candidates:
                filepath = os.path.join(output_dir, merged_candidates[0])
            else:
                candidate_files.sort(key=lambda f: os.path.getsize(os.path.join(output_dir, f)), reverse=True)
                filepath = os.path.join(output_dir, candidate_files[0])
        else:
            files.sort(key=lambda f: os.path.getsize(os.path.join(output_dir, f)), reverse=True)
            filepath = os.path.join(output_dir, files[0])

        extra_msg = ""
        if platform in ('bilibili', 'youtube'):
            has_mp4 = any(f.endswith('.mp4') for f in files)
            has_m4a = any(f.endswith('.m4a') for f in files)
            if has_mp4 and has_m4a:
                merged, merge_msg, merged_path = _merge_mp4_m4a(output_dir, safe_title)
                extra_msg = f" ({merge_msg})" if merge_msg else ""
                if merged and merged_path:
                    filepath = merged_path

        return True, f"下载成功！保存到 {output_dir}{extra_msg}", title, filepath

    except Exception as e:
        return False, f"下载失败: {str(e)}", "", ""
