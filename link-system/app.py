import os
os.environ['TZ'] = 'Asia/Shanghai'
try:
    import time
    time.tzset()
except:
    pass

from flask import Flask, request, jsonify, redirect, session
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


# ===== 读取账号 =====
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


# ===== 数据读写 =====
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


# ===== 判断邮箱类型 =====
def detect_email_type(email):
    if email.endswith("@foxmail.com"):
        return "foxmail"
    username = email.split("@")[0]
    if username.isdigit():
        return "数字"
    return "英文"


# ===== 分配邮箱（含备注） =====
def assign_emails(type_name, quantity, remark):
    all_emails = list(ACCOUNTS.keys())
    type_emails = [e for e in all_emails if detect_email_type(e) == type_name]
    
    if not type_emails:
        return None, f"类型 '{type_name}' 没有可用邮箱"
    
    used_data = load_used_emails()
    
    if "records" not in used_data or not isinstance(used_data["records"], dict):
        used_data = {"records": {}}
        save_used_emails(used_data)
        print("已重置 used_emails.json")
    
    if not remark:
        remark = "default"
    
    used_in_remark = used_data.get("records", {}).get(remark, [])
    
    if not isinstance(used_in_remark, list):
        used_in_remark = []
    
    available = [e for e in type_emails if e not in used_in_remark]
    
    if len(available) < quantity:
        return None, f"库存不足！需要 {quantity} 个，该备注下只剩 {len(available)} 个"
    
    selected = random.sample(available, quantity)
    
    if remark not in used_data["records"]:
        used_data["records"][remark] = []
    used_data["records"][remark].extend(selected)
    save_used_emails(used_data)
    
    return selected, None


# ===== 查询邮件 =====
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

def get_mail_content(msg):
    content = ""
    try:
        if msg.is_multipart():
            for part in msg.walk():
                if part.get_content_type() == "text/plain":
                    payload = part.get_payload(decode=True)
                    if payload:
                        content = payload.decode('utf-8', errors='replace')
                        break
        else:
            payload = msg.get_payload(decode=True)
            if payload:
                content = payload.decode('utf-8', errors='replace')
    except:
        content = "解析失败"
    return content.strip()

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
        
        try:
            mail.select("INBOX")
            status, data = mail.search(None, "ALL")
            if data[0]:
                for mid in data[0].split():
                    all_mail_ids.append(mid)
                    folder_info.append("INBOX")
        except:
            pass
        
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
            except:
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


# ===== 路由 =====
@app.route('/')
def index():
    return redirect('/admin')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        pwd = request.form.get('password')
        if pwd == ADMIN_PASSWORD:
            session['logged_in'] = True
            return redirect('/admin')
        else:
            return '<h2>密码错误</h2><p><a href="/login">重新输入</a></p>'
    
    return '''
    <!DOCTYPE html>
    <html>
    <head><meta charset="UTF-8"><title>后台登录</title></head>
    <body style="font-family: Arial; max-width: 400px; margin: 100px auto; padding: 20px;">
        <h2>后台登录</h2>
        <form method="post">
            <input type="password" name="password" placeholder="请输入密码" style="width:100%;padding:12px;font-size:16px;margin:10px 0;border:2px solid #ddd;border-radius:8px;">
            <button type="submit" style="width:100%;padding:12px;background:#4CAF50;color:white;border:none;font-size:16px;cursor:pointer;border-radius:8px;">登录</button>
        </form>
    </body>
    </html>
    '''

@app.route('/logout')
def logout():
    session.clear()
    return redirect('/login')


