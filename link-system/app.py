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
import shutil
import threading

app = Flask(__name__)
app.secret_key = 'your-secret-key-here'

# ===== 配置文件路径（支持持久化） =====
PERSISTENT_DIR = os.environ.get('PERSISTENT_DIR', '/data')
DATA_DIR = os.path.join(PERSISTENT_DIR, 'mail_data')
BACKUP_DIR = os.path.join(DATA_DIR, 'backups')

# 确保目录存在
for dir_path in [PERSISTENT_DIR, DATA_DIR, BACKUP_DIR]:
    if not os.path.exists(dir_path):
        try:
            os.makedirs(dir_path)
        except:
            pass

# 数据文件路径
LINKS_FILE = os.path.join(DATA_DIR, "links.json")
USED_EMAILS_FILE = os.path.join(DATA_DIR, "used_emails.json")

# 如果持久化目录不存在文件，从本地复制
LOCAL_LINKS_FILE = "links.json"
LOCAL_USED_FILE = "used_emails.json"

def init_data_files():
    """初始化数据文件，优先使用持久化存储的版本"""
    if os.path.exists(LINKS_FILE):
        print("✅ 使用持久化存储的链接数据")
    elif os.path.exists(LOCAL_LINKS_FILE):
        shutil.copy2(LOCAL_LINKS_FILE, LINKS_FILE)
        print("📋 从本地复制链接数据到持久化存储")
    
    if os.path.exists(USED_EMAILS_FILE):
        print("✅ 使用持久化存储的使用记录")
    elif os.path.exists(LOCAL_USED_FILE):
        shutil.copy2(LOCAL_USED_FILE, USED_EMAILS_FILE)
        print("📋 从本地复制使用记录到持久化存储")

init_data_files()

ACCOUNTS_FILE = "accounts.txt"
ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD', '060910')
DEFAULT_DAYS = 30
DOMAIN = os.environ.get('DOMAIN', 'mail-auto.zeabur.app')

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
print(f"数据存储路径: {DATA_DIR}")

def get_auth_map():
    return ACCOUNTS

# ===== 邮件解析函数 =====
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
    """获取最新邮件（包括收件箱和垃圾箱）"""
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
                    folder_info.append("收件箱")
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
        
        # 去重
        seen = set()
        unique_ids = []
        unique_folders = []
        for mid, folder in zip(all_mail_ids, folder_info):
            mid_str = mid.decode() if isinstance(mid, bytes) else str(mid)
            if mid_str not in seen:
                seen.add(mid_str)
                unique_ids.append(mid)
                unique_folders.append(folder)
        
        # 按ID排序，取最新的
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
                                from email.utils import parsedate_to_datetime
                                dt = parsedate_to_datetime(date_str)
                                # 转换为北京时间（加8小时）
                                from datetime import timedelta
                                dt = dt + timedelta(hours=8)
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
                            'time': send_time,
                            'folder': folder
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

# ===== 自动备份功能 =====
def backup_data():
    """自动备份所有数据文件"""
    try:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_folder = os.path.join(BACKUP_DIR, f"backup_{timestamp}")
        
        if not os.path.exists(backup_folder):
            os.makedirs(backup_folder)
        
        files_to_backup = [LINKS_FILE, USED_EMAILS_FILE]
        if os.path.exists(ACCOUNTS_FILE):
            files_to_backup.append(ACCOUNTS_FILE)
        
        for file_path in files_to_backup:
            if os.path.exists(file_path):
                shutil.copy2(file_path, os.path.join(backup_folder, os.path.basename(file_path)))
        
        latest_backup = os.path.join(BACKUP_DIR, "latest")
        if os.path.exists(latest_backup):
            shutil.rmtree(latest_backup)
        shutil.copytree(backup_folder, latest_backup)
        
        all_backups = sorted([d for d in os.listdir(BACKUP_DIR) 
                             if d.startswith("backup_") and os.path.isdir(os.path.join(BACKUP_DIR, d))])
        while len(all_backups) > 30:
            old_backup = all_backups.pop(0)
            old_path = os.path.join(BACKUP_DIR, old_backup)
            shutil.rmtree(old_path)
            print(f"清理旧备份: {old_backup}")
        
        print(f"✅ 数据备份完成: {backup_folder}")
        return True
    except Exception as e:
        print(f"❌ 备份失败: {e}")
        return False

