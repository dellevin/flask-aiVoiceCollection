# -*- coding: utf-8 -*-
"""
down-video 视频下载蓝图
"""
import os
import json
import queue
from flask import Blueprint, render_template, request, jsonify, send_file, Response

from config import DEFAULT_OUTPUT_DIR, PROXY_URL
from utils.down_video_utils import (
    check_ffmpeg,
    check_deno,
    is_twitter_url,
    is_bilibili_url,
    is_instagram_url,
    is_youtube_url,
    download_video,
)
from utils.stats_db import add_recent_path, get_recent_paths, delete_recent_path, clear_recent_paths

bp = Blueprint('dv_cookies', __name__, url_prefix='/down-video')

# 用于存储下载进度的队列
progress_queues = {}


@bp.route('/')
def page():
    ffmpeg_ok = check_ffmpeg()
    deno_ok = check_deno()
    return render_template('down_video.html', ffmpeg_ok=ffmpeg_ok, deno_ok=deno_ok)


@bp.route('/file')
def serve_file():
    filepath = request.args.get("path", "")
    as_download = request.args.get("download", "0") == "1"
    if not filepath:
        return "Missing path", 400

    filepath = os.path.abspath(filepath)
    if not os.path.exists(filepath) or not os.path.isfile(filepath):
        return "File not found", 404

    ext = os.path.splitext(filepath)[1].lower()
    if ext not in ('.mp4', '.m4a', '.webm', '.mkv', '.mov', '.avi'):
        return "File type not allowed", 403

    mime_types = {
        '.mp4': 'video/mp4',
        '.m4a': 'audio/mp4',
        '.webm': 'video/webm',
        '.mkv': 'video/x-matroska',
        '.mov': 'video/quicktime',
        '.avi': 'video/x-msvideo',
    }
    mimetype = mime_types.get(ext, 'application/octet-stream')

    response = send_file(
        filepath,
        mimetype=mimetype,
        conditional=True,
    )
    response.headers['Accept-Ranges'] = 'bytes'
    if not as_download:
        response.headers['Content-Disposition'] = 'inline'
    return response


@bp.route('/download', methods=["POST"])
def download():
    data = request.get_json()
    url = data.get("url", "").strip() if data else ""
    output_dir = data.get("output_dir", DEFAULT_OUTPUT_DIR).strip() if data else DEFAULT_OUTPUT_DIR
    download_id = data.get("download_id", "") if data else ""
    use_proxy = data.get("use_proxy", False) if data else False
    proxy_url_input = (data.get("proxy_url") or "").strip() if data else ""

    if not url:
        return jsonify({"success": False, "message": "请输入视频链接"}), 400
    if is_twitter_url(url):
        platform = 'twitter'
    elif is_bilibili_url(url):
        platform = 'bilibili'
    elif is_instagram_url(url):
        platform = 'instagram'
    elif is_youtube_url(url):
        platform = 'youtube'
    else:
        return jsonify({"success": False, "message": "仅支持 Twitter/X、Bilibili 和 Instagram 视频链接"}), 400
    if not output_dir:
        output_dir = DEFAULT_OUTPUT_DIR

    # 确定使用的代理地址
    proxy_url = proxy_url_input if use_proxy and proxy_url_input else (PROXY_URL if use_proxy else None)

    # 创建进度队列
    progress_queue = queue.Queue()
    if download_id:
        progress_queues[download_id] = progress_queue

    def progress_callback(data):
        progress_queue.put(data)

    try:
        # 发送开始合并的消息
        def send_merge_status():
            progress_queue.put({
                'type': 'merging',
                'message': '正在合并视频和音频...'
            })

        success, message, title, filepath = download_video(url, output_dir, platform, progress_callback, proxy_url=proxy_url)

        # 如果是bilibili或youtube，可能有合并过程，发送合并状态
        if platform in ('bilibili', 'youtube') and filepath and '_merged' in filepath:
            send_merge_status()

        return jsonify({"success": success, "message": message, "title": title, "filepath": filepath})
    finally:
        # 清理队列
        if download_id and download_id in progress_queues:
            del progress_queues[download_id]


@bp.route('/progress/<download_id>')
def progress_stream(download_id):
    """SSE端点 - 实时推送下载进度"""

    def generate():
        progress_queue = progress_queues.get(download_id)
        if not progress_queue:
            yield f"data: {json.dumps({'type': 'error', 'message': '下载任务不存在'})}\n\n"
            return

        while True:
            try:
                data = progress_queue.get(timeout=30)
                yield f"data: {json.dumps(data)}\n\n"
                if data.get('type') == 'progress' and data.get('percent', 0) >= 100:
                    break
            except queue.Empty:
                # 发送心跳保持连接
                yield f":\n\n"
                continue

    return Response(
        generate(),
        mimetype='text/event-stream',
        headers={
            'Cache-Control': 'no-cache',
            'Connection': 'keep-alive',
            'X-Accel-Buffering': 'no'
        }
    )


# ===== 保存路径历史 =====

@bp.route('/save-paths', methods=['GET'])
def get_save_paths():
    paths = get_recent_paths(limit=10)
    return jsonify({'paths': paths})


@bp.route('/save-paths', methods=['POST'])
def add_save_path():
    data = request.get_json()
    path = data.get('path', '').strip() if data else ''
    if not path:
        return jsonify({'success': False, 'message': '路径不能为空'}), 400
    add_recent_path(path)
    return jsonify({'success': True})


@bp.route('/save-paths/delete', methods=['POST'])
def delete_save_path():
    data = request.get_json()
    path = data.get('path', '').strip() if data else ''
    if not path:
        return jsonify({'success': False, 'message': '路径不能为空'}), 400
    delete_recent_path(path)
    return jsonify({'success': True})
