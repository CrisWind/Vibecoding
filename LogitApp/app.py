import os
import sqlite3
import time
import unicodedata
import re
from datetime import datetime, timedelta
from flask import Flask, request, jsonify, send_from_directory
from zhipuai import ZhipuAI

app = Flask(__name__)

# ------------------------------------------------------------------ #
# 智谱 AI 配置（请替换为你自己的 API Key）
# ------------------------------------------------------------------ #
ZHIPU_API_KEY = 'YOUR_ZHIPU_API_KEY_HERE'

# ------------------------------------------------------------------ #
# 数据库路径（PythonAnywhere 部署时请改为绝对路径）
# 例如：DB_PATH = '/home/rflogit/mysite/logit.db'
# ------------------------------------------------------------------ #
DB_PATH = os.path.join(os.path.dirname(__file__), 'logit.db')


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


# ================================================================== #
# 勋章目录 — 4 大分类 15 枚勋章
# ================================================================== #

BADGE_CATALOG = {
    # ---- 时间与毅力类 (time_streak) ----
    'early_bird': {
        'name': '早鸟先飞', 'icon': '🐦',
        'desc': '在早上 4:00-7:00 之间完成打卡',
        'hint': '如何获得：在早上 4:00-7:00 之间打卡',
        'category': 'time_streak',
    },
    'night_owl': {
        'name': '暗夜精灵', 'icon': '🦉',
        'desc': '在凌晨 0:00-3:00 之间完成打卡',
        'hint': '如何获得：在凌晨 0:00-3:00 之间打卡',
        'category': 'time_streak',
    },
    'warrior': {
        'name': '便秘战士', 'icon': '⚔️',
        'desc': '单次打卡耗时超过 30 分钟',
        'hint': '如何获得：单次耗时超过 30 分钟',
        'category': 'time_streak',
    },
    'flash': {
        'name': '闪电突击', 'icon': '⚡',
        'desc': '单次打卡耗时 ≤ 3 分钟',
        'hint': '如何获得：单次耗时少于等于 3 分钟',
        'category': 'time_streak',
    },
    'streak_3': {
        'name': '初级自律', 'icon': '🔥',
        'desc': '连续 3 天打卡不间断',
        'hint': '如何获得：连续 3 天不间断打卡',
        'category': 'time_streak',
    },
    'streak_7': {
        'name': '肠胃管理大师', 'icon': '👑',
        'desc': '连续 7 天不间断打卡',
        'hint': '如何获得：连续 7 天不间断打卡',
        'category': 'time_streak',
    },
    # ---- 形态与特征类 (shape_property) ----
    'golden_perfect': {
        'name': '天选之子', 'icon': '✨',
        'desc': '形态为"完美"且颜色为"金黄"',
        'hint': '如何获得：打卡形态为"完美"且颜色为"金黄"',
        'category': 'shape_property',
    },
    'hard_rock': {
        'name': '坚如磐石', 'icon': '🪨',
        'desc': '连续 2 次形态都是"干燥"',
        'hint': '如何获得：连续 2 次形态都是"干燥"',
        'category': 'shape_property',
    },
    'liquid_warning': {
        'name': '喷射战士', 'icon': '💦',
        'desc': '形态为"稀软/腹泻"',
        'hint': '如何获得：打卡形态为"稀软"',
        'category': 'shape_property',
    },
    # ---- 地理探索类 (location) ----
    'homebody': {
        'name': '画地为牢', 'icon': '🏠',
        'desc': '在同一个位置连续打卡 5 次',
        'hint': '如何获得：在同一个位置连续打卡 5 次',
        'category': 'location',
    },
    'explorer': {
        'name': '四海为家', 'icon': '🗺️',
        'desc': '在 3 个不同地址留下记录',
        'hint': '如何获得：在 3 个完全不同的地址打卡',
        'category': 'location',
    },
    'wild_pooper': {
        'name': '野外求生', 'icon': '🏕️',
        'desc': '在公厕/卫生间/洗手间打卡',
        'hint': '如何获得：位置名称包含"公厕""卫生间"或"洗手间"',
        'category': 'location',
    },
    # ---- 社交与联机类 (social) ----
    'first_blood_social': {
        'name': '破冰行动', 'icon': '🤝',
        'desc': '成功添加第一个好友',
        'hint': '如何获得：成功添加第一个好友',
        'category': 'social',
    },
    'sync_master': {
        'name': '心有灵犀', 'icon': '💞',
        'desc': '与好友同时处于打卡状态时保存记录',
        'hint': '如何获得：在联机大厅中与好友同时打卡时保存',
        'category': 'social',
    },
    'social_butterfly': {
        'name': '交际花', 'icon': '🦋',
        'desc': '好友列表达到 5 人',
        'hint': '如何获得：好友列表达到 5 人',
        'category': 'social',
    },
}

