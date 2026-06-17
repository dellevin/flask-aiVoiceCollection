# -*- coding: utf-8 -*-
"""
fen-ci 分词蓝图
"""
from flask import Blueprint, render_template, request, jsonify

from utils.fen_ci_utils import tokenizer, token_auth

bp = Blueprint('fen_ci', __name__, url_prefix='/fen-ci')


def _authenticate():
    """检查请求头中的 Authorization token"""
    auth_header = request.headers.get('Authorization')
    if not auth_header or not auth_header.startswith('Bearer '):
        return None, "缺少或格式错误的Authorization头。请使用 'Authorization: Bearer <your_token>'"
    token = auth_header.split(" ", 1)[1]
    user = token_auth.get_user(token)
    if not user:
        return None, "无效的Token"
    return user, None


@bp.route('/')
def page():
    return render_template('fen_ci.html')


@bp.route('/tokenize', methods=['POST'])
def tokenize():
    user, error_msg = _authenticate()
    if not user:
        return jsonify({'success': False, 'error': error_msg}), 401

    data = request.get_json()
    input_text = data.get('input_text', '')

    try:
        tokens = tokenizer.tokenize(input_text)
        stats = tokenizer.get_stats(tokens)
        return jsonify({
            'success': True,
            'tokens': tokens,
            'stats': stats,
            'original_text': input_text,
            'requested_by': user
        })
    except Exception as e:
        return jsonify({'success': False, 'error': f'分词处理失败: {str(e)}'}), 500