@app.route('/admin')
def admin():
    if not session.get('logged_in'):
        return redirect('/login')
    
    links = load_links()
    used_data = load_used_emails()
    all_emails = list(ACCOUNTS.keys())
    
    total = len(all_emails)
    all_used = []
    for remark, emails in used_data.get("records", {}).items():
        all_used.extend(emails)
    used = len(set(all_used))
    
    link_list = ""
    for link_id, data in links.items():
        status = '有效' if data['status'] == 'active' else '禁用'
        link_list += f"""
        <tr>
            <td>{link_id}</td>
            <td>{data.get('remark', data.get('buyer_id', '未知'))}</td>
            <td>{data.get('type', '未知')}</td>
            <td>{len(data['emails'])}</td>
            <td>{data['created_at']}</td>
            <td>{data['expire_at']}</td>
            <td>{status}</td>
        </tr>
        """
    
    html_admin = f'''
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <title>邮箱管理后台</title>
        <style>
            * {{ margin: 0; padding: 0; box-sizing: border-box; }}
            body {{ font-family: Arial, sans-serif; background: #f0f2f5; padding: 20px; }}
            .container {{ max-width: 1200px; margin: 0 auto; }}
            .card {{ background: white; border-radius: 12px; padding: 24px; margin-bottom: 20px; box-shadow: 0 2px 8px rgba(0,0,0,0.1); }}
            .card h2 {{ margin-bottom: 16px; font-size: 18px; }}
            .row {{ display: flex; gap: 12px; flex-wrap: wrap; align-items: end; }}
            .field {{ display: flex; flex-direction: column; }}
            .field label {{ font-size: 13px; color: #666; margin-bottom: 4px; }}
            .field input, .field select, .field textarea {{ padding: 10px; border: 2px solid #ddd; border-radius: 8px; font-size: 14px; }}
            .field input:focus, .field select:focus, .field textarea:focus {{ border-color: #667eea; outline: none; }}
            .btn {{ padding: 10px 30px; background: #4CAF50; color: white; border: none; border-radius: 8px; cursor: pointer; font-size: 14px; font-weight: bold; }}
            .btn:hover {{ background: #45a049; }}
            .btn-blue {{ background: #667eea; }}
            .btn-blue:hover {{ background: #5a67d8; }}
            .result-box {{ background: #f8f9fa; padding: 16px; border-radius: 8px; margin-top: 16px; display: none; }}
            .result-box .email-item {{ padding: 6px 0; border-bottom: 1px solid #eee; font-family: monospace; }}
            .result-box .link-area {{ background: #e8f5e9; padding: 12px; border-radius: 6px; margin-top: 10px; word-break: break-all; }}
            .copy-btn {{ padding: 6px 16px; background: #667eea; color: white; border: none; border-radius: 6px; cursor: pointer; font-size: 12px; margin-left: 8px; }}
            .copy-btn:hover {{ background: #5a67d8; }}
            .stats {{ display: flex; gap: 30px; flex-wrap: wrap; }}
            .stats span {{ font-size: 14px; color: #666; }}
            .stats strong {{ font-size: 18px; color: #1a1a2e; }}
            .logout {{ float: right; color: #e74c3c; text-decoration: none; font-size: 14px; }}
            table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
            th {{ background: #f8f9fa; padding: 10px; text-align: left; border-bottom: 2px solid #ddd; }}
            td {{ padding: 10px; border-bottom: 1px solid #eee; }}
            .separator {{ border: none; border-top: 2px dashed #ddd; margin: 20px 0; }}
    </style>
</head>
<body>
    <div class="container">
        <div class="card">
            <h2>邮箱管理后台 <a href="/logout" class="logout">退出</a></h2>
            <div class="stats">
                <span>总邮箱：<strong>{total}</strong></span>
                <span>已分配：<strong>{used}</strong></span>
                <span>可用：<strong>{total - used}</strong></span>
            </div>
        </div>

        <div class="card">
            <h2>批量生成（从库存随机抽取）</h2>
            <div class="row">
                <div class="field">
                    <label>邮箱种类</label>
                    <select id="emailType">
                        <option value="数字">数字邮箱</option>
                        <option value="英文">英文邮箱</option>
                        <option value="foxmail">foxmail邮箱</option>
                    </select>
                </div>
                <div class="field">
                    <label>数量</label>
                    <input type="number" id="emailCount" value="1" min="1" max="100">
                </div>
                <div class="field">
                    <label>有效期（天）</label>
                    <input type="number" id="emailDays" value="30" min="1" max="365">
                </div>
                <div class="field">
                    <label>备注（客户名称/批次号）</label>
                    <input type="text" id="remarkInput" placeholder="例如：客户A_20260715" style="min-width:150px;">
                </div>
                <button class="btn" onclick="generateLinks()">生成链接</button>
            </div>
            <div class="result-box" id="resultBox">
                <div id="resultContent"></div>
            </div>
        </div>

        <hr class="separator">

        <div class="card">
            <h2>输入邮箱生成链接（手动指定邮箱）</h2>
            <div class="row">
                <div class="field">
                    <label>输入邮箱（每行一个）</label>
                    <textarea id="manualEmails" placeholder="123456789@qq.com&#10;987654321@qq.com&#10;111222333@qq.com" rows="4" style="min-width:300px;font-family:monospace;"></textarea>
                </div>
                <div class="field">
                    <label>有效期（天）</label>
                    <input type="number" id="manualDays" value="30" min="1" max="365">
                </div>
                <div class="field">
                    <label>备注（客户名称/批次号）</label>
                    <input type="text" id="manualRemark" placeholder="例如：客户B_20260715" style="min-width:150px;">
                </div>
                <button class="btn btn-blue" onclick="generateManualLinks()">生成链接</button>
            </div>
            <div class="result-box" id="manualResultBox">
                <div id="manualResultContent"></div>
            </div>
        </div>

        <div class="card">
            <h2>已生成的链接</h2>
            <div style="overflow-x:auto;">
                <table>
                    <tr><th>链接ID</th><th>备注</th><th>类型</th><th>数量</th><th>创建时间</th><th>过期时间</th><th>状态</th></tr>
                    {link_list if link_list else '<tr><td colspan="7">暂无链接</td></tr>'}
                </table>
            </div>
        </div>
    </div>

    <script>
        async function generateLinks() {{
            const type = document.getElementById('emailType').value;
            const quantity = parseInt(document.getElementById('emailCount').value);
            const days = parseInt(document.getElementById('emailDays').value);
            const remark = document.getElementById('remarkInput').value.trim();

            if (!quantity || quantity < 1) {{
                alert('请输入有效数量');
                return;
            }}

            const resultBox = document.getElementById('resultBox');
            const resultContent = document.getElementById('resultContent');
            resultBox.style.display = 'block';
            resultContent.innerHTML = '生成中...';

            try {{
                const res = await fetch('/api/auto_create_link', {{
                    method: 'POST',
                    headers: {{ 'Content-Type': 'application/json' }},
                    body: JSON.stringify({{
                        type: type,
                        quantity: quantity,
                        days: days,
                        remark: remark
                    }})
                }});
                const data = await res.json();

                if (data.error) {{
                    resultContent.innerHTML = '<div style="color:red;">' + data.error + '</div>';
                    return;
                }}

                let html = '<div style="font-weight:bold;margin-bottom:10px;">生成成功</div>';
                html += '<div style="margin-bottom:8px;">备注：' + (data.remark || '无') + '</div>';
                html += '<div style="margin-bottom:8px;">邮箱列表：</div>';
                data.emails.forEach((email, idx) => {{
                    html += '<div class="email-item">' + (idx+1) + '. ' + email + '</div>';
                }});
                html += '<div class="link-area">查询链接：<span style="color:#667eea;">' + data.link_url + '</span>';
                html += '<button class="copy-btn" onclick="copyText(\'' + data.link_url + '\')">复制链接</button></div>';
                html += '<div style="margin-top:8px;color:#999;font-size:13px;">有效期至：' + data.expire_at + '</div>';
                html += '<button onclick="copyAll(\'' + data.emails.join(',') + '\', \'' + data.link_url + '\', \'' + (data.remark || '') + '\')" style="margin-top:12px;padding:8px 20px;background:#667eea;color:white;border:none;border-radius:6px;cursor:pointer;font-size:13px;">复制全部</button>';

                resultContent.innerHTML = html;
                location.reload();

            }} catch (e) {{
                resultContent.innerHTML = '<div style="color:red;">请求失败：' + e.message + '</div>';
            }}
        }}

        async function generateManualLinks() {{
            const emailsText = document.getElementById('manualEmails').value.trim();
            const days = parseInt(document.getElementById('manualDays').value) || 30;
            const remark = document.getElementById('manualRemark').value.trim();

            if (!emailsText) {{
                alert('请输入邮箱地址');
                return;
            }}

            const emails = emailsText.split('\\n').map(e => e.trim()).filter(e => e);
            if (emails.length === 0) {{
                alert('请输入有效邮箱地址');
                return;
            }}

            const resultBox = document.getElementById('manualResultBox');
            const resultContent = document.getElementById('manualResultContent');
            resultBox.style.display = 'block';
            resultContent.innerHTML = '生成中...';

            try {{
                const res = await fetch('/api/admin_create_link_manual', {{
                    method: 'POST',
                    headers: {{ 'Content-Type': 'application/json' }},
                    body: JSON.stringify({{
                        emails: emails,
                        days: days,
                        remark: remark
                    }})
                }});
                const data = await res.json();

                if (data.error) {{
                    resultContent.innerHTML = '<div style="color:red;">' + data.error + '</div>';
                    return;
                }}

                let html = '<div style="font-weight:bold;margin-bottom:10px;">生成成功</div>';
                html += '<div style="margin-bottom:8px;">备注：' + (data.remark || '无') + '</div>';
                html += '<div style="margin-bottom:8px;">邮箱列表：</div>';
                data.emails.forEach((email, idx) => {{
                    html += '<div class="email-item">' + (idx+1) + '. ' + email + '</div>';
                }});
                html += '<div class="link-area">查询链接：<span style="color:#667eea;">' + data.link_url + '</span>';
                html += '<button class="copy-btn" onclick="copyText(\'' + data.link_url + '\')">复制链接</button></div>';
                html += '<div style="margin-top:8px;color:#999;font-size:13px;">有效期至：' + data.expire_at + '</div>';
                html += '<button onclick="copyAll(\'' + data.emails.join(',') + '\', \'' + data.link_url + '\', \'' + (data.remark || '') + '\')" style="margin-top:12px;padding:8px 20px;background:#667eea;color:white;border:none;border-radius:6px;cursor:pointer;font-size:13px;">复制全部</button>';

                resultContent.innerHTML = html;
                location.reload();

            }} catch (e) {{
                resultContent.innerHTML = '<div style="color:red;">请求失败：' + e.message + '</div>';
            }}
        }}

        function copyText(text) {{
            navigator.clipboard.writeText(text).then(() => {{
                alert('已复制');
            }});
        }}

        function copyAll(emails, link, remark) {{
            const text = '备注：' + remark + '\\n邮箱：' + emails.replace(/,/g, '、') + '\\n查询链接：' + link;
            navigator.clipboard.writeText(text).then(() => {{
                alert('已复制全部内容');
            }});
        }}
    </script>
</body>
</html>
    '''
    return html_admin


