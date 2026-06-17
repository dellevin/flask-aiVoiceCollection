# -*- coding: utf-8 -*-
"""
pin-tu 图片浏览蓝图
用户自行指定浏览路径（root 用 URL-safe base64 编码放在 URL 中）
"""
import os
import shutil
import base64
from flask import Blueprint, render_template, send_from_directory, request, abort, jsonify

from utils.pin_tu_utils import (
    find_media_in_folder,
    get_subfolders,
    build_breadcrumbs,
    is_safe_path,
)
from utils.stats_db import (
    add_search, get_search_history, delete_search_history, clear_search_history,
    add_recent_path, get_recent_paths, delete_recent_path, clear_recent_paths,
)

bp = Blueprint('pin_tu', __name__, url_prefix='/pin-tu')


def _decode_root(b64_str):
    """URL-safe base64 解码为原始路径"""
    try:
        # URL-safe → standard base64
        padded = b64_str.replace('-', '+').replace('_', '/')
        # 补齐 =
        pad = len(padded) % 4
        if pad:
            padded += '=' * (4 - pad)
        return base64.b64decode(padded).decode('utf-8')
    except Exception:
        return None


def _encode_root(path_str):
    """路径编码为 URL-safe base64 字符串"""
    b = base64.b64encode(path_str.encode('utf-8')).decode('ascii')
    return b.replace('+', '-').replace('/', '_').rstrip('=')


@bp.route('/')
def index():
    return render_template('pin_tu.html', mode='index', root='', root_b64='')


@bp.route('/browse/<string:root_b64>')
def browse(root_b64):
    root_path = _decode_root(root_b64)
    if not root_path:
        abort(400)

    # 记录最近访问路径
    add_recent_path(root_path)

    subpath = request.args.get('p', '')
    mode = request.args.get('mode', 'list')
    if mode not in ['list', 'grid', 'manga']:
        mode = 'list'

    root_abs = os.path.normpath(root_path)
    if subpath:
        current_path = os.path.normpath(os.path.join(root_abs, subpath))
    else:
        current_path = root_abs

    if not os.path.isdir(root_abs):
        abort(404)
    if not is_safe_path(root_abs, current_path):
        abort(403)

    image_files, video_files, other_files = find_media_in_folder(current_path)
    subfolders = get_subfolders(current_path)
    breadcrumbs = build_breadcrumbs(subpath, base_url=f'/pin-tu/browse/{root_b64}')
    current_path_name = os.path.basename(current_path) if current_path != root_abs else os.path.basename(root_abs)

    if mode == 'manga':
        return render_template(
            'pin_tu_manga.html',
            images=image_files,
            current_path_name=current_path_name,
            current_subpath=subpath + '/' if subpath else '',
            root_b64=root_b64,
        )

    return render_template(
        'pin_tu.html',
        mode='browse',
        images=image_files,
        videos=video_files,
        files=other_files,
        subfolders=subfolders,
        current_path_name=current_path_name,
        current_subpath=subpath + '/' if subpath else '',
        breadcrumbs=breadcrumbs,
        view_mode=mode,
        is_manga_path='漫画' in subpath,
        root_b64=root_b64,
        root_display=root_path,
    )


@bp.route('/file-info/<string:root_b64>')
def file_info(root_b64):
    root_path = _decode_root(root_b64)
    if not root_path:
        abort(400)
    file_path = request.args.get('p', '')
    if not file_path:
        abort(400)

    root_abs = os.path.normpath(root_path)
    full_file = os.path.normpath(os.path.join(root_abs, file_path))
    if not is_safe_path(root_abs, full_file) or not os.path.isfile(full_file):
        abort(404)

    stat = os.stat(full_file)
    size = stat.st_size
    if size < 1024:
        size_str = f'{size} B'
    elif size < 1024 * 1024:
        size_str = f'{size / 1024:.1f} KB'
    elif size < 1024 * 1024 * 1024:
        size_str = f'{size / (1024 * 1024):.1f} MB'
    else:
        size_str = f'{size / (1024 * 1024 * 1024):.2f} GB'

    from datetime import datetime
    modified = datetime.fromtimestamp(stat.st_mtime).strftime('%Y-%m-%d %H:%M:%S')
    ext = os.path.splitext(file_path)[1].lstrip('.').upper()

    return {
        'name': os.path.basename(file_path),
        'size': size_str,
        'size_bytes': size,
        'type': ext or '未知',
        'modified': modified,
        'path': file_path,
    }


@bp.route('/media/<string:root_b64>')
def serve_media(root_b64):
    root_path = _decode_root(root_b64)
    if not root_path:
        abort(400)

    file_path = request.args.get('p', '')
    if not file_path:
        abort(400)

    root_abs = os.path.normpath(root_path)
    folder = os.path.dirname(file_path)
    filename = os.path.basename(file_path)
    base_dir = os.path.join(root_abs, folder)
    full_file = os.path.normpath(os.path.join(base_dir, filename))

    if not is_safe_path(root_abs, full_file):
        abort(403)
    return send_from_directory(base_dir, filename)


