# -*- coding: utf-8 -*-
"""
STT 语音识别蓝图
使用 faster-whisper 实现音频转文字
"""
import os
import json
import uuid
import subprocess
import tempfile
import threading
import time
from datetime import timedelta
from flask import Blueprint, render_template, request, jsonify

bp = Blueprint('stt', __name__, url_prefix='/stt')

# HuggingFace 镜像
os.environ['HF_ENDPOINT'] = 'https://hf-mirror.com'

# OpenCC 繁体转简体
try:
    from opencc import OpenCC
    _cc_t2s = OpenCC('t2s')
except Exception:
    _cc_t2s = None

try:
    from config import BASE_DIR
except ImportError:
    BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

CONFIG_PATH = os.path.join(BASE_DIR, 'config', 'stt_config.json')

# 模型缓存
_model_cache = {}
_task_queue = []
_task_progress = {}
_task_results = {}
_worker_started = False
_worker_lock = threading.Lock()

LANGUAGES = {
    'auto': '自动检测',
    'zh': '中文', 'en': '英语', 'ja': '日语', 'ko': '韩语',
    'fr': '法语', 'de': '德语', 'es': '西班牙语', 'ru': '俄语',
    'th': '泰语', 'it': '意大利语', 'pt': '葡萄牙语', 'vi': '越南语',
    'ar': '阿拉伯语', 'tr': '土耳其语',
}


def _load_config():
    cfg = {'model_dir': ''}
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
            cfg.update(json.load(f))
    return cfg


def _save_config(cfg):
    with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


def _scan_models(directory):
    """扫描模型目录，提取已下载的模型名称"""
    models = []
    if not directory or not os.path.isdir(directory):
        return models
    prefix = 'models--Systran--faster-whisper-'
    for name in os.listdir(directory):
        full = os.path.join(directory, name)
        if os.path.isdir(full) and name.startswith(prefix):
            model_name = name[len(prefix):]
            if model_name:
                models.append(model_name)
    return sorted(models)


@bp.route('/')
def page():
    cfg = _load_config()
    return render_template('stt.html', models=_scan_models(cfg['model_dir']), languages=LANGUAGES)


@bp.route('/config', methods=['GET'])
def get_config():
    cfg = _load_config()
    cfg['models'] = _scan_models(cfg['model_dir'])
    return jsonify(cfg)


@bp.route('/config', methods=['POST'])
def save_config():
    data = request.get_json()
    cfg = _load_config()
    if 'model_dir' in data:
        cfg['model_dir'] = data['model_dir'].strip()
    _save_config(cfg)
    return jsonify({'success': True})


@bp.route('/models')
def list_models():
    cfg = _load_config()
    return jsonify({'models': _scan_models(cfg['model_dir'])})


@bp.route('/cuda-check')
def cuda_check():
    """检查 CUDA 是否可用（ctranslate2 优先，PyTorch 兜底）"""
    # 方法1: ctranslate2（faster-whisper 的实际后端）
    try:
        import ctranslate2
        count = ctranslate2.get_cuda_device_count()
        if count > 0:
            name = 'CUDA Device'
            try:
                name = ctranslate2.get_cuda_device_name(0) or name
            except Exception:
                pass
            return jsonify({'cuda': True, 'device_count': count, 'name': name})
    except Exception:
        pass
    # 方法2: PyTorch
    try:
        import torch
        if torch.cuda.is_available():
            return jsonify({'cuda': True, 'device_count': torch.cuda.device_count(),
                            'name': torch.cuda.get_device_name(0)})
    except Exception:
        pass
    return jsonify({'cuda': False})


def _ms_to_srt_time(ms):
    td = timedelta(milliseconds=ms)
    h, rem = divmod(td.seconds, 3600)
    m, s = divmod(rem, 60)
    ms_part = td.microseconds // 1000
    return f'{h:02d}:{m:02d}:{s:02d},{ms_part:03d}'


def _convert_to_wav(input_path):
    """用 FFmpeg 转为 16kHz 单声道 WAV，返回 wav 路径或 None"""
    wav_path = os.path.join(tempfile.gettempdir(), uuid.uuid4().hex + '.wav')
    cmd = ['ffmpeg', '-y', '-i', input_path, '-ar', '16000', '-ac', '1', wav_path]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300,
                                creationflags=0x08000000 if os.name == 'nt' else 0)
        if result.returncode == 0 and os.path.exists(wav_path):
            return wav_path
    except Exception:
        pass
    return None


def _get_model(model_name, device='cpu'):
    """获取或加载模型，CUDA 失败自动回退 CPU"""
    cfg = _load_config()
    model_dir = cfg['model_dir']
    cache_key = f'{model_name}_{device}_{model_dir}'
    if cache_key not in _model_cache:
        from faster_whisper import WhisperModel
        try:
            _model_cache[cache_key] = WhisperModel(
                model_name, device=device, download_root=model_dir
            )
        except Exception as e:
            if device == 'cuda':
                # CUDA 加载失败，回退 CPU
                cpu_key = f'{model_name}_cpu_{model_dir}'
                if cpu_key not in _model_cache:
                    _model_cache[cpu_key] = WhisperModel(
                        model_name, device='cpu', download_root=model_dir
                    )
                return _model_cache[cpu_key], f'CUDA 加载失败({e})，已回退到 CPU'
            raise
    return _model_cache[cache_key], None


