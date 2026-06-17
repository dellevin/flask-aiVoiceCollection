# -*- coding: utf-8 -*-
"""
sovits-tts GPT-SoVITS v2 配音蓝图
代理转发请求到 GPT-SoVITS v2 API 服务
"""
import os
import json
import glob
import platform
import subprocess
import requests
from flask import Blueprint, render_template, request, jsonify, Response

bp = Blueprint('sovits_tts', __name__, url_prefix='/sovits-tts')

CONFIG_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'config', 'sovits_config.json')
try:
    from config import BASE_DIR
    CONFIG_PATH = os.path.join(BASE_DIR, 'config', 'sovits_config.json')
except ImportError:
    pass
_sovits_proc = None


@bp.route('/')
def page():
    return render_template('sovits_tts.html')


def _load_config():
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {
        'api_url': 'http://127.0.0.1:9880',
        'gpt_model_dir': '', 'sovits_model_dir': '',
        'last_gpt_model': '', 'last_sovits_model': '',
        'sovits_path': '', 'start_cmd': 'start api_v2.bat',
        'refer_audio_history': [],
        'refer_audio_folder': '',
        'top_k': 5, 'top_p': 1.0, 'temperature': 1.0,
        'batch_size': 32, 'speed': 1.0, 'text_split_method': 'cut5'
    }


def _save_config(cfg):
    with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


def _scan_models(model_dir, ext):
    if not model_dir or not os.path.isdir(model_dir):
        return []
    files = glob.glob(os.path.join(model_dir, '**', f'*{ext}'), recursive=True)
    files.sort(key=lambda f: os.path.getmtime(f), reverse=True)
    return files


@bp.route('/config', methods=['GET'])
def get_config():
    cfg = _load_config()
    cfg['gpt_models'] = _scan_models(cfg.get('gpt_model_dir', ''), '.ckpt')
    cfg['sovits_models'] = _scan_models(cfg.get('sovits_model_dir', ''), '.pth')
    return jsonify(cfg)


@bp.route('/config', methods=['POST'])
def save_config():
    data = request.get_json()
    cfg = _load_config()
    for key in ('api_url', 'gpt_model_dir', 'sovits_model_dir', 'last_gpt_model', 'last_sovits_model', 'sovits_path', 'start_cmd',
                'top_k', 'top_p', 'temperature', 'batch_size', 'speed', 'text_split_method', 'refer_audio_folder'):
        if key in data:
            cfg[key] = data[key]
    _save_config(cfg)
    return jsonify({'success': True})


@bp.route('/refer-audio-history', methods=['POST'])
def save_refer_audio():
    data = request.get_json()
    path = (data.get('path') or '').strip()
    if not path:
        return jsonify({'success': False, 'error': '路径不能为空'})
    cfg = _load_config()
    history = cfg.get('refer_audio_history', [])
    if path in history:
        history.remove(path)
    history.insert(0, path)
    cfg['refer_audio_history'] = history
    _save_config(cfg)
    return jsonify({'success': True, 'history': history})


@bp.route('/refer-audio-history', methods=['DELETE'])
def delete_refer_audio():
    data = request.get_json()
    path = (data.get('path') or '').strip()
    cfg = _load_config()
    history = cfg.get('refer_audio_history', [])
    if path in history:
        history.remove(path)
        cfg['refer_audio_history'] = history
        _save_config(cfg)
    return jsonify({'success': True, 'history': history})


@bp.route('/refer-audio-history', methods=['PUT'])
def update_refer_audio():
    data = request.get_json()
    old_path = (data.get('old_path') or '').strip()
    new_path = (data.get('new_path') or '').strip()
    if not old_path or not new_path:
        return jsonify({'success': False, 'error': '路径不能为空'})
    cfg = _load_config()
    history = cfg.get('refer_audio_history', [])
    if old_path not in history:
        return jsonify({'success': False, 'error': '原路径不存在'})
    if new_path in history and new_path != old_path:
        return jsonify({'success': False, 'error': '新路径已存在'})
    idx = history.index(old_path)
    history[idx] = new_path
    cfg['refer_audio_history'] = history
    _save_config(cfg)
    return jsonify({'success': True, 'history': history})


