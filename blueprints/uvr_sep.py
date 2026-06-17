# -*- coding: utf-8 -*-
"""
UVR 人声分离蓝图
基于 Ultimate Vocal Remover GUI 的音频源分离能力
支持 VR / MDX-Net / Demucs 三种架构
"""
import os
import sys
import io
import json
import uuid
import hashlib
import zipfile
import shutil
import threading
import tempfile
import traceback
from flask import Blueprint, render_template, request, jsonify, send_file
import numpy as np

try:
    from config import BASE_DIR
except ImportError:
    BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

bp = Blueprint('uvr_sep', __name__, url_prefix='/uvr-sep')

AUDIO_EXTS = ('.wav', '.flac', '.ogg', '.mp3', '.aac', '.m4a', '.wma', '.aiff', '.opus')
CONFIG_PATH = os.path.join(BASE_DIR, 'config', 'uvr_sep_config.json')

# 架构类型常量（与 UVR 的 gui_data.constants 一致）
VR_ARCH = 'VR Arc'
MDX_ARCH = 'MDX-Net'
DEMUCS_ARCH = 'Demucs'

_tasks = {}

# ── 配置读写 ──────────────────────────────────────────────────────────────────

DEFAULT_PARAMS = {
    'uvr_project_path': '', 'model_dir_mode': 'absolute',
    'demucs_model_dir': '', 'vr_model_dir': '', 'mdx_model_dir': '',
    'demucs_model_dir_mode': 'absolute', 'vr_model_dir_mode': 'absolute', 'mdx_model_dir_mode': 'absolute',
    'arch_type': 'Demucs',
    'save_format': 'wav', 'wav_type': 'PCM_16', 'mp3_bitrate': '320k',
    'is_gpu': True, 'device_set': 'Default',
    'is_normalization': False,
    'is_primary_stem_only': False, 'is_secondary_stem_only': False,
    'demucs_stems': 'All Stems',
    'demucs_segment': 'Default',
    'mdx_segment_size': 'Default', 'mdx_overlap': 0.25, 'mdx_batch_size': 1,
    'vr_window_size': 1024, 'vr_aggression': 5, 'vr_batch_size': 4,
    'demucs_selected_model': '', 'mdx_selected_model': '', 'vr_selected_model': '',
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


def _resolve_model_dir(raw_dir, cfg=None, mode=None):
    """将模型目录路径解析为绝对路径"""
    if not raw_dir:
        return ''
    if os.path.isabs(raw_dir):
        return raw_dir
    if cfg is None:
        cfg = _load_config()
    uvr_path = cfg.get('uvr_project_path', '')
    if mode is None:
        mode = cfg.get('model_dir_mode', 'absolute')
    if mode == 'relative' and uvr_path:
        return os.path.normpath(os.path.join(uvr_path, raw_dir))
    # absolute 模式下如果输入的是相对路径，也尝试拼接 uvr_project_path
    if uvr_path:
        resolved = os.path.normpath(os.path.join(uvr_path, raw_dir))
        if os.path.isdir(resolved):
            return resolved
    return raw_dir


def _get_model_dir_for_arch(arch_type, explicit_dir=None, cfg=None):
    """获取指定架构的模型目录：优先显式路径，否则从配置自动推断"""
    if cfg is None:
        cfg = _load_config()
    mode_map = {'Demucs': 'demucs_model_dir_mode', 'VR Arc': 'vr_model_dir_mode', 'MDX-Net': 'mdx_model_dir_mode'}
    mode = cfg.get(mode_map.get(arch_type, ''), cfg.get('model_dir_mode', 'absolute'))
    if explicit_dir:
        return _resolve_model_dir(explicit_dir, cfg, mode=mode)
    key_map = {'Demucs': 'demucs_model_dir', 'VR Arc': 'vr_model_dir', 'MDX-Net': 'mdx_model_dir'}
    raw = cfg.get(key_map.get(arch_type, ''), '')
    return _resolve_model_dir(raw, cfg, mode=mode)


def _get_uvr_paths():
    """计算 UVR 项目内的关键路径"""
    cfg = _load_config()
    uvr_path = cfg.get('uvr_project_path', '')
    if not uvr_path:
        return {}
    models_dir = os.path.join(uvr_path, 'models')
    return {
        'uvr_path': uvr_path,
        'models_dir': models_dir,
        'vr_param_dir': os.path.join(uvr_path, 'lib_v5', 'vr_network', 'modelparams'),
        'mdx_c_config_path': os.path.join(models_dir, 'MDX_Net_Models', 'model_data', 'mdx_c_configs'),
        'mixer_path': os.path.join(uvr_path, 'lib_v5', 'mixer.ckpt'),
        'denoiser_path': os.path.join(models_dir, 'VR_Models', 'UVR-DeNoise-Lite.pth'),
        'deverb_path': os.path.join(models_dir, 'VR_Models', 'UVR-DeEcho-DeReverb.pth'),
    }


# ── 延迟导入 UVR ──────────────────────────────────────────────────────────────

_uvr_imported = False
_uvr_error = None
# 导入后的 UVR 模块引用
_ModelParameters = None
_SeperateVR = _SeperateMDX = _SeperateMDXC = _SeperateDemucs = None
_secondary_stem = None


def _ensure_uvr_imports():
    """延迟导入 UVR 模块，仅在使用时加载"""
    global _uvr_imported, _uvr_error
    global _ModelParameters, _secondary_stem
    global _SeperateVR, _SeperateMDX, _SeperateMDXC, _SeperateDemucs

    if _uvr_imported:
        return True
    if _uvr_error:
        raise ImportError(_uvr_error)

    cfg = _load_config()
    uvr_path = cfg.get('uvr_project_path', '')
    if not uvr_path or not os.path.isdir(uvr_path):
        _uvr_error = '请先配置 UVR 项目路径'
        raise ImportError(_uvr_error)

    try:
        if uvr_path not in sys.path:
            sys.path.insert(0, uvr_path)

        from gui_data.constants import secondary_stem as _ss
        _secondary_stem = _ss

        from lib_v5.vr_network.model_param_init import ModelParameters as _MP
        _ModelParameters = _MP

        from separate import (
            SeperateVR as _SVR, SeperateMDX as _SMDX,
            SeperateMDXC as _SMDXC, SeperateDemucs as _SDem,
        )
        _SeperateVR = _SVR
        _SeperateMDX = _SMDX
        _SeperateMDXC = _SMDXC
        _SeperateDemucs = _SDem

        # PyTorch 2.6+ 默认 weights_only=True，对 UVR 模型不兼容
        # patch torch.load，对 demucs 的模型加载使用 weights_only=False
        import torch as _torch
        _original_torch_load = _torch.load
        def _safe_torch_load(*args, **kwargs):
            if 'weights_only' not in kwargs:
                kwargs['weights_only'] = False
            return _original_torch_load(*args, **kwargs)
        _torch.load = _safe_torch_load

        # 重新检测 CUDA（separate.py 在模块加载时检测一次，可能不准确）
        import separate as _separate_module
        _separate_module.cuda_available = _torch.cuda.is_available()

        # 修复 UVR demucs/apply.py 中 bag_num/prog_bar 未初始化的 bug
        try:
            from demucs import apply as _demucs_apply
            _orig_apply = _demucs_apply.apply_model
            def _patched_apply(*args, **kwargs):
                _demucs_apply.bag_num = getattr(_demucs_apply, 'bag_num', 1)
                _demucs_apply.prog_bar = getattr(_demucs_apply, 'prog_bar', 0)
                return _orig_apply(*args, **kwargs)
            _demucs_apply.apply_model = _patched_apply
        except Exception:
            pass

        # 修复 librosa 新版本 API 不兼容（位置参数 → 关键字参数）
        import librosa as _librosa
        _orig_librosa_load = _librosa.load
        def _compat_librosa_load(path, *args, **kwargs):
            if args:
                if 'sr' not in kwargs and len(args) >= 1:
                    kwargs['sr'] = args[0]
                if 'mono' not in kwargs and len(args) >= 2:
                    kwargs['mono'] = args[1]
                args = ()
            return _orig_librosa_load(path, *args, **kwargs)
        _librosa.load = _compat_librosa_load

        _orig_librosa_stft = _librosa.stft
        def _compat_librosa_stft(y, *args, **kwargs):
            names = ['n_fft', 'hop_length', 'win_length', 'window', 'center',
                     'pad_mode', 'length', 'return_complex']
            for i, v in enumerate(args):
                if i < len(names) and names[i] not in kwargs:
                    kwargs[names[i]] = v
            return _orig_librosa_stft(y, **kwargs)
        _librosa.stft = _compat_librosa_stft

        _orig_librosa_resample = _librosa.resample
        def _compat_librosa_resample(y, *args, **kwargs):
            names = ['orig_sr', 'target_sr', 'fix', 'scale', 'axis', 'res_type']
            for i, v in enumerate(args):
                if i < len(names) and names[i] not in kwargs:
                    kwargs[names[i]] = v
            return _orig_librosa_resample(y, **kwargs)
        _librosa.resample = _compat_librosa_resample

        _orig_librosa_istft = getattr(_librosa, 'istft', None)
        if _orig_librosa_istft:
            def _compat_librosa_istft(stft_matrix, *args, **kwargs):
                names = ['hop_length', 'win_length', 'window', 'center', 'length', 'dtype']
                for i, v in enumerate(args):
                    if i < len(names) and names[i] not in kwargs:
                        kwargs[names[i]] = v
                return _orig_librosa_istft(stft_matrix, **kwargs)
            _librosa.istft = _compat_librosa_istft

        # 修复 VR Arc: cmb_spectrogram_to_wave 中 np.ndarray(dtype=complex)
        # 未初始化包含垃圾值 → 用 np.zeros 替代
        from lib_v5 import spec_utils as _spec_utils
        import numpy as _cmb_np
        import librosa as _cmb_librosa
        def _fixed_cmb_spectrogram_to_wave(spec_m, mp, extra_bins_h=None, extra_bins=None, is_v51_model=False):
            spec_m = _cmb_np.nan_to_num(spec_m, nan=0.0, posinf=0.0, neginf=0.0)
            bands_n = len(mp.param['band'])
            offset = 0
            for d in range(1, bands_n + 1):
                bp = mp.param['band'][d]
                spec_s = _cmb_np.zeros(shape=(2, bp['n_fft'] // 2 + 1, spec_m.shape[2]), dtype=complex)
                h = bp['crop_stop'] - bp['crop_start']
                spec_s[:, bp['crop_start']:bp['crop_stop'], :] = spec_m[:, offset:offset+h, :]
                offset += h
                if d == bands_n:
                    if extra_bins_h:
                        max_bin = bp['n_fft'] // 2
                        spec_s[:, max_bin-extra_bins_h:max_bin, :] = extra_bins[:, :extra_bins_h, :]
                    if bp['hpf_start'] > 0:
                        if is_v51_model:
                            spec_s *= _spec_utils.get_hp_filter_mask(spec_s.shape[1], bp['hpf_start'], bp['hpf_stop'] - 1)
                        else:
                            spec_s = _spec_utils.fft_hp_filter(spec_s, bp['hpf_start'], bp['hpf_stop'] - 1)
                    if bands_n == 1:
                        wav = _spec_utils.spectrogram_to_wave(spec_s, bp['hl'], mp, d, is_v51_model)
                    else:
                        wav = _cmb_np.add(wav, _spec_utils.spectrogram_to_wave(spec_s, bp['hl'], mp, d, is_v51_model))
                else:
                    sr = mp.param['band'][d+1]['sr']
                    if d == 1:
                        if is_v51_model:
                            spec_s *= _spec_utils.get_lp_filter_mask(spec_s.shape[1], bp['lpf_start'], bp['lpf_stop'])
                        else:
                            spec_s = _spec_utils.fft_lp_filter(spec_s, bp['lpf_start'], bp['lpf_stop'])
                        wav = _cmb_librosa.resample(_spec_utils.spectrogram_to_wave(spec_s, bp['hl'], mp, d, is_v51_model), orig_sr=bp['sr'], target_sr=sr, res_type=_spec_utils.wav_resolution)
                    else:
                        if is_v51_model:
                            spec_s *= _spec_utils.get_hp_filter_mask(spec_s.shape[1], bp['hpf_start'], bp['hpf_stop'] - 1)
                            spec_s *= _spec_utils.get_lp_filter_mask(spec_s.shape[1], bp['lpf_start'], bp['lpf_stop'])
                        else:
                            spec_s = _spec_utils.fft_hp_filter(spec_s, bp['hpf_start'], bp['hpf_stop'] - 1)
                            spec_s = _spec_utils.fft_lp_filter(spec_s, bp['lpf_start'], bp['lpf_stop'])
                        wav2 = _cmb_np.add(wav, _spec_utils.spectrogram_to_wave(spec_s, bp['hl'], mp, d, is_v51_model))
                        wav = _cmb_librosa.resample(wav2, orig_sr=bp['sr'], target_sr=sr, res_type=_spec_utils.wav_resolution)
            return wav
        _spec_utils.cmb_spectrogram_to_wave = _fixed_cmb_spectrogram_to_wave

        # 修复 torch.stft/istft 参数兼容性
        import torch as _torch
        _orig_stft = _torch.stft
        def _compat_stft(input, *args, **kwargs):
            names = ['n_fft', 'hop_length', 'win_length', 'window',
                     'center', 'pad_mode', 'normalized', 'onesided', 'return_complex']
            for i, v in enumerate(args):
                if i < len(names) and names[i] not in kwargs:
                    kwargs[names[i]] = v
            return _orig_stft(input, **kwargs)
        _torch.stft = _compat_stft
        _torch.functional.stft = _compat_stft

        _uvr_imported = True
        return True
    except Exception as e:
        _uvr_error = f'UVR 模块加载失败: {e}'
        if 'No module named' in str(e):
            _uvr_error += '\n请安装缺少的依赖: pip install pyrubberband ml_collections'
        raise ImportError(_uvr_error) from e


def _secondary_stem_fallback(stem):
    """当 UVR 未加载时的备用 stem 映射"""
    pairs = {'Vocals': 'Instrumental', 'Instrumental': 'Vocals',
             'Primary Stem': 'Secondary Stem', 'Other': 'No Other',
             'Drums': 'No Drums', 'Bass': 'No Bass', 'Guitar': 'No Guitar'}
    return pairs.get(stem, f'No {stem}' if not stem.startswith('No ') else stem.replace('No ', ''))


def _get_secondary_stem(stem):
    """获取配对音轨名"""
    if _secondary_stem:
        return _secondary_stem(stem)
    return _secondary_stem_fallback(stem)


# ── 模型元数据 ─────────────────────────────────────────────────────────────────

_vr_hash_data = None
_mdx_hash_data = None


def _load_model_hash_data():
    """加载 UVR 的模型哈希数据"""
    global _vr_hash_data, _mdx_hash_data
    if _vr_hash_data is not None:
        return

    cfg = _load_config()
    uvr_path = cfg.get('uvr_project_path', '')
    if not uvr_path:
        _vr_hash_data, _mdx_hash_data = {}, {}
        return

    vr_dir = os.path.join(uvr_path, 'models', 'VR_Models', 'model_data')
    mdx_dir = os.path.join(uvr_path, 'models', 'MDX_Net_Models', 'model_data')

    _vr_hash_data = {}
    _mdx_hash_data = {}

    for path in [
        os.path.join(vr_dir, 'model_data.json'),
        os.path.join(vr_dir, 'model_data_new.json'),
    ]:
        if os.path.isfile(path):
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    _vr_hash_data.update(json.load(f))
            except Exception:
                pass

    for path in [
        os.path.join(mdx_dir, 'model_data.json'),
        os.path.join(mdx_dir, 'model_data_new.json'),
    ]:
        if os.path.isfile(path):
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    _mdx_hash_data.update(json.load(f))
            except Exception:
                pass


def _compute_model_hash(model_path):
    """计算模型文件哈希（与 UVR 相同的算法）"""
    try:
        with open(model_path, 'rb') as f:
            f.seek(-10000 * 1024, 2)
            return hashlib.md5(f.read()).hexdigest()
    except Exception:
        try:
            return hashlib.md5(open(model_path, 'rb').read()).hexdigest()
        except Exception:
            return None


def _lookup_model_meta(model_hash, arch_type):
    """通过哈希查找模型元数据"""
    _load_model_hash_data()
    if not model_hash:
        return None
    hash_data = _vr_hash_data if 'VR' in arch_type else _mdx_hash_data
    return hash_data.get(model_hash)


# ── HeadlessModelData ─────────────────────────────────────────────────────────

class HeadlessModelData:
    """替代 UVR 的 ModelData，无需 tkinter GUI"""

    def __init__(self, model_path, process_method, params, model_meta=None):
        self.model_path = model_path
        self.model_name = os.path.splitext(os.path.basename(model_path))[0]
        self.model_basename = self.model_name
        self.process_method = process_method
        self.model_status = True
        self.model_meta = model_meta

        # GPU 设置
        self.is_gpu_conversion = 0 if params.get('is_gpu', True) else -1
        self.device_set = params.get('device_set', 'Default')

        # 通用参数
        self.is_normalization = params.get('is_normalization', False)
        self.is_primary_stem_only = params.get('is_primary_stem_only', False)
        self.is_secondary_stem_only = params.get('is_secondary_stem_only', False)
        self.wav_type_set = params.get('wav_type', 'PCM_16')
        self.mp3_bit_set = params.get('mp3_bitrate', '320k')
        self.save_format = params.get('save_format', 'WAV').upper()

        self.is_invert_spec = False
        self.is_mixer_mode = False
        self.is_mdx_c_seg_def = True
        self.mdx_batch_size = params.get('mdx_batch_size', 1)
        self.mdxnet_stem_select = 'Vocals'
        self.overlap = params.get('mdx_overlap', 0.25)
        self.overlap_mdx = params.get('mdx_overlap', 0.25)
        self.overlap_mdx23 = 8
        self.semitone_shift = 0
        self.is_pitch_change = False
        self.is_match_frequency_pitch = True
        self.is_mdx_combine_stems = False
        self.is_use_opencl = False

        # MDX 模型数据
        self.is_mdx_ckpt = model_path.endswith('.ckpt') or model_path.endswith('.ckptc')
        self.is_mdx_c = False
        self.mdx_c_configs = None
        self.mdx_model_stems = []
        self.mdx_dim_f_set = None
        self.mdx_dim_t_set = None
        self.mdx_stem_count = 1
        self.compensate = None
        self.mdx_n_fft_scale_set = None

        # 路径
        paths = _get_uvr_paths()
        self.mixer_path = paths.get('mixer_path', '')

        # Demucs 默认
        self.demucs_stems = 'All Stems'
        self.is_demucs_combine_stems = False
        self.demucs_source_list = []
        self.demucs_stem_count = 0
        self.demucs_source_map = {}
        self.demucs_version = 'v4'

        # 主音轨设置
        self.primary_stem = None
        self.secondary_stem = None
        self.primary_stem_native = None

        # 禁用复杂功能
        self.is_ensemble_mode = False
        self.ensemble_primary_stem = None
        self.ensemble_secondary_stem = None
        self.primary_model_primary_stem = None
        self.is_secondary_model = False
        self.is_secondary_model_activated = False
        self.secondary_model = None
        self.secondary_model_scale = None
        self.pre_proc_model = None
        self.pre_proc_model_activated = False
        self.is_pre_proc_model = False
        self.is_dry_check = False
        self.is_vocal_split_model = False
        self.is_vocal_split_model_activated = False
        self.is_primary_model_primary_stem_only = False
        self.is_primary_model_secondary_stem_only = False
        self.is_save_inst_vocal_splitter = False
        self.is_inst_only_voc_splitter = False
        self.is_save_vocal_only = False
        self.is_deverb_vocals = False
        self.deverb_vocal_opt = 'Vocals'
        self.is_denoise = False
        self.is_denoise_model = False
        self.is_karaoke = False
        self.is_bv_model = False
        self.bv_model_rebalance = 0
        self.is_sec_bv_rebalance = False

        # 默认
        self.model_samplerate = 44100
        self.model_capacity = (32, 128)
        self.is_vr_51_model = False
        self.is_demucs_pre_proc_model_inst_mix = False
        self.is_change_def = False
        self.is_4_stem_ensemble = False
        self.is_multi_stem_ensemble = False
        self.is_demucs_4_stem_secondaries = False
        self.demucs_4_stem_added_count = 0
        self.model_hash_dir = None
        self.is_get_hash_dir_only = False

        # 多模型占位
        self.secondary_model_4_stem = []
        self.secondary_model_4_stem_scale = []
        self.secondary_model_4_stem_names = []
        self.secondary_model_4_stem_model_names_list = []
        self.all_models = []
        self.secondary_model_other = None
        self.secondary_model_scale_other = None
        self.secondary_model_bass = None
        self.secondary_model_scale_bass = None
        self.secondary_model_drums = None
        self.secondary_model_scale_drums = None

        # DeNoise / DeVerb 模型路径
        self.DENOISER_MODEL = paths.get('denoiser_path', '')
        self.DEVERBER_MODEL = paths.get('deverb_path', '')
        self.vocal_split_model = None

        # 根据架构类型初始化特定参数
        if process_method == VR_ARCH:
            self._init_vr(params, model_meta, paths)
        elif process_method == MDX_ARCH:
            self._init_mdx(params, model_meta, paths)
        elif process_method == DEMUCS_ARCH:
            self._init_demucs(params)

    def _init_vr(self, params, model_meta, paths):
        """VR 架构特定参数"""
        self.aggression_setting = float(int(params.get('vr_aggression', 5)) / 100)
        self.is_tta = False
        self.is_post_process = False
        self.window_size = params.get('vr_window_size', 1024)
        self.batch_size = params.get('vr_batch_size', 4)
        self.crop_size = 256
        self.is_high_end_process = 'None'
        self.post_process_threshold = 0.2

        if model_meta:
            self.primary_stem = model_meta.get('primary_stem', 'Vocals')
            vr_param_name = model_meta.get('vr_model_param', 'bandparam_opposite')
            vr_param_path = os.path.join(paths.get('vr_param_dir', ''), f'{vr_param_name}.json')
            if os.path.isfile(vr_param_path) and _ModelParameters:
                self.vr_model_param = _ModelParameters(vr_param_path)
                self.model_samplerate = self.vr_model_param.param['sr']
            else:
                self.vr_model_param = None
            if 'nout' in model_meta and 'nout_lstm' in model_meta:
                self.model_capacity = (model_meta['nout'], model_meta['nout_lstm'])
                self.is_vr_51_model = True
        else:
            self.vr_model_param = None
            self.primary_stem = 'Vocals'

        self.primary_stem_native = self.primary_stem
        self.secondary_stem = _get_secondary_stem(self.primary_stem)

    def _init_mdx(self, params, model_meta, paths):
        """MDX 架构特定参数"""
        self.margin = 44100
        self.chunks = 0
        seg = params.get('mdx_segment_size', 'Default')
        self.mdx_segment_size = int(seg) if seg and seg != 'Default' else 256

        if model_meta:
            if 'config_yaml' in model_meta:
                self.is_mdx_c = True
                config_path = os.path.join(
                    paths.get('mdx_c_config_path', ''),
                    model_meta['config_yaml']
                )
                if os.path.isfile(config_path):
                    import yaml
                    from ml_collections import ConfigDict
                    with open(config_path) as f:
                        self.mdx_c_configs = ConfigDict(yaml.load(f, Loader=yaml.FullLoader))
                    if self.mdx_c_configs.training.target_instrument:
                        target = self.mdx_c_configs.training.target_instrument
                        self.mdx_model_stems = [target]
                        self.primary_stem = target
                    else:
                        self.mdx_model_stems = self.mdx_c_configs.training.instruments
                        self.mdx_stem_count = len(self.mdx_model_stems)
                        self.primary_stem = self.mdx_model_stems[0] if self.mdx_stem_count == 2 else self.mdxnet_stem_select
                else:
                    self.primary_stem = model_meta.get('primary_stem', 'Vocals')
            else:
                self.compensate = model_meta.get('compensate', 1.0)
                self.mdx_dim_f_set = model_meta.get('mdx_dim_f_set')
                self.mdx_dim_t_set = model_meta.get('mdx_dim_t_set')
                self.mdx_n_fft_scale_set = model_meta.get('mdx_n_fft_scale_set')
                self.primary_stem = model_meta.get('primary_stem', 'Vocals')
        else:
            self.primary_stem = 'Vocals'

        self.primary_stem_native = self.primary_stem
        self.secondary_stem = _get_secondary_stem(self.primary_stem)

    def _init_demucs(self, params):
        """Demucs 架构特定参数"""
        self.margin_demucs = 44100
        self.chunks_demucs = 0
        self.shifts = 1
        self.is_split_mode = True
        self.segment = params.get('demucs_segment', 'Default')
        self.is_chunk_demucs = False
        self.demucs_stems = params.get('demucs_stems', 'All Stems')

        # 从文件名推断版本和音轨数
        self.demucs_version = 'v4'
        for ver, tag in [('v1', 'v1 | '), ('v2', 'v2 | '), ('v3', 'v3 | '), ('v4', 'v4 | ')]:
            if tag in self.model_name:
                self.demucs_version = ver
                break

        # .th 文件名格式为 "sig-checksum"，get_model 只需要 sig 部分
        if self.model_path.endswith('.th') and '-' in self.model_basename:
            self.model_basename = self.model_basename.split('-')[0]

        if 'UVR_Model' in self.model_name:
            self.demucs_source_list = ['instrumental', 'vocals']
            self.demucs_source_map = {'instrumental': 0, 'vocals': 1}
            self.demucs_stem_count = 2
            self.primary_stem = 'Vocals'
            self.secondary_stem = 'Instrumental'
        else:
            self.demucs_source_list = ['drums', 'bass', 'other', 'vocals']
            self.demucs_source_map = {
                'Bass': 0, 'Drums': 1, 'Other': 2, 'Vocals': 3
            }
            self.demucs_stem_count = 4
            self.primary_stem = 'Vocals'
            self.secondary_stem = _get_secondary_stem('Vocals')


# ── 工具函数 ───────────────────────────────────────────────────────────────────

def _update_progress(task_id, step, inference_iterations=0):
    """更新任务进度 (step: 0.0~1.0)"""
    task = _tasks.get(task_id)
    if not task:
        return
    progress = min(99, max(1, int((step + inference_iterations) * 100)))
    task['progress'] = progress
    import time as _t
    # 记录子进度（inference_iterations > 0 表示推理中的迭代进度）
    if inference_iterations > 0:
        last = task.get('_last_iter_log', 0)
        if inference_iterations - last >= 0.1:
            task['_last_iter_log'] = inference_iterations
            task['logs'].append('[%s] Inference iteration: %.0f%%' % (_t.strftime('%H:%M:%S'), inference_iterations * 100))
            if len(task['logs']) > 500:
                task['logs'] = task['logs'][-500:]
    else:
        # 主进度每 20% 记录一条
        last = task.get('_last_progress_log', 0)
        if progress - last >= 20:
            task['_last_progress_log'] = progress
            task['logs'].append('[%s] Progress: %d%%' % (_t.strftime('%H:%M:%S'), progress))
            if len(task['logs']) > 500:
                task['logs'] = task['logs'][-500:]


def _make_process_data(task_id, model_data, audio_path, export_path):
    """构造 process_data 字典"""
    base = os.path.splitext(os.path.basename(audio_path))[0]
    task = _tasks.get(task_id)
    def _write_console(*args, **kwargs):
        msg = ' '.join(str(a) for a in args)
        msg = msg.replace('\r\n', '\n').replace('\r', '\n').strip()
        if msg and task:
            import time as _t
            for line in msg.split('\n'):
                line = line.strip()
                if line:
                    task['logs'].append('[%s] %s' % (_t.strftime('%H:%M:%S'), line))
                    if len(task['logs']) > 500:
                        task['logs'] = task['logs'][-500:]
    return {
        'model_data': model_data,
        'export_path': export_path,
        'audio_file_base': base,
        'audio_file': audio_path,
        'set_progress_bar': lambda step, it=0: _update_progress(task_id, step, it),
        'write_to_console': _write_console,
        'process_iteration': lambda: None,
        'cached_source_callback': lambda *_, **__: (None, None),
        'cached_model_source_holder': lambda *_, **__: None,
        'list_all_models': [],
        'is_ensemble_master': False,
        'is_4_stem_ensemble': False,
    }


def _do_separate(task_id, model_path, process_method, audio_path, params, model_meta):
    """后台线程执行分离"""
    task = _tasks[task_id]
    import time as _time

    # 日志捕获
    class _LogCapture:
        def __init__(self, orig):
            self._orig = orig
        def write(self, msg):
            if msg and msg.strip():
                text = msg.replace('\r\n', '\n').replace('\r', '\n').strip()
                for line in text.split('\n'):
                    line = line.strip()
                    if line:
                        task['logs'].append('[%s] %s' % (_time.strftime('%H:%M:%S'), line))
                        if len(task['logs']) > 500:
                            task['logs'] = task['logs'][-500:]
            self._orig.write(msg)
        def flush(self):
            self._orig.flush()

    old_stdout, old_stderr = sys.stdout, sys.stderr
    sys.stdout = _LogCapture(old_stdout)
    sys.stderr = _LogCapture(old_stderr)

    try:
        _ensure_uvr_imports()

        import separate as _sep_mod
        import torch
        cuda_ok = getattr(_sep_mod, 'cuda_available', False) and torch.cuda.is_available()

        model_data = HeadlessModelData(model_path, process_method, params, model_meta)

        export_path = os.path.join(tempfile.gettempdir(), f'uvr_out_{task_id}')
        os.makedirs(export_path, exist_ok=True)

        process_data = _make_process_data(task_id, model_data, audio_path, export_path)

        # 根据架构类型选择分离器
        if process_method == VR_ARCH:
            separator = _SeperateVR(model_data, process_data)
        elif process_method == MDX_ARCH:
            if model_data.is_mdx_c:
                separator = _SeperateMDXC(model_data, process_data)
            else:
                separator = _SeperateMDX(model_data, process_data)
        elif process_method == DEMUCS_ARCH:
            separator = _SeperateDemucs(model_data, process_data)
        else:
            raise ValueError(f'不支持的架构: {process_method}')

        actual_device = str(getattr(separator, 'device', 'unknown'))
        task['device'] = actual_device
        print(f'[UVR-Sep] is_gpu={params.get("is_gpu")}, cuda={cuda_ok}, device={actual_device}')

        separator.seperate()

        # 扫描输出文件
        stems = []
        if os.path.isdir(export_path):
            for fname in sorted(os.listdir(export_path)):
                fpath = os.path.join(export_path, fname)
                if os.path.isfile(fpath):
                    stem_name = fname
                    if '_(' in fname and fname.endswith(').wav'):
                        start = fname.index('_(') + 2
                        end = fname.rindex(')')
                        stem_name = fname[start:end]
                    stems.append({
                        'stem': stem_name,
                        'filename': fname,
                        'path': fpath,
                    })

        if not stems:
            task['status'] = 'error'
            task['error'] = '分离完成但未生成输出文件'
            return

        task['stems'] = stems
        task['status'] = 'done'
        task['progress'] = 100

    except Exception as e:
        task['status'] = 'error'
        task['error'] = str(e)
        traceback.print_exc()
    finally:
        sys.stdout = old_stdout
        sys.stderr = old_stderr
        try:
            os.remove(audio_path)
        except OSError:
            pass


# ── 路由 ───────────────────────────────────────────────────────────────────────

@bp.route('/')
def page():
    cfg = _load_config()
    return render_template('uvr_sep.html', config=cfg)


@bp.route('/gpu-status')
def gpu_status():
    """检测 GPU/CUDA 状态"""
    info = {'cuda_available': False, 'gpu_name': '', 'torch_version': 'N/A', 'torch_cuda': False}
    # 方法1: ctranslate2
    try:
        import ctranslate2
        count = ctranslate2.get_cuda_device_count()
        if count > 0:
            info['cuda_available'] = True
            try:
                info['gpu_name'] = ctranslate2.get_cuda_device_name(0) or ''
            except Exception:
                pass
    except Exception:
        pass
    # 方法2: PyTorch
    try:
        import torch
        info['torch_version'] = torch.__version__
        info['torch_cuda'] = torch.cuda.is_available()
        if info['torch_cuda']:
            info['cuda_available'] = True
            if not info['gpu_name']:
                info['gpu_name'] = torch.cuda.get_device_name(0)
    except ImportError:
        pass
    return jsonify(info)


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
    global _uvr_imported, _uvr_error, _vr_hash_data, _mdx_hash_data
    if 'uvr_project_path' in data:
        _uvr_imported = False
        _uvr_error = None
        _vr_hash_data = None
        _mdx_hash_data = None
    return jsonify({'success': True})


@bp.route('/scan-models', methods=['POST'])
def scan_models():
    """扫描模型目录"""
    data = request.get_json()
    model_dir = data.get('model_dir', '')
    arch_type = data.get('arch_type', 'MDX-Net')

    cfg = _load_config()
    model_dir = _get_model_dir_for_arch(arch_type, model_dir, cfg)

    if not model_dir or not os.path.isdir(model_dir):
        return jsonify({'success': False, 'error': '模型目录无效'}), 400

    _load_model_hash_data()

    exts = {
        'VR Arc': ('.pth',),
        'MDX-Net': ('.onnx', '.ckpt', '.ckptc'),
        'Demucs': ('.yaml',),
    }.get(arch_type, ('.onnx', '.ckpt', '.ckptc'))

    models = []
    try:
        for fname in os.listdir(model_dir):
            fpath = os.path.join(model_dir, fname)
            if not os.path.isfile(fpath):
                continue
            ext = os.path.splitext(fname)[1].lower()
            if ext not in exts:
                continue

            info = {
                'name': fname,
                'path': fpath,
                'size': os.path.getsize(fpath),
            }

            model_hash = _compute_model_hash(fpath)
            if model_hash:
                meta = _lookup_model_meta(model_hash, arch_type)
                if meta:
                    info['primary_stem'] = meta.get('primary_stem', '')
                    info['secondary_stem'] = _get_secondary_stem(meta.get('primary_stem', 'Vocals'))
                    info['has_meta'] = True
                else:
                    info['has_meta'] = False

            models.append(info)
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

    return jsonify({'success': True, 'models': models})


@bp.route('/separate', methods=['POST'])
def start_separate():
    """开始分离"""
    task_id = request.form.get('task_id', '')
    model_path = request.form.get('model_path', '')
    process_method = request.form.get('process_method', 'Demucs')
    primary_stem = request.form.get('primary_stem', '')
    demucs_stems = request.form.get('demucs_stems', 'All Stems')
    segment = request.form.get('segment', 'Default')
    mdx_overlap = request.form.get('mdx_overlap', '')
    vr_window_size = request.form.get('vr_window_size', '')
    vr_aggression = request.form.get('vr_aggression', '')
    is_primary_stem_only = request.form.get('is_primary_stem_only', '')
    is_secondary_stem_only = request.form.get('is_secondary_stem_only', '')
    is_gpu = request.form.get('is_gpu', '')
    is_normalization = request.form.get('is_normalization', '')

    if 'audio' not in request.files:
        return jsonify({'success': False, 'error': '请上传音频文件'}), 400
    audio_file = request.files['audio']
    if not audio_file.filename:
        return jsonify({'success': False, 'error': '请上传音频文件'}), 400
    if not model_path:
        return jsonify({'success': False, 'error': '请指定模型路径'}), 400
    if not os.path.isfile(model_path):
        return jsonify({'success': False, 'error': f'模型文件不存在: {model_path}'}), 400

    ext = os.path.splitext(audio_file.filename)[1].lower()
    if ext not in AUDIO_EXTS:
        return jsonify({'success': False, 'error': f'不支持的音频格式: {ext}'}), 400

    save_path = os.path.join(tempfile.gettempdir(), f'uvr_{uuid.uuid4().hex}{ext}')
    audio_file.save(save_path)

    # 架构名直接使用 UVR 常量名
    arch = process_method

    model_hash = _compute_model_hash(model_path)
    model_meta = _lookup_model_meta(model_hash,
        'VR' if arch == VR_ARCH else 'MDX' if arch == MDX_ARCH else 'Demucs')

    if not model_meta and primary_stem:
        model_meta = {'primary_stem': primary_stem}

    if model_meta and primary_stem and primary_stem != model_meta.get('primary_stem', ''):
        model_meta = dict(model_meta)
        model_meta['primary_stem'] = primary_stem

    if not model_meta and arch != DEMUCS_ARCH:
        try:
            os.remove(save_path)
        except OSError:
            pass
        return jsonify({
            'success': False,
            'error': '无法识别模型，请在模型列表中选择正确的主音轨类型',
        }), 400

    params = _load_config()
    # 前端传来的主/副音轨选项覆盖配置
    if is_primary_stem_only:
        params['is_primary_stem_only'] = is_primary_stem_only == '1'
    if is_secondary_stem_only:
        params['is_secondary_stem_only'] = is_secondary_stem_only == '1'
    if is_gpu:
        params['is_gpu'] = is_gpu == '1'
    if is_normalization:
        params['is_normalization'] = is_normalization == '1'
    # 架构特异参数覆盖
    if arch == DEMUCS_ARCH:
        params['demucs_stems'] = demucs_stems
        params['demucs_segment'] = segment
    elif arch == MDX_ARCH:
        params['mdx_segment_size'] = segment
        if mdx_overlap:
            try:
                params['mdx_overlap'] = float(mdx_overlap)
            except ValueError:
                pass
    elif arch == VR_ARCH:
        if vr_window_size:
            try:
                params['vr_window_size'] = int(vr_window_size)
            except ValueError:
                pass
        if vr_aggression:
            try:
                params['vr_aggression'] = int(vr_aggression)
            except ValueError:
                pass

    task_id = task_id or uuid.uuid4().hex
    _tasks[task_id] = {
        'status': 'processing',
        'progress': 0,
        'stems': [],
        'error': None,
        'device': '',
        'logs': [],
    }

    threading.Thread(
        target=_do_separate,
        args=(task_id, model_path, arch, save_path, params, model_meta),
        daemon=True,
    ).start()

    return jsonify({'success': True, 'task_id': task_id})


@bp.route('/status/<task_id>')
def status(task_id):
    task = _tasks.get(task_id)
    if not task:
        return jsonify({'success': False, 'error': '任务不存在'}), 404
    resp = {
        'success': True,
        'status': task['status'],
        'progress': task['progress'],
    }
    if task.get('device'):
        resp['device'] = task['device']
    resp['logs'] = task.get('logs', [])[-200:]
    if task['status'] == 'done':
        resp['stems'] = task['stems']
        resp['count'] = len(task['stems'])
    elif task['status'] == 'error':
        resp['error'] = task.get('error', '未知错误')
    return jsonify(resp)


@bp.route('/clear-logs/<task_id>', methods=['POST'])
def clear_logs(task_id):
    task = _tasks.get(task_id)
    if task:
        task['logs'] = []
    return jsonify({'success': True})


@bp.route('/download/<task_id>/<stem>')
def download_stem(task_id, stem):
    task = _tasks.get(task_id)
    if not task or task.get('status') != 'done':
        return jsonify({'error': '文件不存在'}), 404
    for s in task['stems']:
        if s['stem'] == stem and os.path.isfile(s['path']):
            return send_file(s['path'], mimetype='audio/wav',
                             as_attachment=True, download_name=s['filename'])
    return jsonify({'error': '文件不存在'}), 404


@bp.route('/download-all/<task_id>')
def download_all(task_id):
    task = _tasks.get(task_id)
    if not task or task.get('status') != 'done':
        return jsonify({'error': '无文件可下载'}), 404

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
        for s in task['stems']:
            if os.path.isfile(s['path']):
                zf.write(s['path'], s['filename'])
    buf.seek(0)
    return send_file(buf, mimetype='application/zip', as_attachment=True,
                     download_name='uvr_stems.zip')


@bp.route('/cleanup/<task_id>', methods=['POST'])
def cleanup(task_id):
    task = _tasks.pop(task_id, None)
    if not task:
        return jsonify({'success': True})
    export_path = os.path.join(tempfile.gettempdir(), f'uvr_out_{task_id}')
    if os.path.isdir(export_path):
        shutil.rmtree(export_path, ignore_errors=True)
    return jsonify({'success': True})
