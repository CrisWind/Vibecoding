import os
import sqlite3
import time
import unicodedata
import re
from datetime import datetime, timedelta
from flask import Flask, request, jsonify, send_from_directory

app = Flask(__name__)

# ------------------------------------------------------------------ #
# 数据库路径（PythonAnywhere 部署时请改为绝对路径）
# 例如：DB_PATH = '/home/rflogit/mysite/logit.db'
# ------------------------------------------------------------------ #
DB_PATH = os.path.join(os.path.dirname(__file__), 'logit.db')


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()
    c = conn.cursor()

    # users 表（含联机状态字段）
    c.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            username        TEXT    NOT NULL UNIQUE,
            current_status  TEXT    NOT NULL DEFAULT 'idle',
            poop_start_time REAL             DEFAULT NULL,
            created_at      TEXT    NOT NULL DEFAULT (datetime('now', 'localtime'))
        )
    ''')

    # checkin_logs 表
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

    # friends 表（好友关系）
    c.execute('''
        CREATE TABLE IF NOT EXISTS friends (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id_1  INTEGER NOT NULL,
            user_id_2  INTEGER NOT NULL,
            status     TEXT    NOT NULL DEFAULT 'pending',
            created_at TEXT    NOT NULL DEFAULT (datetime('now', 'localtime')),
            FOREIGN KEY (user_id_1) REFERENCES users(id),
            FOREIGN KEY (user_id_2) REFERENCES users(id)
        )
    ''')

    # 安全迁移：给旧库补字段
    for sql in [
        'ALTER TABLE checkin_logs ADD COLUMN user_id INTEGER NOT NULL DEFAULT 1',
        "ALTER TABLE users ADD COLUMN current_status TEXT NOT NULL DEFAULT 'idle'",
        'ALTER TABLE users ADD COLUMN poop_start_time REAL DEFAULT NULL',
    ]:
        try:
            c.execute(sql)
        except sqlite3.OperationalError:
            pass

    conn.commit()

    # 迁移：归一化所有已存储的旧用户名（修复历史脏数据）
    rows = c.execute('SELECT id, username FROM users').fetchall()
    for row in rows:
        clean = normalize_username(row['username'])
        if clean and clean != row['username']:
            try:
                c.execute('UPDATE users SET username = ? WHERE id = ?', (clean, row['id']))
            except sqlite3.IntegrityError:
                pass  # 归一化后与其他用户重名，跳过
    conn.commit()

    # 迁移：合并归一化后重名的重复用户（修复重复注册问题）
    c.execute('SELECT username FROM users GROUP BY username HAVING COUNT(*) > 1')
    dupes = [row['username'] for row in c.fetchall()]
    for dup_name in dupes:
        c.execute('SELECT id FROM users WHERE username = ? ORDER BY id ASC', (dup_name,))
        ids = [r['id'] for r in c.fetchall()]
        if len(ids) < 2:
            continue
        keeper = ids[0]
        for old_id in ids[1:]:
            c.execute('UPDATE checkin_logs SET user_id = ? WHERE user_id = ?', (keeper, old_id))
            c.execute('UPDATE friends SET user_id_1 = ? WHERE user_id_1 = ?', (keeper, old_id))
            c.execute('UPDATE friends SET user_id_2 = ? WHERE user_id_2 = ?', (keeper, old_id))
            c.execute('DELETE FROM users WHERE id = ?', (old_id,))
    # 清理自引用和重复好友记录
    c.execute('DELETE FROM friends WHERE user_id_1 = user_id_2')
    conn.commit()
    conn.close()


def normalize_username(name):
    """统一处理用户名：去除不可见字符、全角空格、Unicode 归一化"""
    if not name:
        return ''
    # Unicode NFC 归一化（统一组合字符）
    name = unicodedata.normalize('NFC', name)
    # 去除零宽字符、不可见控制字符
    name = re.sub(r'[‌‍﻿­]', '', name)
    # 将全角空格替换为半角空格，再 strip
    name = name.replace('　', ' ').strip()
    return name


def get_current_user_id():
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
    return send_from_directory(os.path.dirname(__file__), 'index.html')


@app.route('/<path:filename>')
def serve_static(filename):
    return send_from_directory(os.path.dirname(__file__), filename)


# ================================================================== #
# 认证接口
# ================================================================== #

@app.route('/api/login', methods=['POST'])
def api_login():
    data = request.get_json(silent=True) or {}
    username = normalize_username(str(data.get('username', '')))
    if not username:
        return jsonify({'code': 400, 'message': '昵称不能为空'}), 400
    if len(username) > 50:
        return jsonify({'code': 400, 'message': '昵称最多 50 个字符'}), 400

    conn = get_db()
    c = conn.cursor()
    c.execute('SELECT id, username FROM users WHERE TRIM(username) = ? COLLATE NOCASE', (username,))
    row = c.fetchone()
    # SQL 匹配失败时，Python 端归一化逐条比对
    if not row:
        c.execute('SELECT id, username FROM users')
        for u in c.fetchall():
            if normalize_username(u['username']) == username:
                row = u
                break
    if row:
        # 始终同步存储的用户名为归一化版本
        c.execute('UPDATE users SET username = ? WHERE id = ?', (username, row['id']))
        conn.commit()
        conn.close()
        return jsonify({'code': 200, 'user_id': row['id'], 'username': username}), 200

    now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    c.execute('INSERT INTO users (username, created_at) VALUES (?, ?)', (username, now_str))
    conn.commit()
    user_id = c.lastrowid
    conn.close()
    return jsonify({'code': 200, 'user_id': user_id, 'username': username}), 200


# ================================================================== #
# 打卡记录 CRUD
# ================================================================== #

@app.route('/api/records', methods=['GET'])
def get_records():
    user_id = get_current_user_id()
    if user_id is None:
        return jsonify({'code': 401, 'message': '未登录'}), 401
    conn = get_db()
    c = conn.cursor()
    c.execute('SELECT * FROM checkin_logs WHERE user_id = ? ORDER BY record_time DESC', (user_id,))
    rows = c.fetchall()
    conn.close()
    result = []
    for row in rows:
        result.append({
            'id': row['id'], 'user_id': row['user_id'],
            'record_time': row['record_time'], 'duration_minutes': row['duration_minutes'],
            'shape': row['shape'], 'color': row['color'],
            'location': row['location'], 'created_at': row['created_at']
        })
    return jsonify({'code': 200, 'data': result}), 200


@app.route('/api/records', methods=['POST'])
def add_record():
    user_id = get_current_user_id()
    if user_id is None:
        return jsonify({'code': 401, 'message': '未登录'}), 401
    data = request.get_json(silent=True) or {}
    record_time = str(data.get('record_time', '')).strip()
    if not record_time:
        return jsonify({'code': 400, 'message': 'record_time 不能为空'}), 400
    try:
        duration_minutes = max(1, int(data.get('duration_minutes', 5)))
    except (ValueError, TypeError):
        duration_minutes = 5
    shape = str(data.get('shape', 'perfect')).strip()
    color = str(data.get('color', '')).strip()
    location = str(data.get('location', '')).strip()
    now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    conn = get_db()
    c = conn.cursor()
    c.execute(
        'INSERT INTO checkin_logs (user_id, record_time, duration_minutes, shape, color, location, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)',
        (user_id, record_time, duration_minutes, shape, color, location, now_str)
    )
    conn.commit()
    new_id = c.lastrowid
    conn.close()
    return jsonify({'code': 201, 'id': new_id}), 201


@app.route('/api/records/<int:record_id>', methods=['DELETE'])
def delete_record(record_id):
    user_id = get_current_user_id()
    if user_id is None:
        return jsonify({'code': 401, 'message': '未登录'}), 401
    conn = get_db()
    c = conn.cursor()
    c.execute('SELECT id FROM checkin_logs WHERE id = ? AND user_id = ?', (record_id, user_id))
    if not c.fetchone():
        conn.close()
        return jsonify({'code': 404, 'message': '记录不存在或无权删除'}), 404
    c.execute('DELETE FROM checkin_logs WHERE id = ? AND user_id = ?', (record_id, user_id))
    conn.commit()
    conn.close()
    return jsonify({'code': 200, 'message': 'ok'}), 200


# ================================================================== #
# 好友系统
# ================================================================== #

@app.route('/api/friends/request', methods=['POST'])
def friend_request():
    """发送好友申请（按 username 搜索）"""
    user_id = get_current_user_id()
    if user_id is None:
        return jsonify({'code': 401, 'message': '未登录'}), 401
    data = request.get_json(silent=True) or {}
    target_name = normalize_username(str(data.get('username', '')))
    if not target_name:
        return jsonify({'code': 400, 'message': '请输入对方昵称'}), 400

    conn = get_db()
    c = conn.cursor()
    # 先精确匹配（忽略大小写 + 去首尾空格）
    c.execute('SELECT id FROM users WHERE TRIM(username) = ? COLLATE NOCASE', (target_name,))
    target = c.fetchone()
    # 精确匹配失败时，用 LIKE 模糊兜底
    if not target:
        c.execute('SELECT id FROM users WHERE TRIM(username) LIKE ? COLLATE NOCASE', (target_name,))
        target = c.fetchone()
    if not target:
        # Python 端逐条归一化比对（终极兜底）
        c.execute('SELECT id, username FROM users')
        for u in c.fetchall():
            if normalize_username(u['username']) == target_name:
                target = u
                break
    if not target:
        conn.close()
        return jsonify({'code': 404, 'message': '找不到该用户 (•́ω•̀)'}), 404
    tid = target['id']
    if tid == user_id:
        conn.close()
        return jsonify({'code': 400, 'message': '不能加自己为好友哦'}), 400

    c.execute('''SELECT id, status FROM friends
        WHERE (user_id_1=? AND user_id_2=?) OR (user_id_1=? AND user_id_2=?)''',
              (user_id, tid, tid, user_id))
    existing = c.fetchone()
    if existing:
        conn.close()
        if existing['status'] == 'accepted':
            return jsonify({'code': 400, 'message': '你们已经是好友啦 ♡'}), 400
        return jsonify({'code': 400, 'message': '已有待处理的好友申请'}), 400

    now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    c.execute('INSERT INTO friends (user_id_1, user_id_2, status, created_at) VALUES (?, ?, ?, ?)',
              (user_id, tid, 'pending', now_str))
    conn.commit()
    conn.close()
    return jsonify({'code': 200, 'message': '好友申请已发送 ♡'}), 200


@app.route('/api/friends/pending', methods=['GET'])
def friend_pending():
    """获取别人发给我的待处理好友申请"""
    user_id = get_current_user_id()
    if user_id is None:
        return jsonify({'code': 401, 'message': '未登录'}), 401
    conn = get_db()
    c = conn.cursor()
    c.execute('''SELECT f.id AS request_id, f.user_id_1 AS from_id, u.username AS from_name, f.created_at
        FROM friends f JOIN users u ON u.id = f.user_id_1
        WHERE f.user_id_2 = ? AND f.status = 'pending'
        ORDER BY f.created_at DESC''', (user_id,))
    rows = c.fetchall()
    conn.close()
    result = [{'request_id': r['request_id'], 'from_user_id': r['from_id'],
               'from_username': normalize_username(r['from_name']), 'created_at': r['created_at']} for r in rows]
    return jsonify({'code': 200, 'data': result}), 200


@app.route('/api/friends/accept', methods=['POST'])
def friend_accept():
    """接受或拒绝好友申请"""
    user_id = get_current_user_id()
    if user_id is None:
        return jsonify({'code': 401, 'message': '未登录'}), 401
    data = request.get_json(silent=True) or {}
    rid = data.get('request_id')
    action = str(data.get('action', '')).strip()
    if not rid or action not in ('accept', 'reject'):
        return jsonify({'code': 400, 'message': '参数错误'}), 400

    conn = get_db()
    c = conn.cursor()
    c.execute('SELECT * FROM friends WHERE id = ? AND user_id_2 = ? AND status = ?',
              (rid, user_id, 'pending'))
    if not c.fetchone():
        conn.close()
        return jsonify({'code': 404, 'message': '申请不存在或已处理'}), 404

    if action == 'accept':
        c.execute('UPDATE friends SET status = ? WHERE id = ?', ('accepted', rid))
        conn.commit()
        conn.close()
        return jsonify({'code': 200, 'message': '好友添加成功 ♡'}), 200
    else:
        c.execute('DELETE FROM friends WHERE id = ?', (rid,))
        conn.commit()
        conn.close()
        return jsonify({'code': 200, 'message': '已拒绝'}), 200


@app.route('/api/friends', methods=['GET'])
def friend_list():
    """获取我的已通过好友列表"""
    user_id = get_current_user_id()
    if user_id is None:
        return jsonify({'code': 401, 'message': '未登录'}), 401
    conn = get_db()
    c = conn.cursor()
    c.execute('''SELECT u.id AS fid, u.username, u.current_status
        FROM friends f JOIN users u ON (
            (f.user_id_1 = ? AND u.id = f.user_id_2) OR
            (f.user_id_2 = ? AND u.id = f.user_id_1)
        ) WHERE f.status = 'accepted' ORDER BY u.username''', (user_id, user_id))
    rows = c.fetchall()
    conn.close()
    result = [{'friend_id': r['fid'], 'username': normalize_username(r['username']),
               'current_status': r['current_status']} for r in rows]
    return jsonify({'code': 200, 'data': result}), 200


# ================================================================== #
# 肠道达人排行榜
# ================================================================== #

@app.route('/api/leaderboard', methods=['GET'])
def leaderboard():
    """本周肠道达人排行榜：统计用户及好友的本周打卡数据"""
    user_id = get_current_user_id()
    if user_id is None:
        return jsonify({'code': 401, 'message': '未登录'}), 401

    conn = get_db()
    c = conn.cursor()

    # 获取好友 ID 列表
    c.execute('''SELECT CASE WHEN f.user_id_1 = ? THEN f.user_id_2 ELSE f.user_id_1 END AS fid
        FROM friends f
        WHERE (f.user_id_1 = ? OR f.user_id_2 = ?) AND f.status = 'accepted' ''',
        (user_id, user_id, user_id))
    friend_ids = [r['fid'] for r in c.fetchall()]

    # 包含自己
    all_ids = [user_id] + friend_ids

    # 计算本周一 00:00
    today = datetime.now()
    monday = today - timedelta(days=today.weekday())
    week_start = monday.strftime('%Y-%m-%d')

    result = []
    for uid in all_ids:
        c.execute('SELECT username FROM users WHERE id = ?', (uid,))
        user_row = c.fetchone()
        if not user_row:
            continue
        uname = normalize_username(user_row['username'])

        c.execute('''SELECT COUNT(*) AS cnt, COALESCE(SUM(duration_minutes), 0) AS total_dur,
            SUM(CASE WHEN shape = 'perfect' THEN 1 ELSE 0 END) AS perfect_cnt
            FROM checkin_logs WHERE user_id = ? AND record_time >= ?''',
            (uid, week_start))
        stats = c.fetchone()

        result.append({
            'user_id': uid,
            'username': uname,
            'is_me': uid == user_id,
            'weekly_count': stats['cnt'] or 0,
            'weekly_duration': stats['total_dur'] or 0,
            'weekly_perfect': stats['perfect_cnt'] or 0
        })

    conn.close()

    # 按打卡次数降序，次数相同按总时长升序（用时短更健康）
    result.sort(key=lambda x: (-x['weekly_count'], x['weekly_duration']))
    for i, item in enumerate(result):
        item['rank'] = i + 1

    return jsonify({'code': 200, 'data': result}), 200


# ================================================================== #
# 联机状态 — Co-Poop 实时共振
# ================================================================== #

@app.route('/api/status', methods=['POST'])
def update_status():
    """更新我的当前状态 (pooping / idle)"""
    user_id = get_current_user_id()
    if user_id is None:
        return jsonify({'code': 401, 'message': '未登录'}), 401
    data = request.get_json(silent=True) or {}
    status = str(data.get('status', 'idle')).strip()
    if status not in ('pooping', 'idle'):
        status = 'idle'

    conn = get_db()
    c = conn.cursor()
    if status == 'pooping':
        c.execute('UPDATE users SET current_status = ?, poop_start_time = ? WHERE id = ?',
                  (status, time.time(), user_id))
    else:
        c.execute('UPDATE users SET current_status = ?, poop_start_time = NULL WHERE id = ?',
                  (status, user_id))
    conn.commit()
    conn.close()
    return jsonify({'code': 200, 'status': status}), 200


@app.route('/api/friends/active', methods=['GET'])
def friends_active():
    """查询当前正在 pooping 的好友，返回持续秒数"""
    user_id = get_current_user_id()
    if user_id is None:
        return jsonify({'code': 401, 'message': '未登录'}), 401
    conn = get_db()
    c = conn.cursor()
    c.execute('''SELECT u.id AS fid, u.username, u.poop_start_time
        FROM friends f JOIN users u ON (
            (f.user_id_1 = ? AND u.id = f.user_id_2) OR
            (f.user_id_2 = ? AND u.id = f.user_id_1)
        ) WHERE f.status = 'accepted'
          AND u.current_status = 'pooping'
          AND u.poop_start_time IS NOT NULL''', (user_id, user_id))
    rows = c.fetchall()
    conn.close()
    now = time.time()
    result = [{'friend_id': r['fid'], 'username': normalize_username(r['username']),
               'elapsed_seconds': max(0, int(now - (r['poop_start_time'] or now)))} for r in rows]
    return jsonify({'code': 200, 'data': result}), 200


# ================================================================== #
# 启动
# ================================================================== #

# 必须在模块顶层调用，确保 WSGI 部署时也能初始化数据库
init_db()

if __name__ == '__main__':
    app.run(debug=True)
