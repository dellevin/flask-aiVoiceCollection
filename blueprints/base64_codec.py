# -*- coding: utf-8 -*-
"""
base64-de-in-code 编解码蓝图
"""
import base64
from flask import Blueprint, render_template, request, jsonify

bp = Blueprint('base64_codec', __name__, url_prefix='/base64-de-in-code')


def _fallback_decode(data_bytes):
    """降级解码：当 encoding_utils 不可用时使用"""
    for enc in ['utf-8', 'gbk', 'gb2312', 'cp936']:
        try:
            return data_bytes.decode(enc)
        except UnicodeDecodeError:
            continue
    return "解码失败，无法识别编码。"


@bp.route('/')
def page():
    return render_template('base64.html')


@bp.route('/decode', methods=['POST'])
def decode():
    data = request.get_json()
    base64_input = data.get('input_text', '').strip()
    if not base64_input:
        return jsonify({'success': False, 'error': '输入不能为空。'})
    try:
        decoded_bytes = base64.b64decode(base64_input)
    except Exception as e:
        return jsonify({'success': False, 'error': f'Base64 解码失败: {str(e)}'})

    try:
        from utils.encoding_utils import detect_and_decode
        decoded_result = detect_and_decode(decoded_bytes)
    except Exception:
        decoded_result = _fallback_decode(decoded_bytes)

    return jsonify({'success': True, 'result': decoded_result})


@bp.route('/encode', methods=['POST'])
def encode():
    data = request.get_json()
    text_to_encode = data.get('text_to_encode', '')
    encoding = data.get('encoding', 'utf-8')
    if text_to_encode is None:
        return jsonify({'success': False, 'error': '输入不能为空。'})
    try:
        encoded_bytes = text_to_encode.encode(encoding)
    except LookupError:
        return jsonify({'success': False, 'error': f'未知的编码格式: {encoding}'})
    except UnicodeEncodeError as e:
        return jsonify({'success': False, 'error': f'编码失败: {str(e)}'})
    encoded_string = base64.b64encode(encoded_bytes).decode('ascii')
    return jsonify({'success': True, 'result': encoded_string})
