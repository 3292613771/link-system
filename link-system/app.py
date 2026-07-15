import os
os.environ['TZ'] = 'Asia/Shanghai'
try:
    import time
    time.tzset()
except:
    pass

from flask import Flask, request, jsonify, redirect, session, send_from_directory
import imaplib
import email
import re
import html
import json
from email.header import decode_header
from email.utils import parsedate_to_datetime
import uuid
import random
from datetime import datetime, timedelta

app = Flask(__name__)
app.secret_key = 'your-secret-key-here'

# ===== 配置文件 =====
LINKS_FILE = "links.json"
ACCOUNTS_FILE = "accounts.txt"
USED_EMAILS_FILE = "used_emails.json"
ADMIN_PASSWORD = "060910"
DEFAULT_DAYS = 30
DOMAIN = "mail-auto.zeabur.app"

# ===== 老系统的账号读取逻辑 =====
def load_accounts():
    accounts = {}
    try:
        with open(ACCOUNTS_FILE, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue

                if "----" in line:
                    parts = line.split("----")
                    if len(parts) == 2:
                        email = parts[0].strip()
                        auth_code = parts[1].strip()
                        if '@' not in email:
                            email = email + "@qq.com"
                        accounts[email] = auth_code
                else:
                    parts = line.split()
                    if len(parts) >= 4:
                        emails = parts[0:3]
                        auth_code = parts[3]
                        for email in emails:
                            if '@' not in email:
                                email = email + "@qq.com"
                            accounts[email] = auth_code
                    elif len(parts) == 2:
                        email = parts[0]
                        auth_code = parts[1]
                        if '@' not in email:
                            email = email + "@qq.com"
                        accounts[email] = auth_code
    except Exception as e:
        print(f"读取账号失败: {e}")
    return accounts

ACCOUNTS = load_accounts()
print(f"已加载 {len(ACCOUNTS)} 个绑定邮箱")

def get_auth_map():
    return ACCOUNTS

# ===== 老系统的邮件解析函数 =====
def decode_str(s):
    if not s:
        return ""
    try:
        decoded_parts = []
        for part, charset in decode_header(s):
            if isinstance(part, bytes):
                if charset:
                    decoded_parts.append(part.decode(charset, errors='replace'))
                else:
                    decoded_parts.append(part.decode('utf-8', errors='replace'))
            else:
                decoded_parts.append(str(part))
        return ' '.join(decoded_parts)
    except:
        return str(s)

def clean_html_to_text(html_text):
    if not html_text:
        return ""
    text = re.sub(r'<style[^>]*>.*?</style>', '', html_text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<script[^>]*>.*?</script>', '', text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<br\s*/?>', '\n', text, flags=re.IGNORECASE)
    text = re.sub(r'</?(div|p|tr|td|li|h[1-6])[^>]*>', '\n', text, flags=re.IGNORECASE)
    text = re.sub(r'<[^>]+>', ' ', text)
    text = html.unescape(text)
    text = re.sub(r'\n\s*\n', '\n\n', text)
    text = re.sub(r'[ \t]+', ' ', text)
    return text.strip()

def get_mail_content(msg):
    import re
    import html

    content = ""

    try:
        all_parts = []
        if msg.is_multipart():
            for part in msg.walk():
                payload = part.get_payload(decode=True)
                if payload:
                    charset = part.get_content_charset() or 'utf-8'
                    try:
                        text = payload.decode(charset, errors='replace')
                    except:
                        text = payload.decode('utf-8', errors='replace')
                    if text.strip():
                        all_parts.append((part.get_content_type(), text))
        else:
            payload = msg.get_payload(decode=True)
            if payload:
                charset = msg.get_content_charset() or 'utf-8'
                try:
                    text = payload.decode(charset, errors='replace')
                except:
                    text = payload.decode('utf-8', errors='replace')
                if text.strip():
                    all_parts.append((msg.get_content_type(), text))

        for content_type, text in all_parts:
            if content_type == "text/plain":
                content = text.strip()
                break

        if not content:
            for content_type, text in all_parts:
                if content_type == "text/html":
                    content = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL)
                    content = re.sub(r'<[^>]+>', ' ', content)
                    content = html.unescape(content)
                    content = re.sub(r'\s+', ' ', content)
                    content = content.strip()
                    break

        if not content:
            return "无法解析邮件内容"

        code = None
        match = re.search(r'(\d)\s*(\d)\s*(\d)\s*(\d)\s*(\d)\s*(\d)', content)
        if match:
            code = match.group(1)+match.group(2)+match.group(3)+match.group(4)+match.group(5)+match.group(6)
        if not code:
            match = re.search(r'\b(\d{6})\b', content)
            if match:
                code = match.group(1)

        content = content[:1000]

        if code:
            return f"验证码：{code}\n\n{content}"
        return content

    except Exception as e:
        return f"解析失败"

