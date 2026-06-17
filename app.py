# -*- coding: utf-8 -*-
"""
整合工具集主入口
只负责创建 Flask 应用、注册蓝图和启动服务
"""
import sys
import os
import json
import time
from datetime import datetime
from flask import Flask, render_template, jsonify, g, request, session, redirect

from config import PORT, LOGIN_ENABLED, LOGIN_USERNAME, LOGIN_PASSWORD, SECRET_KEY
from blueprints import pin_tu_bp, base64_bp, down_video_bp, fen_ci_bp, content_tag_bp, chmod_calc_bp, json_format_bp, qr_code_bp, http_status_bp, url_parser_bp, token_gen_bp, sovits_tts_bp, stt_bp, ai_dubbing_bp, rvc_bp, audio_slicer_bp, uvr_sep_bp, mp4_to_audio_bp
from utils.stats_db import init_db, record_call, get_total_count, get_avg_duration, get_daily_counts, get_monthly_counts, get_available_years, get_available_months, get_daily_counts_by_month, get_hourly_counts, get_endpoint_stats, get_today_count, get_yesterday_count, get_month_count, get_success_rate, get_duration_distribution, get_slowest_endpoints, get_week_compare, get_recent_calls, get_active_days
from utils.stats_db import ALLOWED_TABLES, get_table_info, get_table_rows, get_table_count, insert_row, update_row, delete_rows, clear_table

TOOL_COUNT = 18


