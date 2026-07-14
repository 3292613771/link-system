from flask import Flask, request, jsonify
import json
import uuid
from datetime import datetime, timedelta
import os
import random
import imaplib
import email
from email.header import decode_header

app = Flask(__name__)

LINKS_FILE = "links.json"
ACCOUNTS_FILE = "accounts.txt"
USED_EMAILS_FILE = "used_emails.json"
ADMIN_PASSWORD = "123456"
DEFAULT_DAYS = 30


def get_all_emails():
    accounts = []
    try:
        with open(ACCOUNTS_FILE, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                parts = line.split()
                if len(parts) >= 4:
                    emails = parts[0:3]
                    auth_code = parts[3]
                    for email in emails:
                        if '@' not in email:
                            email = email + "@qq.com"
                        accounts.append({"email": email, "auth": auth_code})
    except:
        pass
    return accounts


def detect_email_type(email):
    if email.endswith("@foxmail.com"):
        return "foxmail"
    username = email.split("@")[0]
    if username.isdigit():
        return "数字"
    return "英文"


def get_emails_by_type(type_name):
    all_emails = get_all_emails()
    result = []
    for item in all_emails:
        if detect_email_type(item["email"]) == type_name:
            result.append(item)
    return result


def load_used_emails():
    try:
        with open(USED_EMAILS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return {"used": [], "records": {}}


def save_used_emails(data):
    with open(USED_EMAILS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_links():
    try:
        with open(LINKS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return {}


def save_links(data):
    with open(LINKS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def assign_emails(type_name, quantity, buyer_id):
    type_emails = get_emails_by_type(type_name)
    if not type_emails:
        return None, f"类型 '{type_name}' 没有可用邮箱"
    all_emails = [item["email"] for item in type_emails]
    used_data = load_used_emails()
    used_emails = used_data.get("used", [])
    available = [e for e in all_emails if e not in used_emails]
    if len(available) < quantity:
        return None, f"类型 '{type_name}' 库存不足！需要 {quantity} 个，只剩 {len(available)} 个"
    selected = random.sample(available, quantity)
    used_data["used"].extend(selected)
    if "records" not in used_data:
        used_data["records"] = {}
    for email in selected:
        used_data["records"][email] = {
            "assigned_to": buyer_id,
            "type": type_name,
            "assigned_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
    save_used_emails(used_data)
    email_auth_map = {item["email"]: item["auth"] for item in type_emails}
    selected_auths = {email: email_auth_map.get(email, "") for email in selected}
    return selected, selected_auths, None


@app.route('/api/auto_create_link', methods=['POST'])
def auto_create_link():
    data = request.get_json()
    type_name = data.get('type', '英文')
    quantity = data.get('quantity', 1)
    days = data.get('days', DEFAULT_DAYS)
    buyer_id = data.get('buyer_id', str(uuid.uuid4())[:8])
    if quantity <= 0:
        return jsonify({'error': '数量必须大于0'}), 400
    valid_types = ["数字", "英文", "foxmail"]
    if type_name not in valid_types:
        return jsonify({'error': f'无效类型，请选择: {", ".join(valid_types)}'}), 400
    selected_emails, selected_auths, error = assign_emails(type_name, quantity, buyer_id)
    if error:
        return jsonify({'error': error}), 400
    link_id = str(uuid.uuid4())[:8]
    links = load_links()
    now = datetime.now()
    links[link_id] = {
        'link_id': link_id,
        'buyer_id': buyer_id,
        'type': type_name,
        'emails': selected_emails,
        'auth_codes': selected_auths,
        'quantity': quantity,
        'created_at': now.strftime("%Y-%m-%d %H:%M:%S"),
        'expire_at': (now + timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S"),
        'status': 'active',
        'query_count': 0
    }
    save_links(links)
    link_url = f"https://你的域名/query?link={link_id}"
    return jsonify({
        'success': True,
        'link_id': link_id,
        'link_url': link_url,
        'type': type_name,
        'emails': selected_emails,
        'quantity': quantity,
        'expire_at': links[link_id]['expire_at']
    })


@app.route('/api/groups', methods=['GET'])
def get_groups():
    all_emails = get_all_emails()
    used_data = load_used_emails()
    used_emails = used_data.get("used", [])
    types = ["数字", "英文", "foxmail"]
    result = []
    for t in types:
        type_emails = [item for item in all_emails if detect_email_type(item["email"]) == t]
        available = len([item for item in type_emails if item["email"] not in used_emails])
        result.append({"name": t, "total": len(type_emails), "available": available})
    return jsonify(result)


@app.route('/admin')
def admin():
    links = load_links()
    used_data = load_used_emails()
    all_emails = get_all_emails()
    total = len(all_emails)
    used = len(used_data.get("used", []))
    html_admin = f'''
    <h2>📦 链接管理后台</h2>
    <p>🔑 密码：123456</p>
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
    <tr><th>链接ID</th><th>类型</th><th>数量</th><th>创建时间</th><th>过期时间</th><th>状态</th></tr>
    '''
    for link_id, data in links.items():
        status = '✅ 有效' if data['status'] == 'active' else '⛔ 禁用'
        html_admin += f"""
        <tr>
            <td>{link_id}</td>
            <td>{data.get('type', '未知')}</td>
            <td>{len(data['emails'])}</td>
            <td>{data['created_at']}</td>
            <td>{data['expire_at']}</td>
            <td>{status}</td>
        </tr>
        """
    html_admin += '</table>'
    return html_admin


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
    emails = link_data['emails']
    html_content = f'''
    <!DOCTYPE html>
    <html>
    <head><meta charset="UTF-8"><title>邮箱查询</title></head>
    <body style="font-family: Arial, sans-serif; max-width: 600px; margin: 40px auto; padding: 20px;">
        <h2>📬 邮箱查询</h2>
        <p>可用邮箱：<strong>{len(emails)}</strong> 个</p>
        <p>有效期至：{link_data['expire_at']}</p>
        <form action="/api/query_mail" method="post">
            <input type="hidden" name="link_id" value="{link_id}">
            <select name="email" style="width:100%;padding:10px;font-size:16px;margin:10px 0;">
                {''.join([f'<option value="{e}">{e}</option>' for e in emails])}
            </select>
            <button type="submit" style="width:100%;padding:12px;background:#4CAF50;color:white;border:none;font-size:16px;cursor:pointer;">查询邮件</button>
        </form>
    </body>
    </html>
    '''
    return html_content


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


@app.route('/api/query_mail', methods=['POST'])
def query_mail():
    link_id = request.form.get('link_id')
    email = request.form.get('email')
    links = load_links()
    if link_id not in links:
        return "链接无效"
    link_data = links[link_id]
    if email not in link_data['emails']:
        return "该邮箱不属于此链接"
    auth_code = link_data['auth_codes'].get(email)
    if not auth_code:
        return "授权码不存在"
    try:
        mail = imaplib.IMAP4_SSL("imap.qq.com")
        mail.login(email, auth_code)
        mail.select("INBOX")
        status, data = mail.search(None, "ALL")
        mail_ids = data[0].split() if data[0] else []
        if not mail_ids:
            return "<h3>暂无邮件</h3>"
        latest_ids = mail_ids[-5:]
        html_result = f"<h3>{email} 的最新邮件</h3>"
        for mid in reversed(latest_ids):
            _, msg_data = mail.fetch(mid, "(RFC822)")
            for part in msg_data:
                if isinstance(part, tuple):
                    msg = email.message_from_bytes(part[1])
                    subject = decode_str(msg.get("Subject", "无主题"))
                    sender = decode_str(msg.get("From", "未知发件人"))
                    content = ""
                    if msg.is_multipart():
                        for p in msg.walk():
                            if p.get_content_type() == "text/plain":
                                payload = p.get_payload(decode=True)
                                if payload:
                                    content = payload.decode('utf-8', errors='replace')
                                    break
                    else:
                        payload = msg.get_payload(decode=True)
                        if payload:
                            content = payload.decode('utf-8', errors='replace')
                    html_result += f"""
                    <div style="border-bottom:1px solid #ddd;padding:10px;">
                        <b>{sender}</b><br>
                        <span style="color:#666;">{subject}</span><br>
                        <span style="font-size:14px;">{content[:200]}</span>
                    </div>
                    """
        mail.close()
        mail.logout()
        return html_result
    except Exception as e:
        return f"查询失败：{str(e)}"


if __name__ == '__main__':
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)
