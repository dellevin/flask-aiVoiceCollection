# -*- coding: utf-8 -*-
"""
audio-slicer 核心切割逻辑
移植自 audio-slicer 项目 (slicer2.py + gui/slicing_tasks.py)
仅依赖 numpy + soundfile
"""
import os
import numpy as np
import soundfile


def get_rms(y, *, frame_length=2048, hop_length=512, pad_mode="constant"):
    """计算 RMS（移植自 librosa）"""
    padding = (int(frame_length // 2), int(frame_length // 2))
    y = np.pad(y, padding, mode=pad_mode)
    axis = -1
    out_strides = y.strides + tuple([y.strides[axis]])
    x_shape_trimmed = list(y.shape)
    x_shape_trimmed[axis] -= frame_length - 1
    out_shape = tuple(x_shape_trimmed) + tuple([frame_length])
    xw = np.lib.stride_tricks.as_strided(y, shape=out_shape, strides=out_strides)
    if axis < 0:
        target_axis = axis - 1
    else:
        target_axis = axis + 1
    xw = np.moveaxis(xw, -1, target_axis)
    slices = [slice(None)] * xw.ndim
    slices[axis] = slice(0, None, hop_length)
    x = xw[tuple(slices)]
    power = np.mean(np.abs(x) ** 2, axis=-2, keepdims=True)
    return np.sqrt(power)


class Slicer:
    def __init__(self, sr, threshold=-40., min_length=5000, min_interval=300,
                 hop_size=20, max_sil_kept=5000):
        if not min_length >= min_interval >= hop_size:
            raise ValueError('min_length >= min_interval >= hop_size')
        if not max_sil_kept >= hop_size:
            raise ValueError('max_sil_kept >= hop_size')
        min_interval_f = sr * min_interval / 1000
        self.threshold = 10 ** (threshold / 20.)
        self.hop_size = round(sr * hop_size / 1000)
        self.win_size = min(round(min_interval_f), 4 * self.hop_size)
        self.min_length = round(sr * min_length / 1000 / self.hop_size)
        self.min_interval = round(min_interval_f / self.hop_size)
        self.max_sil_kept = round(sr * max_sil_kept / 1000 / self.hop_size)

    def _frame_to_sample(self, frame_index, total_samples):
        return min(total_samples, frame_index * self.hop_size)

    def slice_ranges(self, waveform):
        if len(waveform.shape) > 1:
            samples = waveform.mean(axis=0)
            total_samples = waveform.shape[1]
        else:
            samples = waveform
            total_samples = waveform.shape[0]
        if (samples.shape[0] + self.hop_size - 1) // self.hop_size <= self.min_length:
            return [(0, total_samples)]
        rms_list = get_rms(y=samples, frame_length=self.win_size, hop_length=self.hop_size).squeeze(0)
        return self.slice_ranges_from_rms(rms_list, total_samples)

    def slice_ranges_from_rms(self, rms_list, total_samples):
        if rms_list.shape[0] == 0:
            return [(0, total_samples)]
        total_frames = rms_list.shape[0]
        if total_frames <= self.min_length:
            return [(0, total_samples)]
        sil_tags = []
        silence_start = None
        clip_start = 0
        for i, rms in enumerate(rms_list):
            if rms < self.threshold:
                if silence_start is None:
                    silence_start = i
                continue
            if silence_start is None:
                continue
            is_leading_silence = silence_start == 0 and i > self.max_sil_kept
            need_slice_middle = i - silence_start >= self.min_interval and i - clip_start >= self.min_length
            if not is_leading_silence and not need_slice_middle:
                silence_start = None
                continue
            if i - silence_start <= self.max_sil_kept:
                pos = rms_list[silence_start: i + 1].argmin() + silence_start
                if silence_start == 0:
                    sil_tags.append((0, pos))
                else:
                    sil_tags.append((pos, pos))
                clip_start = pos
            elif i - silence_start <= self.max_sil_kept * 2:
                pos = rms_list[i - self.max_sil_kept: silence_start + self.max_sil_kept + 1].argmin()
                pos += i - self.max_sil_kept
                pos_l = rms_list[silence_start: silence_start + self.max_sil_kept + 1].argmin() + silence_start
                pos_r = rms_list[i - self.max_sil_kept: i + 1].argmin() + i - self.max_sil_kept
                if silence_start == 0:
                    sil_tags.append((0, pos_r))
                    clip_start = pos_r
                else:
                    sil_tags.append((min(pos_l, pos), max(pos_r, pos)))
                    clip_start = max(pos_r, pos)
            else:
                pos_l = rms_list[silence_start: silence_start + self.max_sil_kept + 1].argmin() + silence_start
                pos_r = rms_list[i - self.max_sil_kept: i + 1].argmin() + i - self.max_sil_kept
                if silence_start == 0:
                    sil_tags.append((0, pos_r))
                else:
                    sil_tags.append((pos_l, pos_r))
                clip_start = pos_r
            silence_start = None
        if silence_start is not None and total_frames - silence_start >= self.min_interval:
            silence_end = min(total_frames, silence_start + self.max_sil_kept)
            pos = rms_list[silence_start: silence_end + 1].argmin() + silence_start
            sil_tags.append((pos, total_frames + 1))
        if len(sil_tags) == 0:
            return [(0, total_samples)]
        ranges = []
        if sil_tags[0][0] > 0:
            ranges.append((0, self._frame_to_sample(sil_tags[0][0], total_samples)))
        for i in range(len(sil_tags) - 1):
            ranges.append((
                self._frame_to_sample(sil_tags[i][1], total_samples),
                self._frame_to_sample(sil_tags[i + 1][0], total_samples),
            ))
        if sil_tags[-1][1] < total_frames:
            ranges.append((self._frame_to_sample(sil_tags[-1][1], total_samples), total_samples))
        return ranges


def build_rms_list_from_file(source_file, slicer, read_size=131072):
    """流式计算 RMS 列表，避免大文件一次性加载到内存"""
    source_file.seek(0)
    pad = slicer.win_size // 2
    buffer = np.zeros(pad, dtype=np.float32)
    rms_parts = []
    while True:
        chunk = source_file.read(read_size, dtype="float32", always_2d=True)
        if len(chunk) == 0:
            break
        mono = chunk.mean(axis=1, dtype=np.float32)
        buffer = np.concatenate((buffer, mono.astype(np.float32, copy=False)))
        values, buffer = _consume_rms_frames(buffer, slicer)
        if values.size:
            rms_parts.append(values)
    buffer = np.concatenate((buffer, np.zeros(pad, dtype=np.float32)))
    values, _ = _consume_rms_frames(buffer, slicer)
    if values.size:
        rms_parts.append(values)
    if not rms_parts:
        return np.zeros(0, dtype=np.float32)
    return np.concatenate(rms_parts)


def _consume_rms_frames(buffer, slicer):
    if buffer.shape[0] < slicer.win_size:
        return np.zeros(0, dtype=np.float32), buffer
    usable = ((buffer.shape[0] - slicer.win_size) // slicer.hop_size) + 1
    window_view = np.lib.stride_tricks.sliding_window_view(buffer, slicer.win_size)
    windows = window_view[::slicer.hop_size][:usable]
    rms_values = np.sqrt(np.mean(np.abs(windows) ** 2, axis=1, dtype=np.float64)).astype(np.float32)
    remaining = buffer[usable * slicer.hop_size:]
    return rms_values, remaining


def analyze_audio(source_path, settings):
    """分析音频，返回 (ranges, sample_rate, channels, total_samples)"""
    with soundfile.SoundFile(source_path) as f:
        sr = f.samplerate
        ch = f.channels
        total = len(f)
        slicer = Slicer(sr=sr, **settings)
        if (total + slicer.hop_size - 1) // slicer.hop_size <= slicer.min_length:
            return [(0, total)], sr, ch, total
        rms_list = build_rms_list_from_file(f, slicer)
        ranges = slicer.slice_ranges_from_rms(rms_list, total)
        return ranges, sr, ch, total


def write_slice_range(source_path, output_path, sample_rate, channels, begin, end, chunk_size=65536):
    """流式写出单个切片"""
    frames_remaining = max(0, end - begin)
    with soundfile.SoundFile(source_path) as src, \
         soundfile.SoundFile(output_path, mode="w", samplerate=sample_rate, channels=channels) as dst:
        src.seek(begin)
        while frames_remaining > 0:
            block = src.read(min(chunk_size, frames_remaining), dtype="float32", always_2d=True)
            if len(block) == 0:
                break
            dst.write(block)
            frames_remaining -= len(block)