def get_latest_mails(email_addr, limit=10):
    if email_addr not in ACCOUNTS:
        return {'error': f'邮箱 "{email_addr}" 未绑定'}

    auth_code = ACCOUNTS[email_addr]
    mail = None

    try:
        mail = imaplib.IMAP4_SSL("imap.qq.com")
        mail.login(email_addr, auth_code)

        all_mail_ids = []
        folder_info = []

        # 读取收件箱
        try:
            mail.select("INBOX")
            status, data = mail.search(None, "ALL")
            if data[0]:
                for mid in data[0].split():
                    all_mail_ids.append(mid)
                    folder_info.append("INBOX")
        except Exception as e:
            print(f"读取收件箱失败: {e}")

        # 读取垃圾箱
        spam_folders = ["垃圾箱", "广告邮件", "[Gmail]/Spam", "Spam", "Junk", "Junk Email"]
        for folder in spam_folders:
            try:
                mail.select(folder)
                status, data = mail.search(None, "ALL")
                if data[0]:
                    for mid in data[0].split():
                        all_mail_ids.append(mid)
                        folder_info.append(folder)
                break
            except:
                continue

        if not all_mail_ids:
            return []

        seen = set()
        unique_ids = []
        unique_folders = []
        for mid, folder in zip(all_mail_ids, folder_info):
            mid_str = mid.decode() if isinstance(mid, bytes) else str(mid)
            if mid_str not in seen:
                seen.add(mid_str)
                unique_ids.append(mid)
                unique_folders.append(folder)

        sorted_pairs = sorted(zip(unique_ids, unique_folders), key=lambda x: int(x[0]))
        latest_pairs = sorted_pairs[-limit:]

        mails = []

        for mail_id, folder in reversed(latest_pairs):
            try:
                mail_id_str = mail_id.decode() if isinstance(mail_id, bytes) else str(mail_id)

                mail.select(folder)
                _, msg_data = mail.fetch(mail_id, "(RFC822)")

                for part in msg_data:
                    if isinstance(part, tuple):
                        msg = email.message_from_bytes(part[1])

                        date_str = msg.get("Date", "")
                        send_time = ""
                        try:
                            from email.utils import parsedate_to_datetime
                            if date_str:
                                dt = parsedate_to_datetime(date_str)
                                send_time = dt.strftime("%Y-%m-%d %H:%M:%S")
                        except:
                            send_time = date_str[:30]
                        subject = decode_str(msg.get("Subject", "无主题"))
                        sender = decode_str(msg.get("From", "未知发件人"))
                        content = get_mail_content(msg)

                        mails.append({
                            'mail_id': mail_id_str,
                            'sender': sender,
                            'subject': subject,
                            'content': content,
                            'time': send_time
                        })
                        break
            except Exception as e:
                print(f"读取单封邮件失败 (ID:{mail_id_str}, Folder:{folder}): {e}")
                continue

        return mails

    except Exception as e:
        return {'error': f'连接失败：{str(e)}'}

    finally:
        if mail:
            try:
                mail.close()
            except:
                pass
            try:
                mail.logout()
            except:
                pass

