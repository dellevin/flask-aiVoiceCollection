# -*- coding: utf-8 -*-
"""
ai-dubbing AI 配音蓝图
通过 subprocess 调用 GPT-SoVITS 自带的 runtime\python.exe 实现文本转语音
"""
import os
import sys
import json
import uuid
import glob
import struct
import subprocess
import threading
import tempfile
from flask import Blueprint, render_template, request, jsonify, send_file, after_this_request

bp = Blueprint('ai_dubbing', __name__, url_prefix='/ai-dubbing')

try:
    from config import BASE_DIR
except ImportError:
    BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

CONFIG_PATH = os.path.join(BASE_DIR, 'config', 'ai_dubbing_config.json')
WORKER_SCRIPT = os.path.join(BASE_DIR, 'utils', 'sovits_worker.py')

# 子进程管理
_proc = None
_proc_lock = threading.Lock()
_proc_stderr = []
_engine_status = {'initialized': False, 'loading': False, 'error': None, 'version': None}

# 合成请求锁
_synth_lock = threading.Lock()

# 任务结果缓存
_task_results_local = {}

TEXT_LANGUAGES = {
    'zh': '中文', 'en': '英文', 'ja': '日文', 'ko': '韩文',
    'yue': '粤语', 'all_zh': '全部中文', 'all_ja': '全部日文',
    'all_yue': '全部粤语', 'all_ko': '全部韩文',
    'auto': '多语种混合', 'auto_yue': '多语种混合(粤语)',
}
CUT_METHODS = ['cut0', 'cut1', 'cut2', 'cut3', 'cut4', 'cut5']
CUT_METHOD_NAMES = {
    'cut0': '不切分', 'cut1': '凑四句一切', 'cut2': '凑50字一切',
    'cut3': '按中文句号。切', 'cut4': '按英文句号.切', 'cut5': '按标点符号切',
}


def _default_config():
    return {
        'sovits_project_path': r'E:\AI\GPT-SoVITS-v4',
        'sovits_config_yaml': r'GPT_SoVITS\configs\tts_infer.yaml',
        'last_prompt_text': '', 'last_prompt_lang': 'zh',
        'last_text_lang': 'zh', 'last_text_split_method': 'cut5',
        'top_k': 5, 'top_p': 1.0, 'temperature': 1.0,
        'batch_size': 1, 'speed_factor': 1.0, 'seed': -1,
        'gpt_model_dir': '', 'sovits_model_dir': '',
        'gpt_model_dir_mode': 'absolute', 'sovits_model_dir_mode': 'absolute',
        'last_gpt_model': '', 'last_sovits_model': '',
        'refer_audio_folder': '', 'refer_audio_folder_mode': 'absolute',
    }


def _load_config():
    cfg = _default_config()
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
            saved = json.load(f)
            cfg.update(saved)
    return cfg


def _save_config(cfg):
    with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


