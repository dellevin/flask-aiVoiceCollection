# -*- coding: utf-8 -*-
"""
GPT-SoVITS 后台工作进程
通过 stdin/stdout 二进制协议与 Flask 通信
sys.stdout 重定向到 stderr，防止 GPT-SoVITS 的 print() 污染通信通道
"""
import sys
import os
import json
import struct
import traceback

_comm_stdout = sys.stdout.buffer
sys.stdout = sys.stderr

sovits_dir = sys.argv[1] if len(sys.argv) > 1 else os.path.dirname(os.path.abspath(__file__))
os.chdir(sovits_dir)
if sovits_dir not in sys.path:
    sys.path.insert(0, sovits_dir)
gpt_sovits_dir = os.path.join(sovits_dir, 'GPT_SoVITS')
if gpt_sovits_dir not in sys.path:
    sys.path.insert(0, gpt_sovits_dir)


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
    config_yaml = sys.argv[2] if len(sys.argv) > 2 else 'GPT_SoVITS/configs/tts_infer.yaml'

    try:
        from GPT_SoVITS.TTS_infer_pack.TTS import TTS, TTS_Config

        yaml_path = os.path.join(sovits_dir, config_yaml)
        tts_config = TTS_Config(yaml_path)
        tts_engine = TTS(tts_config)
        version = getattr(tts_config, 'version', 'unknown')
        send_msg({'type': 'ready', 'version': version})
    except Exception as e:
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

        elif msg_type == 'reload_gpt':
            try:
                tts_engine.init_t2s_weights(req['path'])
                send_msg({'type': 'done'})
            except Exception as e:
                send_msg({'type': 'error', 'error': traceback.format_exc()})

        elif msg_type == 'reload_sovits':
            try:
                tts_engine.init_vits_weights(req['path'])
                send_msg({'type': 'done'})
            except Exception as e:
                send_msg({'type': 'error', 'error': traceback.format_exc()})

        elif msg_type == 'synthesize':
            try:
                inputs = req['inputs']
                output_path = req['output_path']
                tts_generator = tts_engine.run(inputs)
                sr, audio_data = next(tts_generator)

                if audio_data.dtype != np.int16:
                    if audio_data.max() <= 1.0:
                        audio_data = (audio_data * 32767).astype(np.int16)
                    else:
                        audio_data = audio_data.astype(np.int16)

                with wave.open(output_path, 'w') as wf:
                    wf.setnchannels(1)
                    wf.setsampwidth(2)
                    wf.setframerate(sr)
                    wf.writeframes(audio_data.tobytes())

                send_msg({'type': 'done', 'output_path': output_path})
            except Exception as e:
                send_msg({'type': 'error', 'error': traceback.format_exc()})


if __name__ == '__main__':
    main()