# ===== 链接管理函数 =====
def load_links():
    try:
        with open(LINKS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return {}

def save_links(data):
    with open(LINKS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def load_used_emails():
    try:
        with open(USED_EMAILS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return {"records": {}}

def save_used_emails(data):
    with open(USED_EMAILS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def detect_email_type(email):
    if email.endswith("@foxmail.com"):
        return "foxmail"
    username = email.split("@")[0]
    if username.isdigit():
        return "数字"
    return "英文"

def assign_emails(type_name, quantity, buyer_id):
    all_emails = list(ACCOUNTS.keys())
    type_emails = [e for e in all_emails if detect_email_type(e) == type_name]

    if not type_emails:
        return None, f"类型 '{type_name}' 没有可用邮箱"

    used_data = load_used_emails()
    buyer_used = used_data.get("records", {}).get(buyer_id, [])
    available = [e for e in type_emails if e not in buyer_used]

    if len(available) < quantity:
        return None, f"类型 '{type_name}' 库存不足！需要 {quantity} 个，该买家还能领 {len(available)} 个"

    selected = random.sample(available, quantity)

    if buyer_id not in used_data["records"]:
        used_data["records"][buyer_id] = []
    used_data["records"][buyer_id].extend(selected)
    save_used_emails(used_data)

    return selected, None

# ===== 登录页面 =====
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        pwd = request.form.get('password')
        if pwd == ADMIN_PASSWORD:
            session['logged_in'] = True
            return redirect('/admin')
        else:
            return '''
            <h2>密码错误</h2>
            <p><a href="/login">重新输入</a></p>
            '''

    return '''
    <!DOCTYPE html>
    <html>
    <head><meta charset="UTF-8"><title>后台登录</title></head>
    <body style="font-family: Arial; max-width: 400px; margin: 100px auto; padding: 20px;">
        <h2>🔐 后台登录</h2>
        <form method="post">
            <input type="password" name="password" placeholder="请输入密码" style="width:100%;padding:12px;font-size:16px;margin:10px 0;border:2px solid #ddd;border-radius:8px;">
            <button type="submit" style="width:100%;padding:12px;background:#4CAF50;color:white;border:none;font-size:16px;cursor:pointer;border-radius:8px;">登录</button>
        </form>
    </body>
    </html>
    '''

# ===== 健康检查接口（供闲管家验证） =====
@app.route('/api/health', methods=['GET'])
def health_check():
    return jsonify({"status": "ok", "code": 200, "msg": "服务运行正常"})

# ===== API路由 =====
@app.route('/api/auto_create_link', methods=['GET', 'POST'])
def auto_create_link():
    # GET请求：供闲管家验证连通性
    if request.method == 'GET':
        return jsonify({"status": "ok", "code": 200, "msg": "API 可用"})

    # POST请求：生成链接
    data = request.get_json()
    if not data:
        return jsonify({"code": 400, "msg": "请求体不能为空"}), 400

    type_name = data.get('type', '英文')
    quantity = data.get('quantity', 1)
    days = data.get('days', DEFAULT_DAYS)
    buyer_id = data.get('buyer_id', str(uuid.uuid4())[:8])

    if quantity <= 0:
        return jsonify({"code": 400, "msg": "数量必须大于0"}), 400

    valid_types = ["数字", "英文", "foxmail"]
    if type_name not in valid_types:
        return jsonify({"code": 400, "msg": f"无效类型，请选择: {', '.join(valid_types)}"}), 400

    selected_emails, error = assign_emails(type_name, quantity, buyer_id)
    if error:
        return jsonify({"code": 400, "msg": error}), 400

    link_id = str(uuid.uuid4())[:8]
    links = load_links()
    now = datetime.now()

    links[link_id] = {
        'link_id': link_id,
        'buyer_id': buyer_id,
        'type': type_name,
        'emails': selected_emails,
        'quantity': quantity,
        'created_at': now.strftime("%Y-%m-%d %H:%M:%S"),
        'expire_at': (now + timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S"),
        'status': 'active',
        'query_count': 0
    }
    save_links(links)

    link_url = f"https://{DOMAIN}/query?link={link_id}"

    return jsonify({
        "code": 200,
        "msg": "success",
        "data": {
            "success": True,
            "link_id": link_id,
            "link_url": link_url,
            "type": type_name,
            "emails": selected_emails,
            "quantity": quantity,
            "expire_at": links[link_id]['expire_at']
        }
    })

@app.route('/agisoAcrSupplierApi/app/getAppId', methods=['POST'])
def get_app_id():
    return jsonify({
        "code": 200,
        "msg": "success",
        "data": {
            "appId": "1669765814257093"
        }
    })

@app.route('/goofish/open/info', methods=['POST'])
def goofish_open_info():
    return jsonify({
        "code": 200,
        "msg": "success",
        "data": {}
    })
# ===== 页面路由 =====
@app.route('/')
def index():
    return redirect('/admin')

@app.route('/admin')
def admin():
    if not session.get('logged_in'):
        return redirect('/login')

    links = load_links()
    used_data = load_used_emails()
    all_emails = list(ACCOUNTS.keys())

    total = len(all_emails)
    all_used = []
    for buyer, emails in used_data.get("records", {}).items():
        all_used.extend(emails)
    used = len(set(all_used))

    link_list = ""
    for link_id, data in links.items():
        status = '✅ 有效' if data['status'] == 'active' else '⛔ 禁用'
        link_list += f"""
        <tr>
            <td>{link_id}</td>
            <td>{data.get('buyer_id', '未知')}</td>
            <td>{data.get('type', '未知')}</td>
            <td>{len(data['emails'])}</td>
            <td>{data['created_at']}</td>
            <td>{data['expire_at']}</td>
            <td>{status}</td>
        </tr>
        """

    html_admin = f'''
    <h2>📦 链接管理后台</h2>
    <hr>
    <h3>➕ 手动生成链接</h3>
    <form action="/api/admin_create_link" method="post">
        <textarea name="emails" placeholder="每行一个邮箱" rows="5" style="width:100%;padding:10px;font-size:14px;"></textarea>
        <br>
        <select name="type">
            <option value="数字">数字邮箱</option>
            <option value="英文">英文邮箱</option>
            <option value="foxmail">foxmail邮箱</option>
        </select>
        <input type="number" name="days" value="30" style="width:80px;">
        <label>天</label>
        <br><br>
        <button type="submit" style="padding:10px 30px;background:#4CAF50;color:white;border:none;cursor:pointer;">生成链接</button>
    </form>
    <hr>
    <h3>📊 库存统计</h3>
    <ul>
        <li>总邮箱数：{total}</li>
        <li>已分配：{used}</li>
        <li>可用：{total - used}</li>
    </ul>
    <hr>
    <h3>🔗 链接列表（共 {len(links)} 个）</h3>
    <table border="1" cellpadding="8" style="width:100%;border-collapse:collapse;">
    <tr><th>链接ID</th><th>买家ID</th><th>类型</th><th>数量</th><th>创建时间</th><th>过期时间</th><th>状态</th></tr>
    {link_list if link_list else '<tr><td colspan="7">暂无链接</td></tr>'}
    </table>
    '''
    return html_admin

@app.route('/api/admin_create_link', methods=['POST'])
def admin_create_link():
    emails_text = request.form.get('emails', '')
    type_name = request.form.get('type', '英文')
    days = int(request.form.get('days', 30))

    emails = [e.strip() for e in emails_text.strip().split('\n') if e.strip()]

    if not emails:
        return "请至少输入一个邮箱", 400

    link_id = str(uuid.uuid4())[:8]
    links = load_links()
    now = datetime.now()

    links[link_id] = {
        'link_id': link_id,
        'buyer_id': 'admin',
        'type': type_name,
        'emails': emails,
        'quantity': len(emails),
        'created_at': now.strftime("%Y-%m-%d %H:%M:%S"),
        'expire_at': (now + timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S"),
        'status': 'active',
        'query_count': 0
    }
    save_links(links)

    link_url = f"https://{DOMAIN}/query?link={link_id}"

    return f'''
    生成成功！<br>
    链接：<a href="{link_url}" target="_blank">{link_url}</a><br>
    邮箱：{', '.join(emails)}<br>
    有效期：{links[link_id]['expire_at']}<br>
    <a href="/admin">返回后台</a>
    '''

@app.route('/query')
def query_page():
    link_id = request.args.get('link')
    if not link_id:
        return "缺少链接ID"

    links = load_links()
    if link_id not in links:
        return "链接不存在"

    link_data = links[link_id]
    now = datetime.now()
    expire_time = datetime.strptime(link_data['expire_at'], "%Y-%m-%d %H:%M:%S")

    if now > expire_time:
        return "⛔ 链接已过期"

    if link_data['status'] != 'active':
        return "⛔ 链接已被禁用"

    html_content = f'''
    <!DOCTYPE html>
    <html>
    <head><meta charset="UTF-8"><title>邮箱查询系统</title></head>
    <body style="font-family: Arial, sans-serif; max-width: 600px; margin: 50px auto; padding: 20px;">
        <div style="background: white; padding: 30px; border-radius: 12px; box-shadow: 0 2px 10px rgba(0,0,0,0.1);">
            <h2>📬 邮箱查询系统</h2>
            <p>输入已绑定的邮箱，查看最新邮件</p>
            <p style="color: #999; font-size: 13px;">有效期至：{link_data['expire_at']}</p>
            <form action="/api/query_mail" method="post">
                <input type="hidden" name="link_id" value="{link_id}">
                <input type="text" name="email" placeholder="请输入邮箱地址" style="width:100%;padding:12px;font-size:16px;margin:10px 0;border:2px solid #ddd;border-radius:8px;">
                <button type="submit" style="width:100%;padding:12px;background:#4CAF50;color:white;border:none;font-size:16px;cursor:pointer;border-radius:8px;">查询邮件</button>
            </form>
        </div>
    </body>
    </html>
    '''
    return html_content

@app.route('/api/query_mail', methods=['POST'])
def query_mail():
    link_id = request.form.get('link_id')
    email = request.form.get('email')

    if not email:
        return "请输入邮箱"

    email = email.strip()
    if '@' not in email:
        email = email + "@qq.com"

    links = load_links()
    if link_id not in links:
        return "链接无效"

    link_data = links[link_id]
    if email not in link_data['emails']:
        return f"该邮箱不在本链接中，可查询的邮箱：{', '.join(link_data['emails'])}"

    if email not in ACCOUNTS:
        return f"邮箱 {email} 未绑定"

    result = get_latest_mails(email, limit=10)

    if isinstance(result, dict) and 'error' in result:
        return f"查询失败：{result['error']}"

    if not result:
        return "<h3>📭 暂无邮件</h3>"

    html_result = f"<h3>📧 {email} 的最新邮件</h3>"
    for mail in result:
        html_result += f"""
        <div style="border-bottom:1px solid #ddd;padding:10px;">
            <b>{mail['sender']}</b><br>
            <span style="color:#666;">{mail['subject']}</span><br>
            <span style="font-size:14px;">{mail['content'][:1000]}</span>
            <span style="color:#999;font-size:12px;">{mail.get('time', '')}</span>
        </div>
        """
    return html_result

@app.route('/api/groups', methods=['GET'])
def get_groups():
    all_emails = list(ACCOUNTS.keys())
    used_data = load_used_emails()
    all_used = []
    for buyer, emails in used_data.get("records", {}).items():
        all_used.extend(emails)

    types = ["数字", "英文", "foxmail"]
    result = []
    for t in types:
        type_emails = [e for e in all_emails if detect_email_type(e) == t]
        available = len([e for e in type_emails if e not in all_used])
        result.append({
            "name": t,
            "total": len(type_emails),
            "available": available
        })
    return jsonify(result)

@app.route('/api/admin_logs')
def api_admin_logs():
    return jsonify({"logs": []})

@app.route('/api/test_login', methods=['POST'])
def test_login():
    data = request.get_json()
    email = data.get('email')
    auth = data.get('auth')

    try:
        mail = imaplib.IMAP4_SSL("imap.qq.com")
        mail.login(email, auth)
        mail.select("INBOX")
        mail.close()
        mail.logout()
        return "登录成功"
    except Exception as e:
        return f"登录失败：{str(e)}"

if __name__ == '__main__':
    print("=" * 60)
    print("邮箱查询系统启动")
    print("=" * 60)
    print(f"已绑定 {len(ACCOUNTS)} 个邮箱")
    print("后台密码: 060910")
    print("健康检查: /api/health")
    print("访问 http://127.0.0.1:8080")
    print("=" * 60)
    app.run(host='0.0.0.0', port=8080)