@bp.route('/scan-audio-folder', methods=['POST'])
def scan_audio_folder():
    """扫描文件夹下的音频文件"""
    data = request.get_json()
    folder = (data.get('folder') or '').strip()
    if not folder:
        return jsonify({'success': False, 'error': '请输入文件夹路径'})
    if not os.path.isdir(folder):
        return jsonify({'success': False, 'error': '文件夹不存在'})

    AUDIO_EXTS = ('.wav', '.mp3', '.flac', '.ogg', '.aac', '.m4a', '.wma')
    files = []
    for f in os.listdir(folder):
        full = os.path.join(folder, f)
        if os.path.isfile(full) and f.lower().endswith(AUDIO_EXTS):
            files.append(full)
    files.sort(key=lambda x: os.path.basename(x).lower())

    # 保存最后使用的文件夹
    cfg = _load_config()
    cfg['refer_audio_folder'] = folder
    _save_config(cfg)

    return jsonify({'success': True, 'files': files, 'count': len(files)})


@bp.route('/test-connection', methods=['POST'])
def test_connection():
    data = request.get_json()
    api_url = (data.get('api_url') or '').strip().rstrip('/')
    if not api_url:
        return jsonify({'success': False, 'error': '请填写 GPT-SoVITS 服务地址'})
    try:
        r = requests.get(api_url + '/test', timeout=10)
        if r.status_code == 200:
            data = r.json()
            return jsonify({'success': True, 'message': data.get('message', '连接成功')})
        return jsonify({'success': False, 'error': f'服务返回状态码 {r.status_code}'})
    except requests.exceptions.ConnectionError:
        return jsonify({'success': False, 'error': f'无法连接到: {api_url}'})
    except requests.exceptions.Timeout:
        return jsonify({'success': False, 'error': '连接超时'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})


@bp.route('/run', methods=['POST'])
def run_api():
    global _sovits_proc
    if platform.system() != 'Windows':
        return jsonify({'success': False, 'error': '仅支持 Windows 系统'})
    cfg = _load_config()
    sovits_path = (cfg.get('sovits_path') or '').strip()
    start_cmd = (cfg.get('start_cmd') or '').strip()
    if not sovits_path:
        return jsonify({'success': False, 'error': '请先配置 GPT-SoVITS 路径'})
    if not os.path.isdir(sovits_path):
        return jsonify({'success': False, 'error': f'路径不存在: {sovits_path}'})
    if not start_cmd:
        return jsonify({'success': False, 'error': '请先配置启动命令'})
    try:
        _sovits_proc = subprocess.Popen(start_cmd, shell=True, cwd=sovits_path)
        return jsonify({'success': True, 'message': f'已启动: {start_cmd}'})
    except Exception as e:
        return jsonify({'success': False, 'error': f'启动失败: {str(e)}'})


@bp.route('/status', methods=['GET'])
def check_status():
    cfg = _load_config()
    api_url = (cfg.get('api_url') or '').strip().rstrip('/')
    if not api_url:
        return jsonify({'running': False})
    try:
        r = requests.get(api_url + '/test', timeout=3)
        if r.status_code == 200:
            data = r.json()
            return jsonify({'running': True, 'message': data.get('message', '')})
    except Exception:
        pass
    return jsonify({'running': False})