@bp.route('/rename/<string:root_b64>', methods=['POST'])
def rename(root_b64):
    root_path = _decode_root(root_b64)
    if not root_path:
        abort(400)

    data = request.get_json() or {}
    old_path = data.get('path', '')
    new_name = data.get('new_name', '').strip()
    if not old_path or not new_name:
        return {'success': False, 'error': '参数不完整'}, 400

    # 校验新文件名
    invalid = set(r'\/:*?"<>|')
    if any(c in invalid for c in new_name):
        return {'success': False, 'error': '文件名包含非法字符'}, 400

    root_abs = os.path.normpath(root_path)
    old_full = os.path.normpath(os.path.join(root_abs, old_path))
    if not is_safe_path(root_abs, old_full) or not os.path.exists(old_full):
        return {'success': False, 'error': '文件不存在'}, 404

    new_full = os.path.join(os.path.dirname(old_full), new_name)
    if os.path.exists(new_full) and os.path.normcase(new_full) != os.path.normcase(old_full):
        return {'success': False, 'error': '同名文件已存在'}, 400

    try:
        os.rename(old_full, new_full)
        return {'success': True, 'new_name': new_name}
    except Exception as e:
        return {'success': False, 'error': str(e)}, 500


@bp.route('/search-page/<string:root_b64>')
def search_page(root_b64):
    root_path = _decode_root(root_b64)
    if not root_path:
        abort(400)
    return render_template('pin_tu_search.html', root_b64=root_b64, root_display=root_path, query=request.args.get('q', ''))


@bp.route('/search/<string:root_b64>')
def search(root_b64):
    root_path = _decode_root(root_b64)
    if not root_path:
        abort(400)

    query = request.args.get('q', '').strip().lower()
    if not query or len(query) < 1:
        return {'results': []}

    root_abs = os.path.normpath(root_path)
    if not os.path.isdir(root_abs):
        abort(404)

    results = []
    max_results = 50
    for dirpath, dirnames, filenames in os.walk(root_abs):
        # 搜索文件夹名
        for d in list(dirnames):
            if query in d.lower():
                rel = os.path.relpath(os.path.join(dirpath, d), root_abs).replace('\\', '/')
                results.append({'name': d, 'path': rel, 'type': 'folder'})
                if len(results) >= max_results:
                    return {'results': results}
        # 搜索文件名
        for f in filenames:
            if query in f.lower():
                rel = os.path.relpath(os.path.join(dirpath, f), root_abs).replace('\\', '/')
                ext = os.path.splitext(f)[1].lstrip('.').upper()
                results.append({'name': f, 'path': rel, 'type': ext or 'file'})
                if len(results) >= max_results:
                    return {'results': results}

    return {'results': results}


# ===== 搜索历史 API =====

@bp.route('/api/search-history/<string:root_b64>')
def api_get_search_history(root_b64):
    root_path = _decode_root(root_b64)
    if not root_path:
        abort(400)
    records = get_search_history(root_path, limit=20)
    return jsonify({'history': records})


@bp.route('/api/search-history/<string:root_b64>', methods=['POST'])
def api_add_search(root_b64):
    root_path = _decode_root(root_b64)
    if not root_path:
        abort(400)
    data = request.get_json() or {}
    keyword = data.get('keyword', '').strip()
    result_count = data.get('result_count', 0)
    if keyword:
        add_search(keyword, root_path, result_count)
    return jsonify({'success': True})


@bp.route('/api/search-history/<string:root_b64>', methods=['DELETE'])
def api_delete_search_history(root_b64):
    root_path = _decode_root(root_b64)
    if not root_path:
        abort(400)
    data = request.get_json() or {}
    if data.get('clear'):
        clear_search_history(root_path)
    else:
        ids = data.get('ids', [])
        if ids:
            delete_search_history(ids)
    return jsonify({'success': True})


# ===== 最近访问路径 API =====

@bp.route('/api/recent-paths')
def api_get_recent_paths():
    records = get_recent_paths(limit=8)
    return jsonify({'paths': records})


@bp.route('/api/recent-paths', methods=['POST'])
def api_add_recent_path():
    data = request.get_json() or {}
    path = data.get('path', '').strip()
    if path:
        add_recent_path(path)
    return jsonify({'success': True})


@bp.route('/api/recent-paths', methods=['DELETE'])
def api_delete_recent_path():
    data = request.get_json() or {}
    if data.get('clear'):
        clear_recent_paths()
    else:
        path = data.get('path', '')
        if path:
            delete_recent_path(path)
    return jsonify({'success': True})


