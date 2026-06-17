# -*- coding: utf-8 -*-
"""
全局配置集中管理
"""
import os
# 基础目录
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# ====================  是否自动打开浏览器 ====================
AUTO_OPEN_BROWSER = False
# AUTO_OPEN_BROWSER = True
# ==================== 登录设置 ====================
LOGIN_ENABLED = False
# LOGIN_ENABLED = True
LOGIN_USERNAME = 'admin'
LOGIN_PASSWORD = '123456'
SECRET_KEY = 'LQkCU9XfKjxOSfIFZP7kDhwS57o2bvI4qVwZWMet4l8X3RNKnl12bgxvAy1BaByV'
# ==================== down-video 视频下载 ====================
TWITTER_COOKIE = os.path.join(BASE_DIR, "config", "dv_cookies", "x.com_cookies.txt")
BILIBILI_COOKIE = os.path.join(BASE_DIR, "config", "dv_cookies", "bilibili.com_cookies.txt")
INSTAGRAM_COOKIE = os.path.join(BASE_DIR, "config", "dv_cookies", "instagram.com_cookies.txt")
YOUTUBE_COOKIE = os.path.join(BASE_DIR, "config", "dv_cookies", "youtube.com_cookies.txt")
# 默认下载目录
DEFAULT_OUTPUT_DIR = r"D:\UserData\Desktop\xiazai"
# 默认代理端口
PROXY_URL = "socks5://127.0.0.1:10808"
# ==================== fen-ci 分词 ====================
JIEBA_DICT_FILE = os.path.join(BASE_DIR, 'config', 'fen_ci', 'jieba.txt')
TOKEN_FILE_PATH = os.path.join(BASE_DIR, 'config', 'fen_ci', 'usertoken.json')
# ==================== 服务端口 ====================
PORT = 5000