# ===== API接口 =====
@app.route('/api/auto_create_link', methods=['POST'])
def auto_create_link():
    data = request.get_json()
    type_name = data.get('type', '英文')
    quantity = data.get('quantity', 1)
    days = data.get('days', DEFAULT_DAYS)
    remark = data.get('remark', '')
    
    if not remark:
        remark = f"生成_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    
    if quantity <= 0:
        return jsonify({'error': '数量必须大于0'}), 400
    
    valid_types = ["数字", "英文", "foxmail"]
    if type_name not in valid_types:
        return jsonify({'error': f'无效类型，请选择: {", ".join(valid_types)}'}), 400
    
    selected_emails, error = assign_emails(type_name, quantity, remark)
    if error:
        return jsonify({'error': error}), 400
    
    link_id = str(uuid.uuid4())[:8]
    links = load_links()
    now = datetime.now()
    
    links[link_id] = {
        'link_id': link_id,
        'buyer_id': remark,
        'remark': remark,
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
        'success': True,
        'link_id': link_id,
        'link_url': link_url,
        'type': type_name,
        'emails': selected_emails,
        'quantity': quantity,
        'remark': remark,
        'expire_at': links[link_id]['expire_at']
    })


@app.route('/api/admin_create_link_manual', methods=['POST'])
def admin_create_link_manual():
    data = request.get_json()
    emails = data.get('emails', [])
    days = data.get('days', 30)
    remark = data.get('remark', '')
    
    if not emails:
        return jsonify({'error': '请提供邮箱'})
    
    if not remark:
        remark = f"手动_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    
    link_id = str(uuid.uuid4())[:8]
    links = load_links()
    now = datetime.now()
    
    links[link_id] = {
        'link_id': link_id,
        'buyer_id': remark,
        'remark': remark,
        'type': '手动',
        'emails': emails,
        'quantity': len(emails),
        'created_at': now.strftime("%Y-%m-%d %H:%M:%S"),
        'expire_at': (now + timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S"),
        'status': 'active',
        'query_count': 0
    }
    save_links(links)
    
    link_url = f"https://{DOMAIN}/query?link={link_id}"
    
    return jsonify({
        'success': True,
        'link_id': link_id,
        'link_url': link_url,
        'emails': emails,
        'remark': remark,
        'expire_at': links[link_id]['expire_at']
    })


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
        return "链接已过期"
    
    if link_data['status'] != 'active':
        return "链接已被禁用"
    
    html_content = f'''
    <!DOCTYPE html>
    <html>
    <head><meta charset="UTF-8"><title>邮箱查询系统</title></head>
    <body style="font-family: Arial, sans-serif; max-width: 600px; margin: 50px auto; padding: 20px;">
        <div style="background: white; padding: 30px; border-radius: 12px; box-shadow: 0 2px 10px rgba(0,0,0,0.1);">
            <h2>邮箱查询系统</h2>
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
        return f"该邮箱不在本链接中"
    
    if email not in ACCOUNTS:
        return f"邮箱 {email} 未绑定"
    
    result = get_latest_mails(email, limit=10)
    
    if isinstance(result, dict) and 'error' in result:
        return f"查询失败：{result['error']}"
    
    if not result:
        return "<h3>暂无邮件</h3>"
    
    html_result = f"<h3>{email} 的最新邮件</h3>"
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
    for remark, emails in used_data.get("records", {}).items():
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


if __name__ == '__main__':
    print("=" * 60)
    print("邮箱查询系统启动")
    print("=" * 60)
    print(f"已绑定 {len(ACCOUNTS)} 个邮箱")
    print("后台密码: 060910")
    print("访问 http://127.0.0.1:8080")
    print("=" * 60)
    app.run(host='0.0.0.0', port=8080)
