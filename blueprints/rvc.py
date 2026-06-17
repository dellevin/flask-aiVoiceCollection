# -*- coding: utf-8 -*-
"""
rvc RVC 变声蓝图
通过 subprocess 调用 RVC 自带的 runtime\python.exe 实现语音变声
"""
import os
import json
import uuid
import glob
import struct
import subprocess
import threading
import tempfile
from flask import Blueprint, render_template, request, jsonify, send_file, after_this_request

bp = Blueprint('rvc', __name__, url_prefix='/rvc')

try:
    from config import BASE_DIR
except ImportError:
    BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

CONFIG_PATH = os.path.join(BASE_DIR, 'config', 'rvc_config.json')
WORKER_SCRIPT = os.path.join(BASE_DIR, 'utils', 'rvc_worker.py')

# 子进程管理
_proc = None
_proc_lock = threading.Lock()
_proc_stderr = []
_engine_status = {'initialized': False, 'loading': False, 'error': None, 'device': None, 'model_loaded': False}

# 变声请求锁
_synth_lock = threading.Lock()

# 任务结果缓存
_task_results_local = {}

F0_METHODS = ['pm', 'harvest', 'crepe', 'rmvpe']
F0_METHOD_NAMES = {
    'pm': 'PM (最快)', 'harvest': 'Harvest (高质量)',
    'crepe': 'Crepe (神经网络)', 'rmvpe': 'RMVPE (推荐)',
}


def _default_config():
    return {
        'rvc_project_path': r'E:\AI\RVC\RVC1006Nvidia',
        'model_dir': 'assets/weights',
        'model_dir_mode': 'relative',
        'index_dir': 'logs',
        'index_dir_mode': 'relative',
        'last_model': '',
        'last_index': '',
        'f0_up_key': 0,
        'f0_method': 'rmvpe',
        'index_rate': 0.75,
        'filter_radius': 3,
        'resample_sr': 0,
        'rms_mix_rate': 0.25,
        'protect': 0.33,
    }


def _load_config():
    cfg = _default_config()
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
            saved = json.load(f)
            cfg.update(saved)
    # 空值回退到默认值
    if not cfg.get('model_dir'):
        cfg['model_dir'] = 'assets/weights'
    if not cfg.get('index_dir'):
        cfg['index_dir'] = 'logs'
    if not cfg.get('model_dir_mode'):
        cfg['model_dir_mode'] = 'relative'
    if not cfg.get('index_dir_mode'):
        cfg['index_dir_mode'] = 'relative'
    return cfg


def _save_config(cfg):
    with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


def _scan_pth_models(directory):
    """扫描 .pth 模型文件"""
    if not directory or not os.path.isdir(directory):
        return []
    files = glob.glob(os.path.join(directory, '*.pth'))
    files.sort(key=lambda x: os.path.basename(x).lower())
    return [os.path.basename(f) for f in files]


def _scan_index_files(directory):
    """递归扫描 .index 文件，排除 trained"""
    if not directory or not os.path.isdir(directory):
        return []
    results = []
    for root, dirs, files in os.walk(directory):
        for f in files:
            if f.endswith('.index') and 'trained' not in f.lower():
                results.append(os.path.join(root, f))
    results.sort(key=lambda x: os.path.basename(x).lower())
    return results


def _send_msg(proc, obj):
    data = json.dumps(obj, ensure_ascii=False).encode('utf-8')
    proc.stdin.write(struct.pack('<I', len(data)))
    proc.stdin.write(data)
    proc.stdin.flush()


def _recv_msg(proc):
    raw_len = proc.stdout.read(4)
    if not raw_len:
        return None
    msg_len = struct.unpack('<I', raw_len)[0]
    data = proc.stdout.read(msg_len)
    return json.loads(data.decode('utf-8'))