@bp.route('/kill', methods=['POST'])
def kill_api():
    global _sovits_proc
    if _sovits_proc and _sovits_proc.poll() is None:
        try:
            subprocess.call(['taskkill', '/F', '/T', '/PID', str(_sovits_proc.pid)],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            pass
        _sovits_proc = None
    else:
        cfg = _load_config()
        api_url = (cfg.get('api_url') or '').strip().rstrip('/')
        if api_url:
            try:
                r = requests.get(api_url + '/control?command=exit', timeout=5)
            except Exception:
                pass
    return jsonify({'success': True, 'message': '已发送停止信号'})


@bp.route('/synthesize', methods=['POST'])
def synthesize():
    data = request.get_json()
    api_url = (data.get('api_url') or '').strip().rstrip('/')
    text = (data.get('text') or '').strip()
    text_lang = (data.get('text_lang') or 'zh').strip()

    if not api_url:
        return jsonify({'success': False, 'error': '请填写 GPT-SoVITS 服务地址'})
    if not text:
        return jsonify({'success': False, 'error': '请输入要合成的文本'})

    ref_audio_path = (data.get('ref_audio_path') or '').strip()
    prompt_text = (data.get('prompt_text') or '').strip()
    prompt_lang = (data.get('prompt_lang') or 'zh').strip()

    if not ref_audio_path:
        return jsonify({'success': False, 'error': '请填写参考音频路径'})

    payload = {
        'text': text,
        'text_lang': text_lang,
        'ref_audio_path': ref_audio_path,
        'prompt_text': prompt_text,
        'prompt_lang': prompt_lang,
        'text_split_method': (data.get('text_split_method') or 'cut5').strip(),
        'batch_size': data.get('batch_size', 1),
        'top_k': data.get('top_k', 5),
        'top_p': data.get('top_p', 1.0),
        'temperature': data.get('temperature', 1.0),
        'speed_factor': data.get('speed_factor', 1.0),
        'seed': data.get('seed', -1),
        'media_type': 'wav',
        'streaming_mode': False,
    }

    try:
        r = requests.post(api_url + '/tts', json=payload, timeout=300)
        content_type = r.headers.get('Content-Type', '')

        if r.status_code == 200 and 'audio' in content_type:
            return Response(r.content, mimetype='audio/wav',
                            headers={'Content-Disposition': 'inline'})
        else:
            try:
                err = r.json()
                msg = err.get('message', r.text[:200])
            except Exception:
                msg = r.text[:200]
            return jsonify({'success': False, 'error': f'合成失败: {msg}'})

    except requests.exceptions.ConnectionError:
        return jsonify({'success': False, 'error': f'无法连接到: {api_url}'})
    except requests.exceptions.Timeout:
        return jsonify({'success': False, 'error': '合成超时（300秒），文本可能过长'})
    except Exception as e:
        return jsonify({'success': False, 'error': f'请求失败: {str(e)}'})


@bp.route('/set-gpt-model', methods=['POST'])
def set_gpt_model():
    data = request.get_json()
    api_url = (data.get('api_url') or '').strip().rstrip('/')
    weights_path = (data.get('weights_path') or '').strip()

    if not api_url:
        return jsonify({'success': False, 'error': '请填写 GPT-SoVITS 服务地址'})
    if not weights_path:
        return jsonify({'success': False, 'error': '请选择 GPT 模型'})

    try:
        r = requests.get(api_url + '/set_gpt_weights', params={'weights_path': weights_path}, timeout=60)
        if r.status_code == 200:
            return jsonify({'success': True, 'message': 'GPT 模型切换成功'})
        try:
            err = r.json()
            msg = err.get('message', r.text[:200])
        except Exception:
            msg = r.text[:200]
        return jsonify({'success': False, 'error': f'切换失败: {msg}'})
    except requests.exceptions.ConnectionError:
        return jsonify({'success': False, 'error': f'无法连接到: {api_url}'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})


@bp.route('/set-sovits-model', methods=['POST'])
def set_sovits_model():
    data = request.get_json()
    api_url = (data.get('api_url') or '').strip().rstrip('/')
    weights_path = (data.get('weights_path') or '').strip()

    if not api_url:
        return jsonify({'success': False, 'error': '请填写 GPT-SoVITS 服务地址'})
    if not weights_path:
        return jsonify({'success': False, 'error': '请选择 SoVITS 模型'})

    try:
        r = requests.get(api_url + '/set_sovits_weights', params={'weights_path': weights_path}, timeout=60)
        if r.status_code == 200:
            return jsonify({'success': True, 'message': 'SoVITS 模型切换成功'})
        try:
            err = r.json()
            msg = err.get('message', r.text[:200])
        except Exception:
            msg = r.text[:200]
        return jsonify({'success': False, 'error': f'切换失败: {msg}'})
    except requests.exceptions.ConnectionError:
        return jsonify({'success': False, 'error': f'无法连接到: {api_url}'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})
