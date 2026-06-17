# -*- coding: utf-8 -*-
"""
接口调用统计模块
使用 SQLite 存储调用记录，支持按日/月查询
"""
import sqlite3
import os
from datetime import datetime, timedelta

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'stats.db')
try:
    from config import BASE_DIR
    DB_PATH = os.path.join(BASE_DIR, 'stats.db')
except ImportError:
    pass


def _get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = _get_conn()
    conn.execute('''
        CREATE TABLE IF NOT EXISTS api_calls (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            endpoint TEXT NOT NULL,
            method TEXT NOT NULL,
            status_code INTEGER,
            duration_ms REAL,
            called_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    conn.execute('''
        CREATE TABLE IF NOT EXISTS search_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            keyword TEXT NOT NULL,
            root_path TEXT NOT NULL,
            result_count INTEGER DEFAULT 0,
            searched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    conn.execute('''
        CREATE TABLE IF NOT EXISTS recent_paths (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            path TEXT NOT NULL UNIQUE,
            last_used TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    conn.execute('''
        CREATE TABLE IF NOT EXISTS content_tag_settings (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            system_msg TEXT,
            user_msg TEXT,
            max_tags INTEGER DEFAULT 10,
            min_length INTEGER DEFAULT 2,
            max_length INTEGER DEFAULT 6
        )
    ''')
    conn.execute('INSERT OR IGNORE INTO content_tag_settings (id) VALUES (1)')
    conn.commit()
    conn.close()


def record_call(endpoint, method, status_code, duration_ms):
    conn = _get_conn()
    conn.execute(
        'INSERT INTO api_calls (endpoint, method, status_code, duration_ms) VALUES (?, ?, ?, ?)',
        (endpoint, method, status_code, round(duration_ms, 2))
    )
    conn.commit()
    conn.close()


def get_total_count():
    conn = _get_conn()
    row = conn.execute('SELECT COUNT(*) as cnt FROM api_calls').fetchone()
    conn.close()
    return row['cnt']


def get_avg_duration():
    conn = _get_conn()
    row = conn.execute('SELECT AVG(duration_ms) as avg_dur FROM api_calls').fetchone()
    conn.close()
    return round(row['avg_dur'] or 0, 2)


def get_daily_counts(days=30):
    since = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')
    conn = _get_conn()
    rows = conn.execute(
        "SELECT DATE(called_at, '+8 hours') as day, COUNT(*) as cnt "
        "FROM api_calls WHERE DATE(called_at, '+8 hours') >= ? "
        "GROUP BY day ORDER BY day",
        (since,)
    ).fetchall()
    conn.close()
    return [{'date': r['day'], 'count': r['cnt']} for r in rows]


def get_monthly_counts(year=None):
    conn = _get_conn()
    if year:
        rows = conn.execute(
            "SELECT strftime('%Y-%m', called_at, '+8 hours') as month, COUNT(*) as cnt "
            "FROM api_calls WHERE strftime('%Y', called_at, '+8 hours') = ? "
            "GROUP BY month ORDER BY month",
            (str(year),)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT strftime('%Y-%m', called_at, '+8 hours') as month, COUNT(*) as cnt "
            "FROM api_calls GROUP BY month ORDER BY month"
        ).fetchall()
    conn.close()
    return [{'month': r['month'], 'count': r['cnt']} for r in rows]


def get_available_years():
    conn = _get_conn()
    rows = conn.execute(
        "SELECT DISTINCT strftime('%Y', called_at, '+8 hours') as year "
        "FROM api_calls ORDER BY year DESC"
    ).fetchall()
    conn.close()
    return [r['year'] for r in rows]


def get_available_months(year):
    conn = _get_conn()
    rows = conn.execute(
        "SELECT DISTINCT strftime('%m', called_at, '+8 hours') as month "
        "FROM api_calls WHERE strftime('%Y', called_at, '+8 hours') = ? "
        "ORDER BY month",
        (str(year),)
    ).fetchall()
    conn.close()
    return [r['month'] for r in rows]


def get_daily_counts_by_month(year, month):
    ym_prefix = f'{year}-{month:02d}'
    conn = _get_conn()
    rows = conn.execute(
        "SELECT strftime('%d', called_at, '+8 hours') as day, COUNT(*) as cnt "
        "FROM api_calls WHERE strftime('%Y-%m', called_at, '+8 hours') = ? "
        "GROUP BY day ORDER BY day",
        (ym_prefix,)
    ).fetchall()
    conn.close()
    day_map = {r['day']: r['cnt'] for r in rows}
    # 获取该月天数
    import calendar
    _, days_in_month = calendar.monthrange(int(year), int(month))
    return [{'date': f'{ym_prefix}-{d:02d}', 'day': str(d), 'count': day_map.get(f'{d:02d}', 0)} for d in range(1, days_in_month + 1)]


def get_hourly_counts(date_str):
    conn = _get_conn()
    rows = conn.execute(
        "SELECT strftime('%H', called_at, '+8 hours') as hour, COUNT(*) as cnt "
        "FROM api_calls WHERE DATE(called_at, '+8 hours') = ? "
        "GROUP BY hour ORDER BY hour",
        (date_str,)
    ).fetchall()
    conn.close()
    hour_map = {r['hour']: r['cnt'] for r in rows}
    return [{'hour': f'{h:02d}', 'count': hour_map.get(f'{h:02d}', 0)} for h in range(24)]


def get_endpoint_stats(date=None):
    conn = _get_conn()
    if date:
        rows = conn.execute(
            "SELECT endpoint, COUNT(*) as cnt, AVG(duration_ms) as avg_dur "
            "FROM api_calls WHERE DATE(called_at, '+8 hours') = ? "
            "GROUP BY endpoint ORDER BY cnt DESC",
            (date,)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT endpoint, COUNT(*) as cnt, AVG(duration_ms) as avg_dur "
            "FROM api_calls GROUP BY endpoint ORDER BY cnt DESC"
        ).fetchall()
    conn.close()
    return [{'endpoint': r['endpoint'], 'count': r['cnt'], 'avg_duration': round(r['avg_dur'] or 0, 2)} for r in rows]


def get_today_count():
    conn = _get_conn()
    row = conn.execute(
        "SELECT COUNT(*) as cnt FROM api_calls WHERE DATE(called_at, '+8 hours') = DATE('now', '+8 hours')"
    ).fetchone()
    conn.close()
    return row['cnt']


def get_month_count():
    conn = _get_conn()
    row = conn.execute(
        "SELECT COUNT(*) as cnt FROM api_calls WHERE strftime('%Y-%m', called_at, '+8 hours') = strftime('%Y-%m', 'now', '+8 hours')"
    ).fetchone()
    conn.close()
    return row['cnt']


def get_yesterday_count():
    conn = _get_conn()
    row = conn.execute(
        "SELECT COUNT(*) as cnt FROM api_calls WHERE DATE(called_at, '+8 hours') = DATE('now', '+8 hours', '-1 day')"
    ).fetchone()
    conn.close()
    return row['cnt']


def get_success_rate():
    conn = _get_conn()
    total = conn.execute("SELECT COUNT(*) as cnt FROM api_calls").fetchone()['cnt']
    if total == 0:
        conn.close()
        return 100.0
    success = conn.execute("SELECT COUNT(*) as cnt FROM api_calls WHERE status_code >= 200 AND status_code < 300").fetchone()['cnt']
    conn.close()
    return round(success / total * 100, 1)


def get_duration_distribution():
    conn = _get_conn()
    ranges = [
        ('<100ms', 0, 100),
        ('100-500ms', 100, 500),
        ('0.5-1s', 500, 1000),
        ('1-3s', 1000, 3000),
        ('3-10s', 3000, 10000),
        ('>10s', 10000, 999999999),
    ]
    result = []
    for label, lo, hi in ranges:
        cnt = conn.execute(
            "SELECT COUNT(*) as cnt FROM api_calls WHERE duration_ms >= ? AND duration_ms < ?", (lo, hi)
        ).fetchone()['cnt']
        result.append({'label': label, 'count': cnt})
    conn.close()
    return result


def get_slowest_endpoints(limit=5):
    conn = _get_conn()
    rows = conn.execute(
        "SELECT endpoint, AVG(duration_ms) as avg_dur, MAX(duration_ms) as max_dur, COUNT(*) as cnt "
        "FROM api_calls GROUP BY endpoint ORDER BY avg_dur DESC LIMIT ?", (limit,)
    ).fetchall()
    conn.close()
    return [{
        'endpoint': r['endpoint'],
        'avg_duration': round(r['avg_dur'] or 0, 2),
        'max_duration': round(r['max_dur'] or 0, 2),
        'count': r['cnt']
    } for r in rows]


def get_week_compare():
    conn = _get_conn()
    this_week = []
    last_week = []
    for i in range(7):
        # 本周: 今天往前 i 天
        row = conn.execute(
            "SELECT COUNT(*) as cnt FROM api_calls WHERE DATE(called_at, '+8 hours') = DATE('now', '+8 hours', ?)",
            (f'-{i} day',)
        ).fetchone()
        this_week.append(row['cnt'])
        # 上周: 本周对应天数再往前 7 天
        row = conn.execute(
            "SELECT COUNT(*) as cnt FROM api_calls WHERE DATE(called_at, '+8 hours') = DATE('now', '+8 hours', ?)",
            (f'-{i + 7} day',)
        ).fetchone()
        last_week.append(row['cnt'])
    conn.close()
    # 返回按周一到周日顺序（今天是 index 0，需要反转）
    this_week.reverse()
    last_week.reverse()
    # 生成日期标签
    from datetime import datetime, timedelta
    today = datetime.now()
    weekday = today.weekday()  # 0=周一
    labels = []
    for i in range(7):
        d = today - timedelta(days=weekday - i)
        labels.append(d.strftime('%m-%d'))
    return {'labels': labels, 'this_week': this_week, 'last_week': last_week}


def get_recent_calls(limit=20):
    conn = _get_conn()
    rows = conn.execute(
        "SELECT endpoint, method, status_code, duration_ms, "
        "strftime('%Y-%m-%d %H:%M:%S', called_at, '+8 hours') as called_at "
        "FROM api_calls ORDER BY called_at DESC LIMIT ?", (limit,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_active_days():
    conn = _get_conn()
    row = conn.execute(
        "SELECT COUNT(DISTINCT DATE(called_at, '+8 hours')) as cnt FROM api_calls"
    ).fetchone()
    # 连续活跃天数
    rows = conn.execute(
        "SELECT DISTINCT DATE(called_at, '+8 hours') as day FROM api_calls ORDER BY day DESC"
    ).fetchall()
    conn.close()
    total_days = row['cnt']
    streak = 0
    if rows:
        from datetime import datetime, timedelta
        today = datetime.now().strftime('%Y-%m-%d')
        expected = today
        for r in rows:
            if r['day'] == expected:
                streak += 1
                d = datetime.strptime(expected, '%Y-%m-%d') - timedelta(days=1)
                expected = d.strftime('%Y-%m-%d')
            else:
                break
    return {'total_days': total_days, 'streak': streak}


# ===== 搜索历史 =====

def add_search(keyword, root_path, result_count):
    conn = _get_conn()
    conn.execute(
        'INSERT INTO search_history (keyword, root_path, result_count) VALUES (?, ?, ?)',
        (keyword, root_path, result_count)
    )
    conn.commit()
    conn.close()


def get_search_history(root_path, limit=20):
    conn = _get_conn()
    rows = conn.execute(
        "SELECT id, keyword, result_count, strftime('%Y-%m-%d %H:%M', searched_at, '+8 hours') as searched_at "
        "FROM search_history WHERE root_path = ? ORDER BY searched_at DESC LIMIT ?",
        (root_path, limit)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def delete_search_history(ids):
    if not ids:
        return
    conn = _get_conn()
    placeholders = ','.join('?' for _ in ids)
    conn.execute(f'DELETE FROM search_history WHERE id IN ({placeholders})', ids)
    conn.commit()
    conn.close()


def clear_search_history(root_path):
    conn = _get_conn()
    conn.execute('DELETE FROM search_history WHERE root_path = ?', (root_path,))
    conn.commit()
    conn.close()


# ===== 最近访问路径 =====

def add_recent_path(path):
    conn = _get_conn()
    conn.execute(
        'INSERT INTO recent_paths (path, last_used) VALUES (?, CURRENT_TIMESTAMP) '
        'ON CONFLICT(path) DO UPDATE SET last_used = CURRENT_TIMESTAMP',
        (path,)
    )
    conn.commit()
    conn.close()


def get_recent_paths(limit=8):
    conn = _get_conn()
    rows = conn.execute(
        "SELECT id, path, strftime('%Y-%m-%d %H:%M', last_used, '+8 hours') as last_used "
        "FROM recent_paths ORDER BY last_used DESC LIMIT ?",
        (limit,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def delete_recent_path(path):
    conn = _get_conn()
    conn.execute('DELETE FROM recent_paths WHERE path = ?', (path,))
    conn.commit()
    conn.close()


def clear_recent_paths():
    conn = _get_conn()
    conn.execute('DELETE FROM recent_paths')
    conn.commit()
    conn.close()


# ===== 通用 CRUD（数据管理） =====

ALLOWED_TABLES = {
    'api_calls': {
        'columns': ['endpoint', 'method', 'status_code', 'duration_ms', 'called_at'],
        'searchable': ['endpoint', 'method'],
    },
    'search_history': {
        'columns': ['keyword', 'root_path', 'result_count', 'searched_at'],
        'searchable': ['keyword', 'root_path'],
    },
    'recent_paths': {
        'columns': ['path', 'last_used'],
        'searchable': ['path'],
    },
}

DISPLAY_COLUMNS = {
    'api_calls': 'id, endpoint, method, status_code, duration_ms, strftime("%Y-%m-%d %H:%M:%S", called_at, "+8 hours") as called_at',
    'search_history': 'id, keyword, root_path, result_count, strftime("%Y-%m-%d %H:%M", searched_at, "+8 hours") as searched_at',
    'recent_paths': 'id, path, strftime("%Y-%m-%d %H:%M", last_used, "+8 hours") as last_used',
}


def get_table_info():
    conn = _get_conn()
    result = []
    for table in ALLOWED_TABLES:
        row = conn.execute(f'SELECT COUNT(*) as cnt FROM {table}').fetchone()
        result.append({'table': table, 'count': row['cnt']})
    conn.close()
    return result


def get_table_rows(table, page=1, page_size=20, search=''):
    if table not in ALLOWED_TABLES:
        return []
    cols = DISPLAY_COLUMNS[table]
    offset = (page - 1) * page_size
    conn = _get_conn()
    if search:
        searchable = ALLOWED_TABLES[table]['searchable']
        conditions = ' OR '.join(f'{col} LIKE ?' for col in searchable)
        params = [f'%{search}%'] * len(searchable) + [page_size, offset]
        rows = conn.execute(
            f'SELECT {cols} FROM {table} WHERE {conditions} ORDER BY id DESC LIMIT ? OFFSET ?',
            params
        ).fetchall()
    else:
        rows = conn.execute(
            f'SELECT {cols} FROM {table} ORDER BY id DESC LIMIT ? OFFSET ?',
            (page_size, offset)
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_table_count(table, search=''):
    if table not in ALLOWED_TABLES:
        return 0
    conn = _get_conn()
    if search:
        searchable = ALLOWED_TABLES[table]['searchable']
        conditions = ' OR '.join(f'{col} LIKE ?' for col in searchable)
        params = [f'%{search}%'] * len(searchable)
        row = conn.execute(f'SELECT COUNT(*) as cnt FROM {table} WHERE {conditions}', params).fetchone()
    else:
        row = conn.execute(f'SELECT COUNT(*) as cnt FROM {table}').fetchone()
    conn.close()
    return row['cnt']


def insert_row(table, data):
    if table not in ALLOWED_TABLES:
        return None
    allowed_cols = ALLOWED_TABLES[table]['columns']
    cols = []
    vals = []
    for col in allowed_cols:
        if col in data and data[col] != '':
            cols.append(col)
            vals.append(data[col])
    if not cols:
        return None
    placeholders = ','.join('?' for _ in cols)
    col_str = ','.join(cols)
    conn = _get_conn()
    cur = conn.execute(f'INSERT INTO {table} ({col_str}) VALUES ({placeholders})', vals)
    conn.commit()
    new_id = cur.lastrowid
    conn.close()
    return new_id


def update_row(table, row_id, data):
    if table not in ALLOWED_TABLES:
        return False
    allowed_cols = ALLOWED_TABLES[table]['columns']
    sets = []
    vals = []
    for col in allowed_cols:
        if col in data:
            sets.append(f'{col} = ?')
            vals.append(data[col])
    if not sets:
        return False
    vals.append(row_id)
    conn = _get_conn()
    conn.execute(f'UPDATE {table} SET {",".join(sets)} WHERE id = ?', vals)
    conn.commit()
    conn.close()
    return True


def delete_rows(table, ids):
    if table not in ALLOWED_TABLES or not ids:
        return 0
    conn = _get_conn()
    placeholders = ','.join('?' for _ in ids)
    cur = conn.execute(f'DELETE FROM {table} WHERE id IN ({placeholders})', ids)
    conn.commit()
    deleted = cur.rowcount
    conn.close()
    return deleted


def clear_table(table):
    if table not in ALLOWED_TABLES:
        return 0
    conn = _get_conn()
    cur = conn.execute(f'DELETE FROM {table}')
    conn.commit()
    deleted = cur.rowcount
    conn.close()
    return deleted


# ===== 文章标签设置 =====

DEFAULT_CONTENT_TAG_SETTINGS = {
    'system_msg': "你是一个专门生成文章标签的助手，请你根据我给你的文章的内容总结并生成一系列的标签，格式可以参考[关键词1, 关键词2, 关键词3].你只需要给我生成这种形式的标签即可，其他分析内容无需输出.",
    'user_msg': "请严格按照以下要求，从提供的文章内容中提取关键词。\n\n文章内容:\n{content}\n\n要求:\n- 提取最多 {max_tags} 个最能概括文章主旨和核心概念的关键词。\n- 关键词必须来源于文章内容，准确反映文章主题。\n- 每个关键词的长度必须在 {min_length} 到 {max_length} 个字符之间。\n- 输出格式为：关键词1, 关键词2, 关键词3, ...\n- 只输出关键词列表，不要有任何其他解释或前缀。",
    'max_tags': 10,
    'min_length': 2,
    'max_length': 6,
}


def get_content_tag_settings():
    conn = _get_conn()
    row = conn.execute('SELECT * FROM content_tag_settings WHERE id = 1').fetchone()
    conn.close()
    if not row:
        return DEFAULT_CONTENT_TAG_SETTINGS
    return {
        'system_msg': row['system_msg'] or DEFAULT_CONTENT_TAG_SETTINGS['system_msg'],
        'user_msg': row['user_msg'] or DEFAULT_CONTENT_TAG_SETTINGS['user_msg'],
        'max_tags': row['max_tags'] if row['max_tags'] is not None else DEFAULT_CONTENT_TAG_SETTINGS['max_tags'],
        'min_length': row['min_length'] if row['min_length'] is not None else DEFAULT_CONTENT_TAG_SETTINGS['min_length'],
        'max_length': row['max_length'] if row['max_length'] is not None else DEFAULT_CONTENT_TAG_SETTINGS['max_length'],
    }


def update_content_tag_settings(data):
    allowed = ['system_msg', 'user_msg', 'max_tags', 'min_length', 'max_length']
    sets = []
    vals = []
    for col in allowed:
        if col in data:
            sets.append(f'{col} = ?')
            vals.append(data[col])
    if not sets:
        return False
    conn = _get_conn()
    conn.execute(f'UPDATE content_tag_settings SET {", ".join(sets)} WHERE id = 1', vals)
    conn.commit()
    conn.close()
    return True
