# -*- coding: utf-8 -*-
"""
audio-slicer 音频分割蓝图
基于 RMS 静音检测自动切割音频
"""
import os
import io
import json
import uuid
import zipfile
import threading
import tempfile
from flask import Blueprint, render_template, request, jsonify, send_file, after_this_request
import soundfile

try:
    from config import BASE_DIR
except ImportError:
    BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

from utils.audio_slicer_core import analyze_audio, write_slice_range

bp = Blueprint('audio_slicer', __name__, url_prefix='/audio-slicer')

AUDIO_EXTS = ('.wav', '.flac', '.ogg', '.mp3', '.aac', '.m4a', '.wma', '.aiff', '.opus')
CONFIG_PATH = os.path.join(BASE_DIR, 'config', 'audio_slicer_config.json')

DEFAULT_PARAMS = {
    'threshold': -40, 'min_length': 5000, 'min_interval': 300,
    'hop_size': 10, 'max_sil_kept': 1000,
}


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

# 任务缓存: task_id -> {src, sr, ch, total, duration, ranges, settings, slices, status, progress, error, orig_name}
_tasks = {}


def _audio_info(path):
    with soundfile.SoundFile(path) as f:
        sr = f.samplerate
        ch = f.channels
        total = len(f)
        duration = total / sr
    return {'sample_rate': sr, 'channels': ch, 'total_samples': total, 'duration': round(duration, 2)}


@bp.route('/')
def page():
    cfg = _load_config()
    return render_template('audio_slicer.html', config=cfg)


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
    if 'audio' not in request.files:
        return jsonify({'success': False, 'error': '请上传音频文件'}), 400
    f = request.files['audio']
    if not f.filename:
        return jsonify({'success': False, 'error': '请上传音频文件'}), 400
    ext = os.path.splitext(f.filename)[1].lower()
    if ext not in AUDIO_EXTS:
        return jsonify({'success': False, 'error': f'不支持的格式: {ext}'}), 400

    task_id = uuid.uuid4().hex
    save_path = os.path.join(tempfile.gettempdir(), f'aslicer_{task_id}{ext}')
    f.save(save_path)
    try:
        info = _audio_info(save_path)
    except Exception as e:
        try:
            os.remove(save_path)
        except OSError:
            pass
        return jsonify({'success': False, 'error': f'无法读取音频: {e}'}), 400

    _tasks[task_id] = {
        'src': save_path, 'orig_name': f.filename,
        'sr': info['sample_rate'], 'ch': info['channels'],
        'total': info['total_samples'], 'duration': info['duration'],
        'ranges': None, 'slices': None,
        'status': 'uploaded', 'progress': 0, 'error': None,
    }
    return jsonify({'success': True, 'task_id': task_id, 'info': info, 'filename': f.filename})


@bp.route('/analyze', methods=['POST'])
def analyze():
    data = request.get_json()
    task_id = data.get('task_id')
    task = _tasks.get(task_id)
    if not task:
        return jsonify({'success': False, 'error': '任务不存在'}), 404

    settings = {
        'threshold': float(data.get('threshold', -40)),
        'min_length': int(data.get('min_length', 5000)),
        'min_interval': int(data.get('min_interval', 300)),
        'hop_size': int(data.get('hop_size', 10)),
        'max_sil_kept': int(data.get('max_sil_kept', 1000)),
    }
    task['settings'] = settings

    try:
        ranges, sr, ch, total = analyze_audio(task['src'], settings)
    except ValueError as e:
        return jsonify({'success': False, 'error': str(e)}), 400
    except Exception as e:
        return jsonify({'success': False, 'error': f'分析失败: {e}'}), 500

    task['ranges'] = ranges
    task['sr'] = sr
    task['ch'] = ch
    task['status'] = 'analyzed'

    preview = []
    for i, (begin, end) in enumerate(ranges):
        b, e = int(begin), int(end)
        dur = (e - b) / sr
        preview.append({'index': i, 'duration': round(dur, 2), 'samples': e - b})

    return jsonify({
        'success': True, 'count': len(ranges), 'preview': preview,
        'sample_rate': int(sr), 'channels': int(ch),
    })


@bp.route('/manual-slice', methods=['POST'])
def manual_slice():
    data = request.get_json()
    task_id = data.get('task_id')
    task = _tasks.get(task_id)
    if not task:
        return jsonify({'success': False, 'error': '任务不存在'}), 404

    cut_points = sorted(data.get('cut_points', []))
    sr = task['sr']
    total = task['total']
    duration = task['duration']

    valid = [p for p in cut_points if 0 < p < duration]
    if not valid:
        return jsonify({'success': False, 'error': '没有有效的切割点'}), 400

    samples = [int(round(p * sr)) for p in valid]
    boundaries = [0] + samples + [total]
    ranges = [(boundaries[i], boundaries[i + 1])
              for i in range(len(boundaries) - 1)
              if boundaries[i + 1] > boundaries[i]]

    task['ranges'] = ranges
    task['status'] = 'analyzed'

    preview = [{'index': i, 'duration': round((e - b) / sr, 2), 'samples': e - b}
               for i, (b, e) in enumerate(ranges)]
    return jsonify({'success': True, 'count': len(ranges), 'preview': preview,
                    'sample_rate': int(sr), 'channels': int(task['ch'])})


