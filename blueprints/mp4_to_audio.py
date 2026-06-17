# -*- coding: utf-8 -*-
"""
mp4-to-audio MP4转音频蓝图
基于 ffmpeg 提取视频中的音频流
"""
import os
import re
import json
import uuid
import subprocess
import threading
import tempfile
from flask import Blueprint, render_template, request, jsonify, send_file, after_this_request

try:
    from config import BASE_DIR
except ImportError:
    BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

bp = Blueprint('mp4_to_audio', __name__, url_prefix='/mp4-to-audio')

VIDEO_EXTS = ('.mp4', '.mkv', '.avi', '.webm', '.mov', '.flv', '.wmv')
CONFIG_PATH = os.path.join(BASE_DIR, 'config', 'mp4_to_audio_config.json')

DEFAULT_PARAMS = {
    'output_format': 'mp3',
    'mp3_bitrate': '320k',
    'wav_sample_rate': 'original',
}

_tasks = {}


def _load_config():
    cfg = dict(DEFAULT_PARAMS)
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
                cfg.update(json.load(f))
        except Exception:
            pass
    return cfg


def _save_config(cfg):
    os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
    with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


def _check_ffmpeg():
    try:
        r = subprocess.run(['ffmpeg', '-version'], capture_output=True, text=True, timeout=5)
        return r.returncode == 0
    except Exception:
        return False


def _get_duration(path):
    try:
        cmd = ['ffprobe', '-v', 'error', '-show_entries', 'format=duration',
               '-of', 'default=noprint_wrappers=1:nokey=1', path]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        return float(r.stdout.strip())
    except Exception:
        return 0.0


def _do_convert(task_id):
    task = _tasks[task_id]
    src = task['src']
    output_format = task['output_format']
    out_ext = '.mp3' if output_format == 'mp3' else '.wav'
    base = os.path.splitext(task['orig_name'])[0]
    out_filename = f'{base}{out_ext}'
    out_path = os.path.join(tempfile.gettempdir(), f'mp4audio_{task_id}{out_ext}')

    cmd = ['ffmpeg', '-y', '-i', src]
    if output_format == 'mp3':
        cmd += ['-vn', '-acodec', 'libmp3lame', '-b:a', task['mp3_bitrate']]
    else:
        cmd += ['-vn', '-acodec', 'pcm_s16le']
        if task['wav_sample_rate'] != 'original':
            cmd += ['-ar', task['wav_sample_rate']]
    cmd.append(out_path)

    duration = task['duration']
    try:
        proc = subprocess.Popen(cmd, stderr=subprocess.PIPE, universal_newlines=True)
        for line in proc.stderr:
            m = re.search(r'time=(\d+):(\d+):(\d+\.?\d*)', line)
            if m and duration > 0:
                cur = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
                task['progress'] = min(99, int(cur / duration * 100))
        proc.wait()
        if proc.returncode != 0:
            task['status'] = 'error'
            task['error'] = 'ffmpeg 转换失败'
            return
    except Exception as e:
        task['status'] = 'error'
        task['error'] = str(e)
        return

    task['output_path'] = out_path
    task['filename'] = out_filename
    task['status'] = 'done'
    task['progress'] = 100


@bp.route('/')
def page():
    cfg = _load_config()
    ffmpeg_ok = _check_ffmpeg()
    return render_template('mp4_to_audio.html', config=cfg, ffmpeg_ok=ffmpeg_ok)


@bp.route('/config', methods=['GET'])
def get_config():
    return jsonify(_load_config())


@bp.route('/config', methods=['POST'])
def save_config():
    data = request.get_json()
    cfg = _load_config()
    for key in DEFAULT_PARAMS:
        if key in data:
            cfg[key] = data[key]
    _save_config(cfg)
    return jsonify({'success': True})


@bp.route('/upload', methods=['POST'])
def upload():
    if 'video' not in request.files:
        return jsonify({'success': False, 'error': '请上传视频文件'}), 400
    f = request.files['video']
    if not f.filename:
        return jsonify({'success': False, 'error': '请上传视频文件'}), 400
    ext = os.path.splitext(f.filename)[1].lower()
    if ext not in VIDEO_EXTS:
        return jsonify({'success': False, 'error': f'不支持的格式: {ext}'}), 400

    task_id = uuid.uuid4().hex
    save_path = os.path.join(tempfile.gettempdir(), f'mp4audio_{task_id}{ext}')
    f.save(save_path)

    duration = _get_duration(save_path)
    size = os.path.getsize(save_path)

    _tasks[task_id] = {
        'src': save_path, 'orig_name': f.filename,
        'output_path': None, 'output_format': 'mp3',
        'duration': duration, 'size': size,
        'status': 'uploaded', 'progress': 0, 'error': None,
        'filename': None, 'mp3_bitrate': '320k', 'wav_sample_rate': 'original',
    }
    return jsonify({
        'success': True, 'task_id': task_id,
        'filename': f.filename, 'duration': round(duration, 2), 'size': size,
    })


@bp.route('/convert', methods=['POST'])
def start_convert():
    data = request.get_json()
    task_id = data.get('task_id')
    task = _tasks.get(task_id)
    if not task:
        return jsonify({'success': False, 'error': '任务不存在'}), 404

    task['output_format'] = data.get('output_format', 'mp3')
    task['mp3_bitrate'] = data.get('mp3_bitrate', '320k')
    task['wav_sample_rate'] = data.get('wav_sample_rate', 'original')
    task['status'] = 'converting'
    task['progress'] = 0
    task['error'] = None

    threading.Thread(target=_do_convert, args=(task_id,), daemon=True).start()
    return jsonify({'success': True})


@bp.route('/status/<task_id>')
def status(task_id):
    task = _tasks.get(task_id)
    if not task:
        return jsonify({'success': False, 'error': '任务不存在'}), 404
    resp = {
        'success': True, 'status': task['status'], 'progress': task['progress'],
    }
    if task['status'] == 'done':
        resp['filename'] = task['filename']
        resp['output_format'] = task['output_format']
    elif task['status'] == 'error':
        resp['error'] = task.get('error', '未知错误')
    return jsonify(resp)


@bp.route('/download/<task_id>')
def download(task_id):
    task = _tasks.get(task_id)
    if not task or not task.get('output_path') or not os.path.exists(task['output_path']):
        return jsonify({'error': '文件不存在'}), 404
    mime = 'audio/mpeg' if task['output_format'] == 'mp3' else 'audio/wav'
    return send_file(task['output_path'], mimetype=mime, as_attachment=True,
                     download_name=task['filename'])


@bp.route('/play/<task_id>')
def play(task_id):
    task = _tasks.get(task_id)
    if not task or not task.get('output_path') or not os.path.exists(task['output_path']):
        return jsonify({'error': '文件不存在'}), 404
    mime = 'audio/mpeg' if task['output_format'] == 'mp3' else 'audio/wav'
    return send_file(task['output_path'], mimetype=mime)


@bp.route('/cleanup/<task_id>', methods=['POST'])
def cleanup(task_id):
    task = _tasks.pop(task_id, None)
    if not task:
        return jsonify({'success': True})
    for key in ('src', 'output_path'):
        try:
            p = task.get(key)
            if p and os.path.exists(p):
                os.remove(p)
        except OSError:
            pass
    return jsonify({'success': True})