# ===== 新建文件夹 =====

@bp.route('/create-folder/<string:root_b64>', methods=['POST'])
def create_folder(root_b64):
    root_path = _decode_root(root_b64)
    if not root_path:
        abort(400)
    data = request.get_json() or {}
    subpath = data.get('path', '')
    name = data.get('name', '').strip()
    if not name:
        return {'success': False, 'error': '名称不能为空'}, 400

    invalid = set(r'\/:*?"<>|')
    if any(c in invalid for c in name):
        return {'success': False, 'error': '名称包含非法字符'}, 400

    root_abs = os.path.normpath(root_path)
    if subpath:
        parent = os.path.normpath(os.path.join(root_abs, subpath))
    else:
        parent = root_abs
    if not is_safe_path(root_abs, parent) or not os.path.isdir(parent):
        return {'success': False, 'error': '父目录不存在'}, 400

    target = os.path.join(parent, name)
    if os.path.exists(target):
        return {'success': False, 'error': '同名文件夹已存在'}, 400

    try:
        os.makedirs(target)
        return {'success': True, 'name': name}
    except Exception as e:
        return {'success': False, 'error': str(e)}, 500


# ===== 移动文件/文件夹 =====

@bp.route('/move/<string:root_b64>', methods=['POST'])
def move_item(root_b64):
    root_path = _decode_root(root_b64)
    if not root_path:
        abort(400)
    data = request.get_json() or {}
    source = data.get('source', '')
    dest = data.get('dest', '')  # 目标文件夹的相对路径，空字符串表示根目录
    if not source:
        return {'success': False, 'error': '缺少源路径'}, 400

    root_abs = os.path.normpath(root_path)
    src_full = os.path.normpath(os.path.join(root_abs, source))
    if not is_safe_path(root_abs, src_full) or not os.path.exists(src_full):
        return {'success': False, 'error': '源文件不存在'}, 404

    if dest:
        dest_dir = os.path.normpath(os.path.join(root_abs, dest))
    else:
        dest_dir = root_abs
    if not is_safe_path(root_abs, dest_dir) or not os.path.isdir(dest_dir):
        return {'success': False, 'error': '目标目录不存在'}, 400

    # 不能移动到自身或自身子目录
    norm_src = os.path.normcase(src_full)
    norm_dest = os.path.normcase(dest_dir)
    if norm_dest.startswith(norm_src + os.sep) or norm_dest == norm_src:
        return {'success': False, 'error': '不能移动到自身或子目录'}, 400

    item_name = os.path.basename(src_full)
    new_full = os.path.join(dest_dir, item_name)
    if os.path.exists(new_full) and os.path.normcase(new_full) != norm_src:
        return {'success': False, 'error': '目标位置已存在同名项目'}, 400

    try:
        shutil.move(src_full, new_full)
        return {'success': True, 'name': item_name}
    except Exception as e:
        return {'success': False, 'error': str(e)}, 500


# ===== 删除文件/文件夹 =====

@bp.route('/delete/<string:root_b64>', methods=['POST'])
def delete_item(root_b64):
    root_path = _decode_root(root_b64)
    if not root_path:
        abort(400)
    data = request.get_json() or {}
    source = data.get('source', '')
    if not source:
        return {'success': False, 'error': '缺少路径'}, 400

    root_abs = os.path.normpath(root_path)
    full = os.path.normpath(os.path.join(root_abs, source))
    if not is_safe_path(root_abs, full) or not os.path.exists(full):
        return {'success': False, 'error': '文件不存在'}, 404

    name = os.path.basename(full)
    try:
        if os.path.isdir(full):
            shutil.rmtree(full)
        else:
            os.remove(full)
        return {'success': True, 'name': name}
    except Exception as e:
        return {'success': False, 'error': str(e)}, 500


# ===== 列出子文件夹（供移动弹窗使用）=====

@bp.route('/list-folders/<string:root_b64>')
def list_folders(root_b64):
    root_path = _decode_root(root_b64)
    if not root_path:
        abort(400)
    subpath = request.args.get('p', '')

    root_abs = os.path.normpath(root_path)
    if subpath:
        current = os.path.normpath(os.path.join(root_abs, subpath))
    else:
        current = root_abs

    if not is_safe_path(root_abs, current) or not os.path.isdir(current):
        abort(404)

    subfolders = get_subfolders(current)
    # 父目录
    parent = ''
    if subpath:
        parts = subpath.rstrip('/').split('/')
        parent = '/'.join(parts[:-1]) if len(parts) > 1 else ''

    return jsonify({
        'current': subpath,
        'parent': parent,
        'folders': [{'name': f, 'path': (subpath + '/' + f).strip('/')} for f in subfolders]
    })