CATEGORY_META = {
    'time_streak':    {'name': '⏰ 时间与毅力', 'order': 1},
    'shape_property': {'name': '✨ 形态与特征', 'order': 2},
    'location':       {'name': '🗺️ 地理探索',   'order': 3},
    'social':         {'name': '👫 社交与联机', 'order': 4},
}


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

    # v9: user_badges 表（勋章解锁记录）
    c.execute('''
        CREATE TABLE IF NOT EXISTS user_badges (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER NOT NULL,
            badge_key   TEXT    NOT NULL,
            unlocked_at TEXT    NOT NULL DEFAULT (datetime('now', 'localtime')),
            FOREIGN KEY (user_id) REFERENCES users(id),
            UNIQUE(user_id, badge_key)
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
                pass
    conn.commit()

    # 迁移：合并归一化后重名的重复用户
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
    c.execute('DELETE FROM friends WHERE user_id_1 = user_id_2')
    conn.commit()
    conn.close()


def normalize_username(name):
    """统一处理用户名：去除不可见字符、全角空格、Unicode 归一化"""
    if not name:
        return ''
    name = unicodedata.normalize('NFC', name)
    name = re.sub(r'[\u200c\u200d\ufeff\u00ad]', '', name)
    name = name.replace('\u3000', ' ').strip()
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
# 勋章引擎 — check_and_award_badges
# ================================================================== #

def _user_has_badge(c, user_id, badge_key):
    """检查用户是否已拥有某勋章"""
    c.execute('SELECT 1 FROM user_badges WHERE user_id = ? AND badge_key = ?', (user_id, badge_key))
    return c.fetchone() is not None


def _award_badge(c, user_id, badge_key, now_str):
    """写入勋章记录，返回 True 表示新解锁"""
    try:
        c.execute('INSERT INTO user_badges (user_id, badge_key, unlocked_at) VALUES (?, ?, ?)',
                  (user_id, badge_key, now_str))
        return True
    except sqlite3.IntegrityError:
        return False


def _get_consecutive_streak(c, user_id, today_str):
    """计算截止到 today_str（含）的连续打卡天数"""
    c.execute('''
        SELECT DISTINCT DATE(record_time) AS d
        FROM checkin_logs
        WHERE user_id = ?
        ORDER BY d DESC
    ''', (user_id,))
    dates = [row['d'] for row in c.fetchall()]
    if not dates:
        return 0
    streak = 0
    check_date = datetime.strptime(today_str[:10], '%Y-%m-%d').date()
    for d_str in dates:
        d = datetime.strptime(d_str, '%Y-%m-%d').date()
        if d == check_date:
            streak += 1
            check_date -= timedelta(days=1)
        elif d < check_date:
            break
    return streak


def check_and_award_badges(user_id, current_record):
    """
    统一勋章判断引擎。在打卡记录保存后调用。
    current_record: dict with keys record_time, duration_minutes, shape, color, location
    返回: list of newly unlocked badge dicts
    """
    conn = get_db()
    c = conn.cursor()
    now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    newly_unlocked = []

    def try_award(badge_key):
        if not _user_has_badge(c, user_id, badge_key):
            if _award_badge(c, user_id, badge_key, now_str):
                info = BADGE_CATALOG[badge_key].copy()
                info['key'] = badge_key
                info['unlocked_at'] = now_str
                newly_unlocked.append(info)

    # 解析打卡时间的小时
    record_time_str = str(current_record.get('record_time', ''))
    record_hour = None
    try:
        if 'T' in record_time_str:
            record_hour = int(record_time_str.split('T')[1].split(':')[0])
        elif ' ' in record_time_str:
            record_hour = int(record_time_str.split(' ')[1].split(':')[0])
    except (IndexError, ValueError):
        pass

    duration = current_record.get('duration_minutes', 5)
    shape = str(current_record.get('shape', '')).strip()
    color = str(current_record.get('color', '')).strip().upper()
    location = str(current_record.get('location', '')).strip()

    # ---- 时间与毅力类 ----
    # 1. early_bird: 4:00-6:59
    if record_hour is not None and 4 <= record_hour <= 6:
        try_award('early_bird')

    # 2. night_owl: 0:00-2:59
    if record_hour is not None and 0 <= record_hour <= 2:
        try_award('night_owl')

    # 3. warrior: 耗时 > 30 分钟
    if duration > 30:
        try_award('warrior')

    # 4. flash: 耗时 <= 3 分钟
    if duration <= 3:
        try_award('flash')

    # 5 & 6. streak_3 / streak_7: 连续打卡天数
    today_str = record_time_str[:10] if len(record_time_str) >= 10 else datetime.now().strftime('%Y-%m-%d')
    streak = _get_consecutive_streak(c, user_id, today_str)
    if streak >= 3:
        try_award('streak_3')
    if streak >= 7:
        try_award('streak_7')

    # ---- 形态与特征类 ----
    # 7. golden_perfect: perfect + 金黄 #C4A35A
    if shape == 'perfect' and color in ('#C4A35A', 'RGB(196, 163, 90)', 'RGB(196,163,90)'):
        try_award('golden_perfect')

    # 8. hard_rock: 连续 2 次 dry
    if shape == 'dry':
        c.execute('''
            SELECT shape FROM checkin_logs
            WHERE user_id = ? ORDER BY id DESC LIMIT 2
        ''', (user_id,))
        last_shapes = [r['shape'] for r in c.fetchall()]
        if len(last_shapes) >= 2 and all(s == 'dry' for s in last_shapes):
            try_award('hard_rock')

    # 9. liquid_warning: 稀软
    if shape == 'soft':
        try_award('liquid_warning')

    # ---- 地理探索类 ----
    # 10. homebody: 同一位置连续 5 次
    if location:
        c.execute('''
            SELECT location FROM checkin_logs
            WHERE user_id = ? ORDER BY id DESC LIMIT 5
        ''', (user_id,))
        last_locs = [r['location'] for r in c.fetchall()]
        if len(last_locs) >= 5 and all(l == last_locs[0] and l for l in last_locs):
            try_award('homebody')

    # 11. explorer: 3 个不同地址
    c.execute('''
        SELECT COUNT(DISTINCT location) AS cnt
        FROM checkin_logs
        WHERE user_id = ? AND location != '' AND location != '未知位置'
    ''', (user_id,))
    distinct_locs = c.fetchone()['cnt']
    if distinct_locs >= 3:
        try_award('explorer')

    # 12. wild_pooper: 位置含公厕/卫生间/洗手间
    if location:
        for keyword in ('公厕', '卫生间', '洗手间'):
            if keyword in location:
                try_award('wild_pooper')
                break

    # ---- 社交与联机类 ----
    # 13. first_blood_social: 至少 1 个好友（在打卡时也顺便检查）
    c.execute('''
        SELECT COUNT(*) AS cnt FROM friends
        WHERE (user_id_1 = ? OR user_id_2 = ?) AND status = 'accepted'
    ''', (user_id, user_id))
    friend_count = c.fetchone()['cnt']
    if friend_count >= 1:
        try_award('first_blood_social')

    # 14. sync_master: 有好友正在 pooping
    c.execute('''
        SELECT COUNT(*) AS cnt
        FROM friends f JOIN users u ON (
            (f.user_id_1 = ? AND u.id = f.user_id_2) OR
            (f.user_id_2 = ? AND u.id = f.user_id_1)
        )
        WHERE f.status = 'accepted'
          AND u.current_status = 'pooping'
          AND u.poop_start_time IS NOT NULL
    ''', (user_id, user_id))
    pooping_friends = c.fetchone()['cnt']
    if pooping_friends > 0:
        try_award('sync_master')

    # 15. social_butterfly: 好友 >= 5
    if friend_count >= 5:
        try_award('social_butterfly')

    conn.commit()
    conn.close()
    return newly_unlocked


def check_social_badges_on_friend_accept(user_id):
    """接受好友后检查社交类勋章"""
    conn = get_db()
    c = conn.cursor()
    now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    newly_unlocked = []

    def try_award(badge_key):
        if not _user_has_badge(c, user_id, badge_key):
            if _award_badge(c, user_id, badge_key, now_str):
                info = BADGE_CATALOG[badge_key].copy()
                info['key'] = badge_key
                info['unlocked_at'] = now_str
                newly_unlocked.append(info)

    c.execute('''
        SELECT COUNT(*) AS cnt FROM friends
        WHERE (user_id_1 = ? OR user_id_2 = ?) AND status = 'accepted'
    ''', (user_id, user_id))
    friend_count = c.fetchone()['cnt']
    if friend_count >= 1:
        try_award('first_blood_social')
    if friend_count >= 5:
        try_award('social_butterfly')

    conn.commit()
    conn.close()
    return newly_unlocked


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
    if not row:
        c.execute('SELECT id, username FROM users')
        for u in c.fetchall():
            if normalize_username(u['username']) == username:
                row = u
                break
    if row:
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
# 打卡记录 CRUD（v9: 集成勋章触发）
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

    # v9: 触发勋章检查
    current_record = {
        'record_time': record_time,
        'duration_minutes': duration_minutes,
        'shape': shape,
        'color': color,
        'location': location,
    }
    unlocked = check_and_award_badges(user_id, current_record)

    return jsonify({'code': 201, 'id': new_id, 'unlocked_badges': unlocked}), 201


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
# v9: 勋章图鉴接口
# ================================================================== #

@app.route('/api/badges', methods=['GET'])
def get_badges():
    user_id = get_current_user_id()
    if user_id is None:
        return jsonify({'code': 401, 'message': '未登录'}), 401

    conn = get_db()
    c = conn.cursor()
    c.execute('SELECT badge_key, unlocked_at FROM user_badges WHERE user_id = ?', (user_id,))
    unlocked_map = {}
    for row in c.fetchall():
        unlocked_map[row['badge_key']] = row['unlocked_at']
    conn.close()

    result = []
    for key, info in BADGE_CATALOG.items():
        is_unlocked = key in unlocked_map
        result.append({
            'key': key,
            'name': info['name'],
            'icon': info['icon'],
            'desc': info['desc'],
            'hint': info['hint'],
            'category': info['category'],
            'category_name': CATEGORY_META.get(info['category'], {}).get('name', ''),
            'category_order': CATEGORY_META.get(info['category'], {}).get('order', 99),
            'is_unlocked': is_unlocked,
            'unlocked_at': unlocked_map.get(key, None),
        })

    result.sort(key=lambda x: (x['category_order'], x['key']))
    return jsonify({'code': 200, 'data': result, 'categories': CATEGORY_META}), 200


# ================================================================== #
# 好友系统（v9: 集成社交勋章）
# ================================================================== #

@app.route('/api/friends/request', methods=['POST'])
def friend_request():
    user_id = get_current_user_id()
    if user_id is None:
        return jsonify({'code': 401, 'message': '未登录'}), 401
    data = request.get_json(silent=True) or {}
    target_name = normalize_username(str(data.get('username', '')))
    if not target_name:
        return jsonify({'code': 400, 'message': '请输入对方昵称'}), 400

    conn = get_db()
    c = conn.cursor()
    c.execute('SELECT id FROM users WHERE TRIM(username) = ? COLLATE NOCASE', (target_name,))
    target = c.fetchone()
    if not target:
        c.execute('SELECT id FROM users WHERE TRIM(username) LIKE ? COLLATE NOCASE', (target_name,))
        target = c.fetchone()
    if not target:
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
    friend_row = c.fetchone()
    if not friend_row:
        conn.close()
        return jsonify({'code': 404, 'message': '申请不存在或已处理'}), 404

    if action == 'accept':
        c.execute('UPDATE friends SET status = ? WHERE id = ?', ('accepted', rid))
        conn.commit()
        conn.close()
        # v9: 双方都检查社交勋章
        check_social_badges_on_friend_accept(user_id)
        check_social_badges_on_friend_accept(friend_row['user_id_1'])
        return jsonify({'code': 200, 'message': '好友添加成功 ♡'}), 200
    else:
        c.execute('DELETE FROM friends WHERE id = ?', (rid,))
        conn.commit()
        conn.close()
        return jsonify({'code': 200, 'message': '已拒绝'}), 200


@app.route('/api/friends', methods=['GET'])
def friend_list():
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
    user_id = get_current_user_id()
    if user_id is None:
        return jsonify({'code': 401, 'message': '未登录'}), 401

    conn = get_db()
    c = conn.cursor()

    c.execute('''SELECT CASE WHEN f.user_id_1 = ? THEN f.user_id_2 ELSE f.user_id_1 END AS fid
        FROM friends f
        WHERE (f.user_id_1 = ? OR f.user_id_2 = ?) AND f.status = 'accepted' ''',
        (user_id, user_id, user_id))
    friend_ids = [r['fid'] for r in c.fetchall()]

    all_ids = [user_id] + friend_ids

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

    result.sort(key=lambda x: (-x['weekly_count'], x['weekly_duration']))
    for i, item in enumerate(result):
        item['rank'] = i + 1

    return jsonify({'code': 200, 'data': result}), 200


# ================================================================== #
# 联机状态 — Co-Poop 实时共振
# ================================================================== #

@app.route('/api/status', methods=['POST'])
def update_status():
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
# v10: 智谱 AI 肠道健康周报
# ================================================================== #

def aggregate_weekly_data(user_id):
    """聚合用户过去 7 天的打卡数据，供 AI 周报使用"""
    conn = get_db()
    c = conn.cursor()
    week_ago = (datetime.now() - timedelta(days=7)).strftime('%Y-%m-%d')
    c.execute('''
        SELECT record_time, duration_minutes, shape, color, location
        FROM checkin_logs
        WHERE user_id = ? AND record_time >= ?
        ORDER BY record_time DESC
    ''', (user_id, week_ago))
    rows = c.fetchall()
    conn.close()

    if not rows:
        return None

    total_count = len(rows)
    total_duration = sum(r['duration_minutes'] for r in rows)
    avg_duration = round(total_duration / total_count, 1)

    shape_counts = {}
    color_counts = {}
    has_extreme_time = False

    for r in rows:
        shape = r['shape'] or 'unknown'
        color = r['color'] or 'unknown'
        shape_counts[shape] = shape_counts.get(shape, 0) + 1
        color_counts[color] = color_counts.get(color, 0) + 1
        # 检查极端时间（0:00-5:59）
        rt = str(r['record_time'] or '')
        try:
            if 'T' in rt:
                hour = int(rt.split('T')[1].split(':')[0])
            elif ' ' in rt:
                hour = int(rt.split(' ')[1].split(':')[0])
            else:
                hour = -1
            if 0 <= hour <= 5:
                has_extreme_time = True
        except (IndexError, ValueError):
            pass

    top_shape = max(shape_counts, key=shape_counts.get)
    top_color = max(color_counts, key=color_counts.get)

    SHAPE_NAMES = {'perfect': '完美', 'soft': '稀软', 'dry': '干燥'}
    COLOR_NAMES = {
        '#A0522D': '深褐', '#C4A35A': '金黄', '#6B8E23': '绿色',
        '#D2691E': '橙褐', '#3B3B3B': '深黑', '#CD5C5C': '偏红'
    }

    return {
        'total_count': total_count,
        'avg_duration': avg_duration,
        'top_shape': SHAPE_NAMES.get(top_shape, top_shape),
        'top_shape_count': shape_counts[top_shape],
        'top_color': COLOR_NAMES.get(top_color.upper(), top_color) if top_color else '未知',
        'top_color_count': color_counts[top_color],
        'has_extreme_time': has_extreme_time,
        'shape_distribution': {SHAPE_NAMES.get(k, k): v for k, v in shape_counts.items()},
    }


@app.route('/api/report/weekly', methods=['GET'])
def weekly_report():
    """调用智谱 AI 生成肠道健康周报"""
    user_id = get_current_user_id()
    if user_id is None:
        return jsonify({'code': 401, 'message': '未登录'}), 401

    data = aggregate_weekly_data(user_id)
    if data is None:
        return jsonify({'code': 200, 'report': None,
                        'message': '过去 7 天没有打卡记录，数据不足，无法生成周报 (•́ω•̀)'}), 200

    prompt = f"""你是一位幽默、专业、带点极客风的肠胃科医生，名叫“Dr. Poop”。
请根据以下用户过去 7 天的排便打卡数据，生成一份 300 字左右的【肠道健康周报】。

📊 数据摘要：
- 本周总打卡次数：{data['total_count']} 次
- 平均单次耗时：{data['avg_duration']} 分钟
- 最常见形态：{data['top_shape']}（出现 {data['top_shape_count']} 次）
- 最常见颜色：{data['top_color']}（出现 {data['top_color_count']} 次）
- 形态分布：{data['shape_distribution']}
- 是否有凌晨打卡：{'是（注意作息！）' if data['has_extreme_time'] else '否'}

请按以下格式输出：
1. 📋 数据总结（用生动幽默的语言概括数据）
2. 🏆 健康评级（S/A/B/C 四个等级，S 最佳）
3. 💡 饮食作息建议（给出 2-3 条实用建议）

要求：语言风格幽默但不低俗，像一个朋友在关心你的健康。可以适当使用 emoji。"""

    try:
        client = ZhipuAI(api_key=ZHIPU_API_KEY)
        response = client.chat.completions.create(
            model='glm-4',
            messages=[{'role': 'user', 'content': prompt}],
            temperature=0.8,
            max_tokens=1024,
        )
        report_text = response.choices[0].message.content
        return jsonify({'code': 200, 'report': report_text}), 200
    except Exception as e:
        error_msg = str(e)
        if 'api_key' in error_msg.lower() or 'auth' in error_msg.lower():
            return jsonify({'code': 500, 'message': '智谱 AI API Key 无效，请检查配置 🔑'}), 500
        elif 'timeout' in error_msg.lower():
            return jsonify({'code': 500, 'message': '智谱 AI 响应超时，请稍后再试 ⏱️'}), 500
        else:
            return jsonify({'code': 500, 'message': f'AI 分析失败：{error_msg} 😢'}), 500


# ================================================================== #
# 启动
# ================================================================== #

init_db()

if __name__ == '__main__':
    app.run(debug=True)