def _process_task(task):
    """处理单个转录任务"""
    task_id = task['task_id']
    fmt = task.get('format', 'text')
    device = task.get('device', 'cpu')
    try:
        _task_progress[task_id] = {'percent': 0, 'status': 'loading', 'format': fmt}

        model, warn = _get_model(task['model'], device)
        if warn:
            _task_progress[task_id] = {'percent': 0, 'status': 'transcribing', 'format': fmt, 'warning': warn}
        else:
            _task_progress[task_id] = {'percent': 0, 'status': 'transcribing', 'format': fmt}

        lang = task['language'] if task['language'] != 'auto' else None
        segments, info = model.transcribe(
            task['wav_path'],
            beam_size=5, best_of=5,
            vad_filter=True,
            language=lang,
        )

        total_duration = max(info.duration, 0.01)
        results = []

        for seg in segments:
            _task_progress[task_id] = {
                'percent': round(seg.end / total_duration, 2),
                'status': 'transcribing',
                'format': fmt,
            }
            text = seg.text.strip()
            if not text or len(text) <= 1:
                continue
            if _cc_t2s:
                text = _cc_t2s.convert(text)

            start_ms = int(seg.start * 1000)
            end_ms = int(seg.end * 1000)

            results.append({
                'start': start_ms,
                'end': end_ms,
                'start_time': _ms_to_srt_time(start_ms),
                'end_time': _ms_to_srt_time(end_ms),
                'text': text,
            })

        _task_results[task_id] = results
        _task_progress[task_id] = {'percent': 1, 'status': 'done', 'format': fmt}

    except Exception as e:
        _task_progress[task_id] = {'percent': 0, 'status': 'error', 'error': str(e), 'format': fmt}
    finally:
        # 清理临时文件
        try:
            if os.path.exists(task['wav_path']):
                os.remove(task['wav_path'])
        except Exception:
            pass


def _worker():
    """后台 worker 线程"""
    while True:
        if not _task_queue:
            time.sleep(1)
            continue
        task = _task_queue.pop(0)
        _process_task(task)


def _ensure_worker():
    global _worker_started
    if not _worker_started:
        with _worker_lock:
            if not _worker_started:
                t = threading.Thread(target=_worker, daemon=True)
                t.start()
                _worker_started = True


@bp.route('/transcribe', methods=['POST'])
def transcribe():
    if 'audio' not in request.files:
        return jsonify({'success': False, 'error': '请上传音频文件'}), 400

    audio_file = request.files['audio']
    if not audio_file.filename:
        return jsonify({'success': False, 'error': '未选择文件'}), 400

    model_name = request.form.get('model', 'base')
    language = request.form.get('language', 'auto')
    device = request.form.get('device', 'cpu')
    output_format = request.form.get('format', 'text')

    if model_name not in _scan_models(_load_config()['model_dir']):
        return jsonify({'success': False, 'error': f'模型 {model_name} 不存在，请先下载到 stt_models 目录'}), 400

    # 保存原始文件
    ext = os.path.splitext(audio_file.filename)[1].lower()
    original_path = os.path.join(tempfile.gettempdir(), uuid.uuid4().hex + ext)
    audio_file.save(original_path)

    # 转 WAV
    wav_path = _convert_to_wav(original_path)
    try:
        os.remove(original_path)
    except Exception:
        pass

    if not wav_path:
        return jsonify({'success': False, 'error': '音频转换失败，请确保 FFmpeg 已安装'}), 500

    # 创建任务
    task_id = uuid.uuid4().hex
    _task_progress[task_id] = {'percent': 0, 'status': 'queued', 'format': output_format}
    _task_queue.append({
        'task_id': task_id,
        'wav_path': wav_path,
        'model': model_name,
        'language': language,
        'device': device,
        'format': output_format,
    })

    _ensure_worker()
    return jsonify({'success': True, 'task_id': task_id})


@bp.route('/status/<task_id>')
def task_status(task_id):
    progress = _task_progress.get(task_id)
    if not progress:
        return jsonify({'success': False, 'error': '任务不存在'}), 404

    resp = {'success': True, 'progress': progress}

    if progress['status'] == 'done':
        results = _task_results.get(task_id, [])
        fmt = progress.get('format', 'text')

        if fmt == 'srt':
            lines = []
            for i, r in enumerate(results):
                lines.append(f"{i+1}\n{r['start_time']} --> {r['end_time']}\n{r['text']}\n")
            resp['result'] = '\n'.join(lines)
        elif fmt == 'json':
            resp['result'] = results
        else:
            resp['result'] = '\n'.join(r['text'] for r in results)

        # 清理
        _task_progress.pop(task_id, None)
        _task_results.pop(task_id, None)

    return jsonify(resp)
