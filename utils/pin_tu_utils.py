# -*- coding: utf-8 -*-
"""
pin-tu 图片浏览工具函数
"""
import os
import re


def atoi(text):
    """辅助函数：将文本中的数字部分转换为整数，用于排序"""
    return int(text) if text.isdigit() else text


def natural_keys(text):
    """自然排序键生成器"""
    return [atoi(c) for c in re.split(r'(\d+)', text)]


def find_media_in_folder(folder_path):
    """在指定文件夹中查找所有图片、视频和其他文件"""
    allowed_image_exts = {'.jpg', '.jpeg', '.png', '.gif', '.bmp', '.tiff', '.webp'}
    allowed_video_exts = {'.mp4', '.avi', '.mov', '.wmv', '.flv', '.webm', '.m4v', '.mkv', '.mpg', '.mpeg', '.3gp', '.3g2'}

    try:
        files = os.listdir(folder_path)
    except PermissionError:
        return [], [], []

    image_files = []
    video_files = []
    other_files = []
    for f in files:
        if not os.path.isfile(os.path.join(folder_path, f)):
            continue
        ext = os.path.splitext(f)[1].lower()
        if ext in allowed_image_exts:
            image_files.append(f)
        elif ext in allowed_video_exts:
            video_files.append(f)
        else:
            other_files.append(f)

    return sorted(image_files, key=natural_keys), sorted(video_files, key=natural_keys), sorted(other_files, key=natural_keys)


def get_subfolders(folder_path):
    """获取指定文件夹下的所有子文件夹"""
    try:
        items = os.listdir(folder_path)
    except PermissionError:
        return []
    subfolders = [item for item in items if os.path.isdir(os.path.join(folder_path, item))]
    return sorted(subfolders, key=natural_keys)


def build_breadcrumbs(subpath, base_url='/pin-tu/browse'):
    """构建面包屑导航数据
    base_url 示例: /pin-tu/browse/RDovRGF0YQ
    返回 URL 格式: {base_url}?p={累计子路径}
    """
    path_parts = [p for p in subpath.split('/') if p]
    breadcrumbs = [{'name': 'Home', 'url': base_url}]
    cumulative = ''
    for part in path_parts:
        cumulative += ('/' if cumulative else '') + part
        breadcrumbs.append({'name': part, 'url': f'{base_url}?p={cumulative}'})
    return breadcrumbs


def is_safe_path(root, target):
    """安全检查：防止路径穿越"""
    return os.path.normpath(target).startswith(os.path.normpath(root))