def create_app():
    # 打包模式下，模板和静态文件在 sys._MEIPASS 临时目录
    if getattr(sys, 'frozen', False):
        base = sys._MEIPASS
        app = Flask(__name__,
                    template_folder=os.path.join(base, 'templates'),
                    static_folder=os.path.join(base, 'static'))
    else:
        app = Flask(__name__)

    init_db()
    app.secret_key = SECRET_KEY

    # 登录/登出
    @app.route('/login', methods=['GET', 'POST'])
    def login():
        if not LOGIN_ENABLED:
            return redirect('/')
        if request.method == 'POST':
            if request.form.get('username') == LOGIN_USERNAME and request.form.get('password') == LOGIN_PASSWORD:
                session['logged_in'] = True
                return redirect('/')
            return render_template('login.html', error='用户名或密码错误')
        return render_template('login.html')

    @app.route('/logout')
    def logout():
        session.pop('logged_in', None)
        return redirect('/login')

    # 主页
    @app.route('/')
    def index():
        return render_template('index.html')

    # 数据管理页面
    @app.route('/data-manage')
    def data_manage():
        return render_template('data_manage.html')

    # 侧边栏功能显示配置
    SIDEBAR_CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'config', 'sidebar_config.json')

    @app.route('/api/sidebar-config', methods=['GET'])
    def get_sidebar_config():
        if os.path.exists(SIDEBAR_CONFIG_PATH):
            with open(SIDEBAR_CONFIG_PATH, 'r', encoding='utf-8') as f:
                return jsonify(json.load(f))
        return jsonify({'hidden_features': []})

    @app.route('/api/sidebar-config', methods=['POST'])
    def save_sidebar_config():
        data = request.get_json(force=True)
        os.makedirs(os.path.dirname(SIDEBAR_CONFIG_PATH), exist_ok=True)
        with open(SIDEBAR_CONFIG_PATH, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return jsonify({'success': True})

    # 仪表盘统计接口
    @app.route('/api/stats')
    def api_stats():
        year = request.args.get('year')
        if not year:
            year = datetime.now().strftime('%Y')
        return jsonify({
            'tool_count': TOOL_COUNT,
            'total_calls': get_total_count(),
            'avg_duration': get_avg_duration(),
            'daily': get_daily_counts(30),
            'years': get_available_years(),
            'months': get_available_months(year)
        })

    # 某月每日调用统计
    @app.route('/api/stats/daily-by-month')
    def api_stats_daily_by_month():
        year = request.args.get('year', datetime.now().strftime('%Y'))
        month = request.args.get('month', datetime.now().strftime('%m'))
        return jsonify({
            'year': year,
            'month': month,
            'daily': get_daily_counts_by_month(year, int(month))
        })

    # 每天按小时统计
    @app.route('/api/stats/hourly')
    def api_stats_hourly():
        date_str = request.args.get('date')
        if not date_str:
            date_str = datetime.now().strftime('%Y-%m-%d')
        return jsonify({
            'date': date_str,
            'hourly': get_hourly_counts(date_str)
        })

    # 接口调用分布
    @app.route('/api/stats/endpoints')
    def api_stats_endpoints():
        date = request.args.get('date')
        return jsonify({'endpoints': get_endpoint_stats(date)})

    # 高级统计数据
    @app.route('/api/stats/advanced')
    def api_stats_advanced():
        return jsonify({
            'today_count': get_today_count(),
            'yesterday_count': get_yesterday_count(),
            'month_count': get_month_count(),
            'success_rate': get_success_rate(),
            'duration_distribution': get_duration_distribution(),
            'slowest_endpoints': get_slowest_endpoints(),
            'week_compare': get_week_compare(),
            'recent_calls': get_recent_calls(),
            'active_days': get_active_days()
        })

    # ===== 数据管理 API =====

    @app.route('/api/db/tables')
    def api_db_tables():
        return jsonify({'tables': get_table_info()})

    @app.route('/api/db/<table>')
    def api_db_list(table):
        if table not in ALLOWED_TABLES:
            return jsonify({'success': False, 'error': '不允许的表名'}), 400
        page = int(request.args.get('page', 1))
        page_size = int(request.args.get('page_size', 20))
        search = request.args.get('search', '').strip()
        rows = get_table_rows(table, page, page_size, search)
        total = get_table_count(table, search)
        return jsonify({'success': True, 'rows': rows, 'total': total, 'page': page, 'page_size': page_size})

    @app.route('/api/db/<table>', methods=['POST'])
    def api_db_insert(table):
        if table not in ALLOWED_TABLES:
            return jsonify({'success': False, 'error': '不允许的表名'}), 400
        data = request.get_json()
        if not data:
            return jsonify({'success': False, 'error': '缺少数据'}), 400
        new_id = insert_row(table, data)
        if new_id:
            return jsonify({'success': True, 'id': new_id})
        return jsonify({'success': False, 'error': '插入失败'}), 400

    @app.route('/api/db/<table>/<int:row_id>', methods=['PUT'])
    def api_db_update(table, row_id):
        if table not in ALLOWED_TABLES:
            return jsonify({'success': False, 'error': '不允许的表名'}), 400
        data = request.get_json()
        if not data:
            return jsonify({'success': False, 'error': '缺少数据'}), 400
        if update_row(table, row_id, data):
            return jsonify({'success': True})
        return jsonify({'success': False, 'error': '更新失败'}), 400

    @app.route('/api/db/<table>/<int:row_id>', methods=['DELETE'])
    def api_db_delete(table, row_id):
        if table not in ALLOWED_TABLES:
            return jsonify({'success': False, 'error': '不允许的表名'}), 400
        deleted = delete_rows(table, [row_id])
        return jsonify({'success': deleted > 0})

    @app.route('/api/db/<table>/delete-batch', methods=['POST'])
    def api_db_delete_batch(table):
        if table not in ALLOWED_TABLES:
            return jsonify({'success': False, 'error': '不允许的表名'}), 400
        data = request.get_json()
        ids = data.get('ids', []) if data else []
        if not ids:
            return jsonify({'success': False, 'error': '未选择记录'}), 400
        deleted = delete_rows(table, ids)
        return jsonify({'success': True, 'deleted': deleted})

    @app.route('/api/db/<table>/clear', methods=['POST'])
    def api_db_clear(table):
        if table not in ALLOWED_TABLES:
            return jsonify({'success': False, 'error': '不允许的表名'}), 400
        deleted = clear_table(table)
        return jsonify({'success': True, 'deleted': deleted})

    # 登录验证
    @app.before_request
    def _check_auth():
        if not LOGIN_ENABLED:
            return
        path = request.path
        if path == '/login' or path.startswith('/static/'):
            return
        if not session.get('logged_in'):
            if path.startswith('/api/'):
                return jsonify({'error': '未登录'}), 401
            return redirect('/login')

    # 请求计时
    @app.before_request
    def _start_timer():
        g.start_time = time.time()

    # 请求追踪
    @app.after_request
    def _track_request(response):
        path = request.path
        if path == '/' or path.startswith('/static/') or path.startswith('/api/'):
            return response
        if request.method != 'POST':
            return response
        duration_ms = (time.time() - g.start_time) * 1000
        record_call(path, request.method, response.status_code, duration_ms)
        return response

    # 注册蓝图
    app.register_blueprint(pin_tu_bp)
    app.register_blueprint(base64_bp)
    app.register_blueprint(down_video_bp)
    app.register_blueprint(fen_ci_bp)
    app.register_blueprint(content_tag_bp)
    app.register_blueprint(chmod_calc_bp)
    app.register_blueprint(json_format_bp)
    app.register_blueprint(qr_code_bp)
    app.register_blueprint(http_status_bp)
    app.register_blueprint(url_parser_bp)
    app.register_blueprint(token_gen_bp)
    app.register_blueprint(sovits_tts_bp)
    app.register_blueprint(stt_bp)
    app.register_blueprint(ai_dubbing_bp)
    app.register_blueprint(rvc_bp)
    app.register_blueprint(audio_slicer_bp)
    app.register_blueprint(uvr_sep_bp)
    app.register_blueprint(mp4_to_audio_bp)

    return app

app = create_app()

if __name__ == '__main__':
    from config import AUTO_OPEN_BROWSER
    if AUTO_OPEN_BROWSER:
        import webbrowser
        import threading as _t
        _t.Timer(1, webbrowser.open, args=[f'http://127.0.0.1:{PORT}']).start()
    app.run(debug=True, host='0.0.0.0', port=PORT, threaded=True)