def _init_engine():
    global _proc, _engine_status
    if _engine_status['initialized'] and _proc and _proc.poll() is None:
        return True, None

    with _proc_lock:
        if _engine_status['initialized'] and _proc and _proc.poll() is None:
            return True, None
        if _engine_status['loading']:
            return False, '引擎正在加载中，请稍候...'

        _engine_status['loading'] = True
        _engine_status['error'] = None

    cfg = _load_config()
    rvc_path = cfg.get('rvc_project_path', '').strip()

    if not rvc_path or not os.path.isdir(rvc_path):
        _engine_status['loading'] = False
        _engine_status['error'] = f'RVC 项目路径不存在: {rvc_path}'
        return False, _engine_status['error']

    runtime_python = os.path.join(rvc_path, 'runtime', 'python.exe')
    if not os.path.exists(runtime_python):
        _engine_status['loading'] = False
        _engine_status['error'] = f'找不到 RVC 运行时: {runtime_python}'
        return False, _engine_status['error']

    try:
        creationflags = 0x08000000 if os.name == 'nt' else 0
        _proc = subprocess.Popen(
            [runtime_python, WORKER_SCRIPT, rvc_path],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            creationflags=creationflags,
        )

        _proc_stderr.clear()
        def _read_stderr():
            try:
                for line in _proc.stderr:
                    _proc_stderr.append(line.decode('utf-8', errors='replace').rstrip())
            except Exception:
                pass
        threading.Thread(target=_read_stderr, daemon=True).start()

        msg = _recv_msg(_proc)
        if msg and msg.get('type') == 'ready':
            _engine_status['initialized'] = True
            _engine_status['device'] = msg.get('device', 'unknown')
            _engine_status['error'] = None
            return True, None
        else:
            err = msg.get('error', '未知错误') if msg else '子进程无响应'
            if _proc_stderr:
                err += '\n--- 日志 ---\n' + '\n'.join(_proc_stderr[-10:])
            _engine_status['error'] = err
            _kill_proc()
            return False, err

    except Exception as e:
        _engine_status['error'] = str(e)
        _kill_proc()
        return False, str(e)
    finally:
        _engine_status['loading'] = False


def _kill_proc():
    global _proc
    if _proc:
        try:
            _proc.kill()
        except Exception:
            pass
        _proc = None


def _send_command(obj):
    with _synth_lock:
        if not _proc or _proc.poll() is not None:
            return False, '子进程未运行'
        try:
            _send_msg(_proc, obj)
            msg = _recv_msg(_proc)
            if msg and msg.get('type') == 'done':
                return True, msg
            else:
                return False, msg.get('error', '未知错误') if msg else '子进程无响应'
        except Exception as e:
            return False, str(e)


# ==================== 路由 ====================

@bp.route('/')
def page():
    cfg = _load_config()
    return render_template('rvc.html',
                           config=cfg, f0_methods=F0_METHODS,
                           f0_method_names=F0_METHOD_NAMES)


@bp.route('/config', methods=['GET'])
def get_config():
    cfg = _load_config()
    cfg['engine_status'] = _engine_status
    # 扫描模型
    rvc_path = cfg.get('rvc_project_path', '')
    model_dir_raw = cfg.get('model_dir', 'assets/weights')
    model_dir = model_dir_raw if cfg.get('model_dir_mode') == 'absolute' else os.path.join(rvc_path, model_dir_raw)
    cfg['models'] = _scan_pth_models(model_dir)
    # 扫描 index
    index_dir_raw = cfg.get('index_dir', 'logs')
    index_dir = index_dir_raw if cfg.get('index_dir_mode') == 'absolute' else os.path.join(rvc_path, index_dir_raw)
    cfg['index_files'] = _scan_index_files(index_dir)
    return jsonify(cfg)


@bp.route('/config', methods=['POST'])
def save_config():
    data = request.get_json()
    cfg = _load_config()
    for key in ('rvc_project_path', 'model_dir', 'model_dir_mode', 'index_dir', 'index_dir_mode',
                'last_model', 'last_index',
                'f0_up_key', 'f0_method', 'index_rate',
                'filter_radius', 'resample_sr', 'rms_mix_rate', 'protect'):
        if key in data:
            cfg[key] = data[key]
    _save_config(cfg)
    return jsonify({'success': True})


@bp.route('/upload-audio', methods=['POST'])
def upload_audio():
    if 'audio' not in request.files:
        return jsonify({'success': False, 'error': '请上传音频文件'}), 400
    audio_file = request.files['audio']
    if not audio_file.filename:
        return jsonify({'success': False, 'error': '未选择文件'}), 400
    ext = os.path.splitext(audio_file.filename)[1].lower()
    if ext not in ('.wav', '.mp3', '.flac', '.ogg', '.m4a', '.aac'):
        return jsonify({'success': False, 'error': f'不支持的音频格式: {ext}'}), 400
    orig_filename = audio_file.filename
    filename = uuid.uuid4().hex + ext
    save_path = os.path.join(tempfile.gettempdir(), filename)
    audio_file.save(save_path)
    return jsonify({'success': True, 'path': save_path, 'filename': orig_filename})


@bp.route('/engine-status', methods=['GET'])
def engine_status():
    status = dict(_engine_status)
    status['logs'] = _proc_stderr[-50:]
    return jsonify(status)


@bp.route('/clear-logs', methods=['POST'])
def clear_logs():
    _proc_stderr.clear()
    return jsonify({'success': True})