def auto_backup_worker():
    """后台自动备份线程"""
    while True:
        time.sleep(3600)
        try:
            backup_data()
        except Exception as e:
            print(f"自动备份出错: {e}")

# ===== 清理过期链接功能 =====
def clean_expired_links():
    """清理所有过期链接"""
    links = load_links()
    now = datetime.now()
    cleaned_count = 0
    cleaned_links = {}
    
    for link_id, data in links.items():
        try:
            expire_time = datetime.strptime(data['expire_at'], "%Y-%m-%d %H:%M:%S")
            if now > expire_time and data.get('status') == 'active':
                cleaned_count += 1
                continue
        except:
            pass
        
        cleaned_links[link_id] = data
    
    if cleaned_count > 0:
        save_links(cleaned_links)
        backup_data()
    
    return cleaned_count

def auto_clean_worker():
    """后台自动清理过期链接（每天执行一次）"""
    while True:
        time.sleep(86400)
        try:
            count = clean_expired_links()
            if count > 0:
                print(f"✅ 自动清理了 {count} 个过期链接")
        except Exception as e:
            print(f"自动清理出错: {e}")

# 启动后台线程
backup_thread = threading.Thread(target=auto_backup_worker, daemon=True)
backup_thread.start()

clean_thread = threading.Thread(target=auto_clean_worker, daemon=True)
clean_thread.start()