def _scan_models(directory, pattern):
    """扫描目录下指定格式的模型文件，返回文件名列表"""
    if not directory or not os.path.isdir(directory):
        return []
    files = glob.glob(os.path.join(directory, pattern))
    files.sort(key=lambda x: os.path.basename(x).lower())
    return [os.path.basename(f) for f in files]


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
    sovits_path = cfg.get('sovits_project_path', '').strip()
    sovits_config_yaml = cfg.get('sovits_config_yaml', r'GPT_SoVITS\configs\tts_infer.yaml')

    if not sovits_path or not os.path.isdir(sovits_path):
        _engine_status['loading'] = False
        _engine_status['error'] = f'GPT-SoVITS 项目路径不存在: {sovits_path}'
        return False, _engine_status['error']

    runtime_python = os.path.join(sovits_path, 'runtime', 'python.exe')
    if not os.path.exists(runtime_python):
        _engine_status['loading'] = False
        _engine_status['error'] = f'找不到 GPT-SoVITS 运行时: {runtime_python}'
        return False, _engine_status['error']

    try:
        creationflags = 0x08000000 if os.name == 'nt' else 0
        _proc = subprocess.Popen(
            [runtime_python, WORKER_SCRIPT, sovits_path, sovits_config_yaml],
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
            _engine_status['version'] = msg.get('version', 'unknown')
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


def _reload_model(model_type, path):
    """向子进程发送模型重载请求，model_type: 'reload_gpt' 或 'reload_sovits'"""
    with _synth_lock:
        if not _proc or _proc.poll() is not None:
            return False, '子进程未运行'
        try:
            _send_msg(_proc, {'type': model_type, 'path': path})
            msg = _recv_msg(_proc)
            if msg and msg.get('type') == 'done':
                return True, None
            else:
                return False, msg.get('error', '未知错误') if msg else '子进程无响应'
        except Exception as e:
            return False, str(e)


def _synthesize(inputs, output_path):
    with _synth_lock:
        if not _proc or _proc.poll() is not None:
            return False, '子进程未运行'
        try:
            _send_msg(_proc, {
                'type': 'synthesize',
                'inputs': inputs,
                'output_path': output_path,
            })
            msg = _recv_msg(_proc)
            if msg and msg.get('type') == 'done':
                return True, msg.get('output_path')
            else:
                return False, msg.get('error', '未知错误') if msg else '子进程无响应'
        except Exception as e:
            return False, str(e)


# ==================== 路由 ====================

@bp.route('/')
def page():
    cfg = _load_config()
    return render_template('ai_dubbing.html',
                           config=cfg, languages=TEXT_LANGUAGES,
                           cut_methods=CUT_METHODS, cut_method_names=CUT_METHOD_NAMES)


@bp.route('/config', methods=['GET'])
def get_config():
    cfg = _load_config()
    cfg['engine_status'] = _engine_status
    # 扫描模型列表
    sovits_path = cfg.get('sovits_project_path', '')
    gpt_dir_raw = cfg.get('gpt_model_dir', '')
    gpt_dir = gpt_dir_raw if cfg.get('gpt_model_dir_mode') == 'absolute' else os.path.join(sovits_path, gpt_dir_raw)
    sovits_dir_raw = cfg.get('sovits_model_dir', '')
    sovits_dir = sovits_dir_raw if cfg.get('sovits_model_dir_mode') == 'absolute' else os.path.join(sovits_path, sovits_dir_raw)
    cfg['gpt_models'] = _scan_models(gpt_dir, '*.ckpt')
    cfg['sovits_models'] = _scan_models(sovits_dir, '*.pth')
    # 扫描参考音频
    refer_raw = cfg.get('refer_audio_folder', '')
    refer_folder = refer_raw if cfg.get('refer_audio_folder_mode') == 'absolute' else os.path.join(sovits_path, refer_raw)
    if refer_folder and os.path.isdir(refer_folder):
        AUDIO_EXTS = ('.wav', '.mp3', '.flac', '.ogg', '.aac', '.m4a', '.wma')
        files = [os.path.join(refer_folder, f) for f in os.listdir(refer_folder)
                 if os.path.isfile(os.path.join(refer_folder, f)) and f.lower().endswith(AUDIO_EXTS)]
        files.sort(key=lambda x: os.path.basename(x).lower())
        cfg['audio_files'] = files
    else:
        cfg['audio_files'] = []
    return jsonify(cfg)


@bp.route('/config', methods=['POST'])
def save_config():
    data = request.get_json()
    cfg = _load_config()
    for key in ('sovits_project_path', 'sovits_config_yaml',
                'last_prompt_text', 'last_prompt_lang',
                'last_text_lang', 'last_text_split_method',
                'top_k', 'top_p', 'temperature', 'batch_size', 'speed_factor', 'seed',
                'gpt_model_dir', 'sovits_model_dir', 'refer_audio_folder',
                'gpt_model_dir_mode', 'sovits_model_dir_mode', 'refer_audio_folder_mode',
                'last_gpt_model', 'last_sovits_model'):
        if key in data:
            cfg[key] = data[key]
    _save_config(cfg)
    return jsonify({'success': True})


@bp.route('/scan-audio-folder', methods=['POST'])
def scan_audio_folder():
    data = request.get_json()
    folder = (data.get('folder') or '').strip()
    if not folder or not os.path.isdir(folder):
        return jsonify({'success': False, 'error': '文件夹不存在'})
    AUDIO_EXTS = ('.wav', '.mp3', '.flac', '.ogg', '.aac', '.m4a', '.wma')
    files = [os.path.join(folder, f) for f in os.listdir(folder)
             if os.path.isfile(os.path.join(folder, f)) and f.lower().endswith(AUDIO_EXTS)]
    files.sort(key=lambda x: os.path.basename(x).lower())
    return jsonify({'success': True, 'files': files, 'count': len(files)})


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
        return jsonify({'success': True, 'version': _engine_status.get('version')})
    return jsonify({'success': False, 'error': err})


@bp.route('/reload-engine', methods=['POST'])
def reload_engine():
    global _engine_status
    _kill_proc()
    _engine_status = {'initialized': False, 'loading': False, 'error': None, 'version': None}
    ok, err = _init_engine()
    if ok:
        return jsonify({'success': True, 'version': _engine_status.get('version')})
    return jsonify({'success': False, 'error': err})


@bp.route('/stop-engine', methods=['POST'])
def stop_engine():
    global _engine_status
    _kill_proc()
    _engine_status = {'initialized': False, 'loading': False, 'error': None, 'version': None}
    return jsonify({'success': True})


@bp.route('/switch-model', methods=['POST'])
def switch_model():
    """切换 GPT 或 SoVITS 模型"""
    if not _engine_status['initialized']:
        return jsonify({'success': False, 'error': '引擎未初始化'}), 400

    data = request.get_json()
    model_type = data.get('type')  # 'gpt' 或 'sovits'
    filename = data.get('filename')
    if not model_type or not filename:
        return jsonify({'success': False, 'error': '缺少参数'}), 400

    cfg = _load_config()
    sovits_path = cfg.get('sovits_project_path', '')
    if model_type == 'gpt':
        model_dir_raw = cfg.get('gpt_model_dir', '')
        model_dir = model_dir_raw if cfg.get('gpt_model_dir_mode') == 'absolute' else os.path.join(sovits_path, model_dir_raw)
        full_path = os.path.join(model_dir, filename) if model_dir else filename
        ok, err = _reload_model('reload_gpt', full_path)
        if ok:
            cfg['last_gpt_model'] = filename
            _save_config(cfg)
            return jsonify({'success': True, 'message': f'GPT 模型已切换: {filename}'})
        return jsonify({'success': False, 'error': err})
    elif model_type == 'sovits':
        model_dir_raw = cfg.get('sovits_model_dir', '')
        model_dir = model_dir_raw if cfg.get('sovits_model_dir_mode') == 'absolute' else os.path.join(sovits_path, model_dir_raw)
        full_path = os.path.join(model_dir, filename) if model_dir else filename
        ok, err = _reload_model('reload_sovits', full_path)
        if ok:
            cfg['last_sovits_model'] = filename
            _save_config(cfg)
            return jsonify({'success': True, 'message': f'SoVITS 模型已切换: {filename}'})
        return jsonify({'success': False, 'error': err})
    else:
        return jsonify({'success': False, 'error': '未知模型类型'}), 400


@bp.route('/synthesize', methods=['POST'])
def synthesize():
    if not _engine_status['initialized']:
        return jsonify({'success': False, 'error': '引擎未初始化，请先初始化'}), 400

    data = request.get_json()
    text = (data.get('text') or '').strip()
    if not text:
        return jsonify({'success': False, 'error': '请输入要合成的文本'}), 400
    ref_audio_path = (data.get('ref_audio_path') or '').strip()
    if not ref_audio_path:
        return jsonify({'success': False, 'error': '请选择参考音频'}), 400

    cfg = _load_config()
    for key in ('last_prompt_text', 'last_prompt_lang',
                'last_text_lang', 'last_text_split_method',
                'top_k', 'top_p', 'temperature', 'batch_size', 'speed_factor', 'seed'):
        if key in data:
            cfg[key] = data[key]
    _save_config(cfg)

    task_id = uuid.uuid4().hex
    output_path = os.path.join(tempfile.gettempdir(), f'ai_dub_{task_id}.wav')

    inputs = {
        'text': text,
        'text_lang': data.get('text_lang', 'zh'),
        'ref_audio_path': ref_audio_path,
        'prompt_text': data.get('prompt_text', ''),
        'prompt_lang': data.get('prompt_lang', 'zh'),
        'top_k': data.get('top_k', 5),
        'top_p': data.get('top_p', 1.0),
        'temperature': data.get('temperature', 1.0),
        'text_split_method': data.get('text_split_method', 'cut5'),
        'batch_size': data.get('batch_size', 1),
        'speed_factor': data.get('speed_factor', 1.0),
        'seed': data.get('seed', -1),
        'parallel_infer': True,
        'repetition_penalty': 1.35,
    }

    def _do_synth():
        ok, result = _synthesize(inputs, output_path)
        if ok:
            _task_results_local[task_id] = {'status': 'done', 'file': output_path}
        else:
            _task_results_local[task_id] = {'status': 'error', 'error': result}

    _task_results_local[task_id] = {'status': 'synthesizing'}
    t = threading.Thread(target=_do_synth, daemon=True)
    t.start()

    return jsonify({'success': True, 'task_id': task_id})


@bp.route('/status/<task_id>')
def task_status(task_id):
    info = _task_results_local.get(task_id)
    if not info:
        return jsonify({'success': False, 'error': '任务不存在'}), 404

    resp = {'success': True, 'progress': info}
    if info['status'] == 'done':
        resp['audio_url'] = f'/ai-dubbing/audio/{task_id}'
    elif info['status'] == 'error':
        resp['progress'] = {'status': 'error', 'error': info.get('error', '未知错误')}
        _task_results_local.pop(task_id, None)

    return jsonify(resp)


@bp.route('/audio/<task_id>')
def get_audio(task_id):
    info = _task_results_local.pop(task_id, None)
    audio_path = info['file'] if info else os.path.join(tempfile.gettempdir(), f'ai_dub_{task_id}.wav')
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
