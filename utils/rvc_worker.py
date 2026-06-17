# -*- coding: utf-8 -*-
"""
RVC 变声后台工作进程
通过 stdin/stdout 二进制协议与 Flask 通信
sys.stdout 重定向到 stderr，防止 RVC 的 print() 污染通信通道
"""
import sys
import os
import json
import struct
import traceback

_comm_stdout = sys.stdout.buffer
sys.stdout = sys.stderr

rvc_dir = sys.argv[1] if len(sys.argv) > 1 else os.path.dirname(os.path.abspath(__file__))
os.chdir(rvc_dir)
if rvc_dir not in sys.path:
    sys.path.insert(0, rvc_dir)

# 必须在 Config() 之前，防止 argparse 解析失败
sys.argv = sys.argv[:1]


def send_msg(obj):
    data = json.dumps(obj, ensure_ascii=False).encode('utf-8')
    _comm_stdout.write(struct.pack('<I', len(data)))
    _comm_stdout.write(data)
    _comm_stdout.flush()


def recv_msg():
    raw_len = sys.stdin.buffer.read(4)
    if not raw_len:
        return None
    msg_len = struct.unpack('<I', raw_len)[0]
    data = sys.stdin.buffer.read(msg_len)
    return json.loads(data.decode('utf-8'))


def main():
    try:
        from dotenv import load_dotenv
        load_dotenv(os.path.join(rvc_dir, '.env'))

        from configs.config import Config
        config = Config()

        from infer.modules.vc.modules import VC
        vc = VC(config)

        send_msg({'type': 'ready', 'device': str(config.device), 'is_half': config.is_half})
    except Exception:
        send_msg({'type': 'error', 'error': traceback.format_exc()})
        return

    import numpy as np
    import wave

    while True:
        req = recv_msg()
        if req is None:
            break

        msg_type = req.get('type')

        if msg_type == 'exit':
            break

        elif msg_type == 'load_model':
            try:
                vc.get_vc(req['model_name'])
                send_msg({'type': 'done'})
            except Exception:
                send_msg({'type': 'error', 'error': traceback.format_exc()})

        elif msg_type == 'convert':
            try:
                input_path = req['input_path']
                output_path = req['output_path']
                f0_up_key = req.get('f0_up_key', 0)
                f0_method = req.get('f0_method', 'rmvpe')
                file_index = req.get('file_index', '')
                index_rate = req.get('index_rate', 0.75)
                filter_radius = req.get('filter_radius', 3)
                resample_sr = req.get('resample_sr', 0)
                rms_mix_rate = req.get('rms_mix_rate', 0.25)
                protect = req.get('protect', 0.33)

                status, (sr, audio) = vc.vc_single(
                    sid=0,
                    input_audio_path=input_path,
                    f0_up_key=f0_up_key,
                    f0_file=None,
                    f0_method=f0_method,
                    file_index=file_index,
                    file_index2=None,
                    index_rate=index_rate,
                    filter_radius=filter_radius,
                    resample_sr=resample_sr,
                    rms_mix_rate=rms_mix_rate,
                    protect=protect,
                )

                if audio is None:
                    send_msg({'type': 'error', 'error': status or '变声失败'})
                else:
                    if audio.dtype != np.int16:
                        audio = audio.astype(np.int16)
                    with wave.open(output_path, 'w') as wf:
                        wf.setnchannels(1)
                        wf.setsampwidth(2)
                        wf.setframerate(sr)
                        wf.writeframes(audio.tobytes())
                    send_msg({'type': 'done', 'output_path': output_path, 'sample_rate': sr})
            except Exception:
                send_msg({'type': 'error', 'error': traceback.format_exc()})


if __name__ == '__main__':
    main()