@bp.route('/init-engine', methods=['POST'])
def init_engine():
    ok, err = _init_engine()
    if ok:
        return jsonify({'success': True, 'device': _engine_status.get('device')})
    return jsonify({'success': False, 'error': err})


@bp.route('/reload-engine', methods=['POST'])
def reload_engine():
    global _engine_status
    _kill_proc()
    _engine_status = {'initialized': False, 'loading': False, 'error': None, 'device': None, 'model_loaded': False}
    ok, err = _init_engine()
    if ok:
        return jsonify({'success': True, 'device': _engine_status.get('device')})
    return jsonify({'success': False, 'error': err})


@bp.route('/stop-engine', methods=['POST'])
def stop_engine():
    global _engine_status
    _kill_proc()
    _engine_status = {'initialized': False, 'loading': False, 'error': None, 'device': None, 'model_loaded': False}
    return jsonify({'success': True})


@bp.route('/switch-model', methods=['POST'])
def switch_model():
    if not _engine_status['initialized']:
        return jsonify({'success': False, 'error': '引擎未初始化'}), 400

    data = request.get_json()
    model_name = data.get('model_name')
    if not model_name:
        return jsonify({'success': False, 'error': '缺少模型名称'}), 400

    ok, result = _send_command({'type': 'load_model', 'model_name': model_name})
    if ok:
        _engine_status['model_loaded'] = True
        cfg = _load_config()
        cfg['last_model'] = model_name
        _save_config(cfg)
        return jsonify({'success': True, 'message': f'模型已加载: {model_name}'})
    return jsonify({'success': False, 'error': result})


@bp.route('/convert', methods=['POST'])
def convert():
    if not _engine_status['initialized']:
        return jsonify({'success': False, 'error': '引擎未初始化，请先初始化'}), 400
    if not _engine_status.get('model_loaded'):
        return jsonify({'success': False, 'error': '请先加载语音模型'}), 400

    data = request.get_json()
    input_path = (data.get('input_path') or '').strip()
    if not input_path or not os.path.exists(input_path):
        return jsonify({'success': False, 'error': '请上传有效的音频文件'}), 400

    cfg = _load_config()
    for key in ('last_model', 'last_index', 'f0_up_key', 'f0_method',
                'index_rate', 'filter_radius', 'resample_sr', 'rms_mix_rate', 'protect'):
        if key in data:
            cfg[key] = data[key]
    _save_config(cfg)

    task_id = uuid.uuid4().hex
    output_path = os.path.join(tempfile.gettempdir(), f'rvc_{task_id}.wav')

    convert_params = {
        'type': 'convert',
        'input_path': input_path,
        'output_path': output_path,
        'f0_up_key': data.get('f0_up_key', cfg['f0_up_key']),
        'f0_method': data.get('f0_method', cfg['f0_method']),
        'file_index': data.get('file_index', ''),
        'index_rate': data.get('index_rate', cfg['index_rate']),
        'filter_radius': data.get('filter_radius', cfg['filter_radius']),
        'resample_sr': data.get('resample_sr', cfg['resample_sr']),
        'rms_mix_rate': data.get('rms_mix_rate', cfg['rms_mix_rate']),
        'protect': data.get('protect', cfg['protect']),
    }

    def _do_convert():
        ok, result = _send_command(convert_params)
        if ok:
            _task_results_local[task_id] = {'status': 'done', 'file': output_path}
        else:
            _task_results_local[task_id] = {'status': 'error', 'error': result}

    _task_results_local[task_id] = {'status': 'converting'}
    t = threading.Thread(target=_do_convert, daemon=True)
    t.start()

    return jsonify({'success': True, 'task_id': task_id})


@bp.route('/status/<task_id>')
def task_status(task_id):
    info = _task_results_local.get(task_id)
    if not info:
        return jsonify({'success': False, 'error': '任务不存在'}), 404

    resp = {'success': True, 'progress': info}
    if info['status'] == 'done':
        resp['audio_url'] = f'/rvc/audio/{task_id}'
    elif info['status'] == 'error':
        resp['progress'] = {'status': 'error', 'error': info.get('error', '未知错误')}
        _task_results_local.pop(task_id, None)

    return jsonify(resp)


@bp.route('/audio/<task_id>')
def get_audio(task_id):
    info = _task_results_local.pop(task_id, None)
    audio_path = info['file'] if info else os.path.join(tempfile.gettempdir(), f'rvc_{task_id}.wav')
    if not os.path.exists(audio_path):
        return jsonify({'error': '音频文件不存在'}), 404

    @after_this_request
    def cleanup(resp):
        try:
            os.remove(audio_path)
        except OSError:
            pass
        return resp

    return send_file(audio_path, mimetype='audio/wav')