@bp.route('/preview-range/<task_id>')
def preview_range(task_id):
    task = _tasks.get(task_id)
    if not task:
        return jsonify({'error': '任务不存在'}), 404
    try:
        start = float(request.args.get('start', 0))
        end = float(request.args.get('end', task['duration']))
    except (ValueError, TypeError):
        return jsonify({'error': '参数错误'}), 400

    sr = task['sr']
    ch = task['ch']
    begin = max(0, int(start * sr))
    end_sample = min(task['total'], int(end * sr))
    if end_sample <= begin:
        return jsonify({'error': '无效范围'}), 400

    buf = io.BytesIO()
    with soundfile.SoundFile(task['src']) as src:
        src.seek(begin)
        frames = end_sample - begin
        data = src.read(frames)
    with soundfile.SoundFile(buf, mode='w', samplerate=sr, channels=ch,
                             format='WAV') as dst:
        dst.write(data)
    buf.seek(0)
    return send_file(buf, mimetype='audio/wav')


@bp.route('/waveform-peaks/<task_id>')
def waveform_peaks(task_id):
    """返回波形峰值数据，供前端绘制波形图"""
    task = _tasks.get(task_id)
    if not task:
        return jsonify({'error': '任务不存在'}), 404
    num_samples = int(request.args.get('samples', 400))
    with soundfile.SoundFile(task['src']) as f:
        sr = f.samplerate
        ch = f.channels
        total = len(f)
        samples_per_bucket = max(1, total // num_samples)
        peaks = []
        for i in range(num_samples):
            start = i * samples_per_bucket
            length = min(samples_per_bucket, total - start)
            if length <= 0:
                break
            data = f.read(length)
            if ch > 1:
                data = data.mean(axis=1)
            peaks.append(float(abs(data).max()))
    return jsonify({'peaks': peaks, 'duration': task['duration'], 'sr': sr})


@bp.route('/slice', methods=['POST'])
def start_slice():
    data = request.get_json()
    task_id = data.get('task_id')
    task = _tasks.get(task_id)
    if not task:
        return jsonify({'success': False, 'error': '任务不存在'}), 404
    if not task.get('ranges'):
        return jsonify({'success': False, 'error': '请先分析'}), 400

    # 支持选择性切割
    selected = data.get('selected_indices')
    all_ranges = task['ranges']
    if selected is not None and isinstance(selected, list) and len(selected) > 0:
        ranges = [all_ranges[i] for i in selected if 0 <= i < len(all_ranges)]
    else:
        ranges = all_ranges
    if not ranges:
        return jsonify({'success': False, 'error': '未选择任何片段'}), 400

    task['status'] = 'slicing'
    task['progress'] = 0
    task['slices'] = []
    task['error'] = None

    out_dir = os.path.join(tempfile.gettempdir(), f'aslicer_out_{task_id}')
    os.makedirs(out_dir, exist_ok=True)

    def _do_slice():
        base = os.path.splitext(task['orig_name'])[0]
        total = len(ranges)
        for i, (begin, end) in enumerate(ranges):
            out_path = os.path.join(out_dir, f'{base}_{i:03d}.wav')
            try:
                write_slice_range(task['src'], out_path, task['sr'], task['ch'], begin, end)
                task['slices'].append({
                    'index': i, 'path': out_path,
                    'filename': f'{base}_{i:03d}.wav',
                    'duration': round((end - begin) / task['sr'], 2),
                })
            except Exception as e:
                task['error'] = f'切片 {i} 写入失败: {e}'
                task['status'] = 'error'
                return
            task['progress'] = round((i + 1) / total * 100)
        task['status'] = 'done'
        task['progress'] = 100

    threading.Thread(target=_do_slice, daemon=True).start()
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
        resp['slices'] = task['slices']
        resp['count'] = len(task['slices'])
    elif task['status'] == 'error':
        resp['error'] = task.get('error', '未知错误')
    return jsonify(resp)


@bp.route('/download/<task_id>/<int:index>')
def download_one(task_id, index):
    task = _tasks.get(task_id)
    if not task or not task.get('slices'):
        return jsonify({'error': '文件不存在'}), 404
    slices = task['slices']
    if index < 0 or index >= len(slices):
        return jsonify({'error': '索引越界'}), 404
    path = slices[index]['path']
    if not os.path.exists(path):
        return jsonify({'error': '文件不存在'}), 404
    return send_file(path, mimetype='audio/wav', as_attachment=True,
                     download_name=slices[index]['filename'])


@bp.route('/download-all/<task_id>')
def download_all(task_id):
    task = _tasks.get(task_id)
    if not task or not task.get('slices'):
        return jsonify({'error': '无切片可下载'}), 404

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
        for s in task['slices']:
            if os.path.exists(s['path']):
                zf.write(s['path'], s['filename'])
    buf.seek(0)

    base = os.path.splitext(task['orig_name'])[0]
    return send_file(buf, mimetype='application/zip', as_attachment=True,
                     download_name=f'{base}_slices.zip')


@bp.route('/cleanup/<task_id>', methods=['POST'])
def cleanup(task_id):
    """清理临时文件"""
    task = _tasks.pop(task_id, None)
    if not task:
        return jsonify({'success': True})
    try:
        if task.get('src') and os.path.exists(task['src']):
            os.remove(task['src'])
    except OSError:
        pass
    out_dir = os.path.join(tempfile.gettempdir(), f'aslicer_out_{task_id}')
    if os.path.isdir(out_dir):
        for f in os.listdir(out_dir):
            try:
                os.remove(os.path.join(out_dir, f))
            except OSError:
                pass
        try:
            os.rmdir(out_dir)
        except OSError:
            pass
    return jsonify({'success': True})