# ===== 链接管理函数 =====
def load_links():
    try:
        if os.path.exists(LINKS_FILE):
            with open(LINKS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        return {}
    except:
        return {}

def save_links(data):
    with open(LINKS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def load_used_emails():
    try:
        if os.path.exists(USED_EMAILS_FILE):
            with open(USED_EMAILS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        return {"records": {}}
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

# ===== 路由 =====
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
    
    # 统计过期链接数量
    now = datetime.now()
    expired_count = 0
    for link_id, data in links.items():
        try:
            expire_time = datetime.strptime(data['expire_at'], "%Y-%m-%d %H:%M:%S")
            if now > expire_time and data.get('status') == 'active':
                expired_count += 1
        except:
            pass
    
    data_info = f"""
    <div style="background: #e8f5e9; padding: 10px; border-radius: 8px; margin: 10px 0;">
        <strong>💾 数据持久化状态：</strong>
        <span style="color: green;">✅ 已启用</span>
        <br>数据目录：{DATA_DIR}
        <br>链接文件：{'✅ 存在' if os.path.exists(LINKS_FILE) else '❌ 不存在'}
        <br>使用记录：{'✅ 存在' if os.path.exists(USED_EMAILS_FILE) else '❌ 不存在'}
    </div>
    """
    
    link_list = ""
    for link_id, data in links.items():
        status = '✅ 有效' if data['status'] == 'active' else '⛔ 禁用'
        try:
            expire_time = datetime.strptime(data['expire_at'], "%Y-%m-%d %H:%M:%S")
            if now > expire_time:
                status = '⏰ 已过期'
        except:
            pass
        
        link_list += f"""
        <tr>
            <td>{link_id}</td>
            <td>{data.get('buyer_id', '未知')}</td>
            <td>{data.get('type', '未知')}</td>
            <td>{len(data['emails'])}</td>
            <td>{data['created_at']}</td>
            <td>{data['expire_at']}</td>
            <td>{status}</td>
            <td>
                <a href="/api/disable_link?link_id={link_id}" 
                   onclick="return confirm('确定要禁用此链接吗？')"
                   style="color: red; text-decoration: none;">⛔ 禁用</a>
            </td>
        </tr>
        """
    
    html_admin = f'''
    <!DOCTYPE html>
    <html>
    <head><meta charset="UTF-8"><title>链接管理后台</title></head>
    <body style="font-family: Arial; max-width: 1200px; margin: 20px auto; padding: 20px;">
        <h2>📦 链接管理后台</h2>
        
        {data_info}
        
        <!-- 失效链接功能 -->
        <div style="background: #fff3cd; padding: 15px; border-radius: 8px; margin: 20px 0;">
            <h3>🚫 失效链接</h3>
            <form action="/api/disable_link" method="get" style="display: flex; gap: 10px; align-items: center;">
                <input type="text" name="link_id" placeholder="输入链接ID" 
                       style="padding: 10px; font-size: 14px; border: 2px solid #ddd; border-radius: 8px; flex: 1;">
                <button type="submit" 
                        onclick="return confirm('确定要禁用此链接吗？')"
                        style="padding: 10px 20px; background: #dc3545; color: white; border: none; 
                               border-radius: 8px; cursor: pointer; font-size: 14px;">
                    确认失效
                </button>
            </form>
        </div>
        
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
        
        <!-- 备份管理 -->
        <div style="background: #e3f2fd; padding: 15px; border-radius: 8px; margin: 20px 0;">
            <h3>💾 数据备份（实时备份已启用）</h3>
            <p>最新备份时间：{get_latest_backup_time()}</p>
            <p style="color: green; font-size: 14px;">✅ 生成/禁用/清理链接时自动备份</p>
            <a href="/api/manual_backup" style="padding: 10px 20px; background: #2196F3; color: white; 
               text-decoration: none; border-radius: 8px; display: inline-block;">
                立即备份
            </a>
            <a href="/api/download_latest_backup" style="padding: 10px 20px; background: #4CAF50; color: white; 
               text-decoration: none; border-radius: 8px; display: inline-block; margin-left: 10px;">
                下载最新备份
            </a>
        </div>
        
        <!-- 数据清理 -->
        <div style="background: #fff3e0; padding: 15px; border-radius: 8px; margin: 20px 0;">
            <h3>🧹 数据清理</h3>
            <p>当前过期链接数：<strong style="color: red;">{expired_count}</strong></p>
            <a href="/api/clean_expired_links" 
               onclick="return confirm('确定要清理所有过期链接吗？此操作不可恢复！')"
               style="padding: 10px 20px; background: #ff9800; color: white; 
                      text-decoration: none; border-radius: 8px; display: inline-block;">
                🗑️ 清理过期链接（{expired_count}个）
            </a>
        </div>
        
        <hr>
        <h3>🔗 链接列表（共 {len(links)} 个）</h3>
        <table border="1" cellpadding="8" style="width:100%;border-collapse:collapse;">
        <tr><th>链接ID</th><th>买家ID</th><th>类型</th><th>数量</th><th>创建时间</th><th>过期时间</th><th>状态</th><th>操作</th></tr>
        {link_list if link_list else '<tr><td colspan="8">暂无链接</td></tr>'}
        </table>
    </body>
    </html>
    '''
    return html_admin

def get_latest_backup_time():
    """获取最新备份时间"""
    latest_backup = os.path.join(BACKUP_DIR, "latest")
    if os.path.exists(latest_backup):
        timestamp = os.path.getmtime(latest_backup)
        return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M:%S")
    return "暂无备份"

@app.route('/api/disable_link')
def disable_link():
    """禁用指定链接"""
    if not session.get('logged_in'):
        return redirect('/login')
    
    link_id = request.args.get('link_id')
    if not link_id:
        return "请提供链接ID", 400
    
    links = load_links()
    if link_id not in links:
        return f"链接 {link_id} 不存在", 404
    
    links[link_id]['status'] = 'disabled'
    save_links(links)
    backup_data()
    
    return f'''
    <h2>✅ 链接已失效</h2>
    <p>链接ID: {link_id}</p>
    <p>状态已更新为: 禁用</p>
    <p>数据已保存并备份，重新部署不会丢失</p>
    <a href="/admin">返回后台</a>
    '''

@app.route('/api/clean_expired_links')
def api_clean_expired_links():
    """手动清理过期链接"""
    if not session.get('logged_in'):
        return redirect('/login')
    
    cleaned_count = clean_expired_links()
    
    return f'''
    <h2>✅ 清理完成</h2>
    <p>清理了 {cleaned_count} 个过期链接</p>
    <p>数据已备份</p>
    <a href="/admin">返回后台</a>
    '''

@app.route('/api/manual_backup')
def manual_backup():
    """手动触发备份"""
    if not session.get('logged_in'):
        return redirect('/login')
    
    success = backup_data()
    if success:
        return f'''
        <h2>✅ 备份成功</h2>
        <p>数据已备份到: {BACKUP_DIR}</p>
        <p>重新部署后可从该目录恢复数据</p>
        <a href="/admin">返回后台</a>
        '''
    else:
        return '''
        <h2>❌ 备份失败</h2>
        <p>请检查服务器权限</p>
        <a href="/admin">返回后台</a>
        '''

@app.route('/api/download_latest_backup')
def download_latest_backup():
    """下载最新备份"""
    if not session.get('logged_in'):
        return redirect('/login')
    
    latest_backup = os.path.join(BACKUP_DIR, "latest")
    if not os.path.exists(latest_backup):
        return "暂无备份文件"
    
    import zipfile
    zip_path = os.path.join(BACKUP_DIR, "backup_latest.zip")
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
        for root, dirs, files in os.walk(latest_backup):
            for file in files:
                file_path = os.path.join(root, file)
                arcname = os.path.relpath(file_path, latest_backup)
                zipf.write(file_path, arcname)
    
    return send_from_directory(BACKUP_DIR, "backup_latest.zip", as_attachment=True)

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
    backup_data()
    
    link_url = f"https://{DOMAIN}/query?link={link_id}"
    
    return f'''
    生成成功！<br>
    链接：<a href="{link_url}" target="_blank">{link_url}</a><br>
    邮箱：{', '.join(emails)}<br>
    有效期：{links[link_id]['expire_at']}<br>
    <a href="/admin">返回后台</a>
    '''

@app.route('/api/auto_create_link', methods=['POST'])
def auto_create_link():
    data = request.get_json() or {}
    
    type_name = data.get('type', '英文')
    
    try:
        quantity = int(data.get('quantity', 1))
        days = int(data.get('days', DEFAULT_DAYS))
    except (TypeError, ValueError):
        return "quantity 和 days 必须为整数", 400
    
    buyer_id = str(data.get('buyer_id') or str(uuid.uuid4())[:8])
    
    if quantity <= 0:
        return "数量必须大于0", 400
    
    valid_types = ['数字', '英文', 'foxmail']
    if type_name not in valid_types:
        return f"无效类型，请选择: {', '.join(valid_types)}", 400
    
    selected_emails, error = assign_emails(type_name, quantity, buyer_id)
    if error:
        return f"分配失败：{error}", 400
    
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
    backup_data()
    
    link_url = f"https://{DOMAIN}/query?link={link_id}"

    return f"""您购买的邮箱已发货

邮箱：
{chr(10).join(selected_emails)}

查询链接：{link_url}
有效期至：{links[link_id]['expire_at']}"""
    
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
    
    try:
        expire_time = datetime.strptime(link_data['expire_at'], "%Y-%m-%d %H:%M:%S")
        if now > expire_time:
            return "⛔ 链接已过期"
    except:
        pass
    
    if link_data['status'] != 'active':
        return "⛔ 链接已被禁用"
    
    html_content = f'''
    <!DOCTYPE html>
    <html>
    <head><meta charset="UTF-8"><title>邮箱查询系统</title></head>
    <body style="font-family: Arial, sans-serif; max-width: 600px; margin: 50px auto; padding: 20px;">
        <div style="background: white; padding: 30px; border-radius: 12px; box-shadow: 0 2px 10px rgba(0,0,0,0.1);">
            <h2>📬 邮箱查询系统</h2>
            <p>输入已绑定的邮箱，查看最新邮件（包括垃圾箱）</p>
            <p style="color: #999; font-size: 13px;">有效期至：{link_data['expire_at']}</p>
            <form action="/api/query_mail" method="post">
                <input type="hidden" name="link_id" value="{link_id}">
                <input type="text" name="email" placeholder="请输入邮箱地址" style="width:100%;padding:12px;font-size:16px;margin:10px 0;border:2px solid #ddd;border-radius:8px;">
                <button type="submit" style="width:100%;padding:12px;background:#4CAF50;color:white;border:none;font-size:16px;cursor:pointer;border-radius:8px;">查询最新邮件</button>
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
    if link_data['status'] != 'active':
        return "⛔ 链接已被禁用"
    
    if email not in link_data['emails']:
        return f"该邮箱不在本链接中，可查询的邮箱：{', '.join(link_data['emails'])}"
    
    link_data['query_count'] = link_data.get('query_count', 0) + 1
    save_links(links)
    
    if email not in ACCOUNTS:
        return f"邮箱 {email} 未绑定"
    
    # 使用老函数查询最新1封（包括垃圾箱）
    result = get_latest_mails(email, limit=1)
    
    if isinstance(result, dict) and 'error' in result:
        return f"查询失败：{result['error']}"
    
    if not result:
        return "<h3>📭 暂无邮件</h3>"
    
    # 取第一封邮件
    mail = result[0]
    
    # 文件夹标签
    folder_label = ""
    if mail.get('folder') and '垃圾' in str(mail.get('folder')):
        folder_label = '<span style="background: #ff9800; color: white; padding: 2px 8px; border-radius: 4px; font-size: 12px;">垃圾箱</span>'
    elif mail.get('folder') and 'Spam' in str(mail.get('folder')):
        folder_label = '<span style="background: #ff9800; color: white; padding: 2px 8px; border-radius: 4px; font-size: 12px;">垃圾箱</span>'
    
    html_result = f"""
    <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 20px auto; padding: 20px;">
        <h3>📧 {email} 的最新邮件 {folder_label}</h3>
        <p style="color: #999; font-size: 12px;">来源：{mail.get('folder', '收件箱')}</p>
        <div style="border: 1px solid #ddd; border-radius: 8px; padding: 15px; background: #f9f9f9;">
            <div style="margin-bottom: 10px;">
                <span style="font-weight: bold; color: #333;">发件人：</span>
                <span>{mail['sender']}</span>
            </div>
            <div style="margin-bottom: 10px;">
                <span style="font-weight: bold; color: #333;">主题：</span>
                <span>{mail['subject']}</span>
            </div>
            <div style="margin-bottom: 10px;">
                <span style="font-weight: bold; color: #333;">时间：</span>
                <span style="color: #666;">{mail.get('time', '未知')}</span>
            </div>
            <div style="border-top: 1px solid #eee; padding-top: 10px;">
                <div style="font-weight: bold; color: #333; margin-bottom: 5px;">邮件内容：</div>
                <div style="white-space: pre-wrap; word-break: break-word; color: #555;">
                    {mail['content'][:2000]}
                </div>
            </div>
        </div>
        <div style="margin-top: 20px; text-align: center;">
            <a href="/query?link={link_id}" style="color: #4CAF50; text-decoration: none;">返回查询</a>
        </div>
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
    print(f"数据持久化目录: {DATA_DIR}")
    print("后台密码: 060910")
    print("实时备份: ✅ 已启用")
    print("垃圾箱查询: ✅ 已启用")
    print("访问 http://127.0.0.1:5000")
    print("=" * 60)
    
    # 启动时清理过期链接
    cleaned = clean_expired_links()
    if cleaned > 0:
        print(f"✅ 启动时清理了 {cleaned} 个过期链接")
    
    # 启动时执行一次备份
    backup_data()
    
    app.run(host='0.0.0.0', port=8080)
