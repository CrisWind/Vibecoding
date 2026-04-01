import os
import sqlite3
from datetime import datetime
from flask import Flask, request, jsonify, send_from_directory

app = Flask(__name__)

# ------------------------------------------------------------------ #
# 数据库路径（PythonAnywhere 部署时请改为绝对路径）
# 例如：DB_PATH = '/home/rflogit/mysite/logit.db'
# ------------------------------------------------------------------ #
DB_PATH = os.path.join(os.path.dirname(__file__), 'logit.db')


def get_db():
    """获取当前请求的数据库连接（row_factory 让结果可像字典一样访问）"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """
    初始化数据库：
      - users 表：存储用户（id, username, created_at）
      - checkin_logs 表：存储打卡记录，含 user_id 外键
    已存在时不重复创建，保证幂等性。
    """
    conn = get_db()
    c = conn.cursor()

    # users 表
    c.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            username   TEXT    NOT NULL UNIQUE,
            created_at TEXT    NOT NULL DEFAULT (datetime('now', 'localtime'))
        )
    ''')

    # checkin_logs 表（含 user_id 外键）
    c.execute('''
        CREATE TABLE IF NOT EXISTS checkin_logs (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id          INTEGER NOT NULL DEFAULT 1,
            record_time      TEXT    NOT NULL,
            duration_minutes INTEGER NOT NULL DEFAULT 5,
            shape            TEXT    NOT NULL DEFAULT 'perfect',
            color            TEXT             DEFAULT '',
            location         TEXT             DEFAULT '',
            created_at       TEXT    NOT NULL DEFAULT (datetime('now', 'localtime')),
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    ''')

    # 若旧库没有 user_id 列，则安全地补充（迁移兼容）
    try:
        c.execute('ALTER TABLE checkin_logs ADD COLUMN user_id INTEGER NOT NULL DEFAULT 1')
    except sqlite3.OperationalError:
        pass  # 列已存在，忽略

    conn.commit()
    conn.close()


def get_current_user_id():
    """
    从请求 Header 的 X-User-Id 中读取当前用户 ID。
    如果 Header 不存在或值非法，返回 None。
    """
    uid = request.headers.get('X-User-Id', None)
    if uid is None:
        return None
    try:
        uid_int = int(uid)
        return uid_int if uid_int > 0 else None
    except (ValueError, TypeError):
        return None


# ================================================================== #
# 静态文件服务
# ================================================================== #

@app.route('/')
def serve_index():
    """根路径 → 返回 index.html"""
    return send_from_directory(os.path.dirname(__file__), 'index.html')


@app.route('/<path:filename>')
def serve_static(filename):
    """其他静态资源（图片、manifest.json 等）"""
    return send_from_directory(os.path.dirname(__file__), filename)


# ================================================================== #
# 认证接口
# ================================================================== #

@app.route('/api/login', methods=['POST'])
def api_login():
    """
    极简免密登录 / 自动注册。
    接收 JSON: { "username": "xxx" }
    - 用户名已存在 → 直接返回其 user_id
    - 用户名不存在 → 自动创建并返回新 user_id
    返回: { "code": 200, "user_id": <int>, "username": <str> }
    """
    data = request.get_json(silent=True) or {}
    username = str(data.get('username', '')).strip()

    if not username:
        return jsonify({'code': 400, 'message': '昵称不能为空'}), 400
    if len(username) > 50:
        return jsonify({'code': 400, 'message': '昵称最多 50 个字符'}), 400

    conn = get_db()
    c = conn.cursor()

    # 查询是否已存在
    c.execute('SELECT id, username FROM users WHERE username = ?', (username,))
    row = c.fetchone()

    if row:
        user_id = row['id']
        conn.close()
        return jsonify({'code': 200, 'user_id': user_id, 'username': username}), 200

    # 自动注册
    now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    c.execute('INSERT INTO users (username, created_at) VALUES (?, ?)', (username, now_str))
    conn.commit()
    user_id = c.lastrowid
    conn.close()

    return jsonify({'code': 200, 'user_id': user_id, 'username': username}), 200


# ================================================================== #
# 打卡记录接口（全部基于 user_id 隔离）
# ================================================================== #

@app.route('/api/records', methods=['GET'])
def get_records():
    """
    获取当前用户的所有打卡记录，按时间倒序。
    需要 Header: X-User-Id: <user_id>
    返回: { "code": 200, "data": [ { ...record... }, ... ] }
    """
    user_id = get_current_user_id()
    if user_id is None:
        return jsonify({'code': 401, 'message': '未登录，请先登录'}), 401

    conn = get_db()
    c = conn.cursor()
    c.execute(
        'SELECT * FROM checkin_logs WHERE user_id = ? ORDER BY record_time DESC',
        (user_id,)
    )
    rows = c.fetchall()
    conn.close()

    result = []
    for row in rows:
        result.append({
            'id':               row['id'],
            'user_id':          row['user_id'],
            'record_time':      row['record_time'],
            'duration_minutes': row['duration_minutes'],
            'shape':            row['shape'],
            'color':            row['color'],
            'location':         row['location'],
            'created_at':       row['created_at']
        })

    return jsonify({'code': 200, 'data': result}), 200


@app.route('/api/records', methods=['POST'])
def add_record():
    """
    新增一条打卡记录。
    需要 Header: X-User-Id: <user_id>
    接收 JSON: { record_time, duration_minutes, shape, color?, location? }
    返回: { "code": 201, "id": <新记录id> }
    """
    user_id = get_current_user_id()
    if user_id is None:
        return jsonify({'code': 401, 'message': '未登录，请先登录'}), 401

    data = request.get_json(silent=True) or {}

    record_time = str(data.get('record_time', '')).strip()
    if not record_time:
        return jsonify({'code': 400, 'message': 'record_time 不能为空'}), 400

    try:
        duration_minutes = int(data.get('duration_minutes', 5))
        if duration_minutes < 1:
            duration_minutes = 1
    except (ValueError, TypeError):
        duration_minutes = 5

    shape    = str(data.get('shape', 'perfect')).strip()
    color    = str(data.get('color', '')).strip()
    location = str(data.get('location', '')).strip()
    now_str  = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    conn = get_db()
    c = conn.cursor()
    c.execute(
        '''
        INSERT INTO checkin_logs
            (user_id, record_time, duration_minutes, shape, color, location, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ''',
        (user_id, record_time, duration_minutes, shape, color, location, now_str)
    )
    conn.commit()
    new_id = c.lastrowid
    conn.close()

    return jsonify({'code': 201, 'id': new_id}), 201


@app.route('/api/records/<int:record_id>', methods=['DELETE'])
def delete_record(record_id):
    """
    删除一条打卡记录（只能删除自己的记录）。
    需要 Header: X-User-Id: <user_id>
    返回: { "code": 200, "message": "ok" }
    """
    user_id = get_current_user_id()
    if user_id is None:
        return jsonify({'code': 401, 'message': '未登录，请先登录'}), 401

    conn = get_db()
    c = conn.cursor()

    # 先验证这条记录属于当前用户
    c.execute('SELECT id FROM checkin_logs WHERE id = ? AND user_id = ?', (record_id, user_id))
    row = c.fetchone()

    if not row:
        conn.close()
        return jsonify({'code': 404, 'message': '记录不存在或无权删除'}), 404

    c.execute('DELETE FROM checkin_logs WHERE id = ? AND user_id = ?', (record_id, user_id))
    conn.commit()
    conn.close()

    return jsonify({'code': 200, 'message': 'ok'}), 200


# ================================================================== #
# 启动
# ================================================================== #

if __name__ == '__main__':
    init_db()
    app.run(debug=True)