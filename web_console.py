#!/usr/bin/env python3
"""
Ultimate Enterprise Telegram Suite - Telegram Clone Web Dashboard
Filename: web_console.py
"""

import os
import logging
from datetime import datetime
from fastapi import APIRouter
from fastapi.responses import HTMLResponse
from telethon import TelegramClient
from telethon.sessions import StringSession

# Configuration Imports
from config import CONFIG, DEVICE_PROFILES

logger = logging.getLogger("WebConsoleModule")

console_router = APIRouter()
_db = None

def init_console_db(db_instance):
    """Initializes the database reference link when the async lifecycle boots up."""
    global _db
    _db = db_instance
    logger.info("⚡ Web Console Database reference initialized successfully.")

@console_router.get("/console", response_class=HTMLResponse)
async def live_admin_console():
    """Generates an ultra-premium 3-Pane Telegram Web Clone UI framework."""
    html_content = """
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Telegram Enterprise Console</title>
        <link href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.0.0/css/all.min.css" rel="stylesheet">
        <style>
            :root {
                --bg-dark: #0e1621;
                --bg-panel: #17212b;
                --bg-hover: #202b36;
                --bg-active: #2b5278;
                --text-main: #f5f5f5;
                --text-muted: #7f8c8d;
                --accent: #2481cc;
                --border: #101924;
                --msg-in: #182533;
                --msg-out: #2b5278;
            }
            * { margin: 0; padding: 0; box-sizing: border-box; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }
            body { background-color: var(--bg-dark); color: var(--text-main); display: flex; height: 100vh; overflow: hidden; }
            
            /* Pane 1: Accounts */
            .pane-accounts { width: 260px; background-color: var(--bg-panel); border-right: 1px solid var(--border); display: flex; flex-direction: column; z-index: 3; }
            .header { padding: 15px 20px; border-bottom: 1px solid var(--border); font-size: 16px; font-weight: bold; display: flex; align-items: center; gap: 10px; height: 60px; }
            .list-container { flex: 1; overflow-y: auto; list-style: none; }
            
            .list-item { padding: 12px 15px; border-bottom: 1px solid var(--border); cursor: pointer; transition: 0.2s; display: flex; align-items: center; gap: 10px; }
            .list-item:hover { background-color: var(--bg-hover); }
            .list-item.active { background-color: var(--bg-active); }
            .avatar { width: 40px; height: 40px; border-radius: 50%; background-color: var(--accent); display: flex; align-items: center; justify-content: center; font-weight: bold; font-size: 16px; flex-shrink: 0; }
            .info { flex: 1; overflow: hidden; display: flex; flex-direction: column; }
            .name { font-weight: 600; font-size: 14px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
            .sub { font-size: 12px; color: var(--text-muted); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; margin-top: 3px; }
            .status-dot { width: 10px; height: 10px; border-radius: 50%; background-color: #2ecc71; flex-shrink: 0; }
            .status-dot.revoked { background-color: #e74c3c; }

            /* Pane 2: Dialogs (Chats) */
            .pane-dialogs { width: 320px; background-color: var(--bg-panel); border-right: 1px solid var(--border); display: flex; flex-direction: column; z-index: 2; position: relative; }
            
            /* Pane 3: Messages */
            .pane-messages { flex: 1; background-color: var(--bg-dark); display: flex; flex-direction: column; position: relative; background-image: url('https://web.telegram.org/a/chat-bg-pattern-dark.png'); background-size: cover; background-blend-mode: overlay; background-color: rgba(14, 22, 33, 0.85); }
            .chat-history { flex: 1; overflow-y: auto; padding: 20px; display: flex; flex-direction: column; gap: 10px; }
            
            .message-bubble { max-width: 65%; padding: 10px 14px; border-radius: 12px; font-size: 14px; line-height: 1.4; position: relative; display: flex; flex-direction: column; }
            .message-bubble.in { background-color: var(--msg-in); align-self: flex-start; border-bottom-left-radius: 4px; }
            .message-bubble.out { background-color: var(--msg-out); align-self: flex-end; border-bottom-right-radius: 4px; }
            .msg-sender { font-size: 12px; font-weight: bold; color: #5288c1; margin-bottom: 4px; }
            .msg-time { font-size: 10px; color: rgba(255,255,255,0.5); align-self: flex-end; margin-top: 4px; }

            /* Loaders & Overlays */
            .overlay { position: absolute; inset: 0; background-color: var(--bg-dark); display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 15px; z-index: 10; color: var(--text-muted); }
            .loader { border: 3px solid rgba(255,255,255,0.1); border-top: 3px solid var(--accent); border-radius: 50%; width: 30px; height: 30px; animation: spin 1s linear infinite; }
            @keyframes spin { 0% { transform: rotate(0deg); } 100% { transform: rotate(360deg); } }
            .hidden { display: none !important; }
        </style>
    </head>
    <body>

        <!-- PANE 1: Accounts -->
        <div class="pane-accounts">
            <div class="header"><i class="fa-solid fa-users"></i> Accounts</div>
            <ul class="list-container" id="accountsList"></ul>
        </div>

        <!-- PANE 2: Dialogs (Chats) -->
        <div class="pane-dialogs">
            <div class="overlay" id="dialogsOverlay">Select an account to load chats</div>
            <div class="header" id="dialogsHeader"><i class="fa-regular fa-comments"></i> Chats</div>
            <ul class="list-container" id="dialogsList"></ul>
        </div>

        <!-- PANE 3: Messages -->
        <div class="pane-messages">
            <div class="overlay" id="messagesOverlay">Select a chat to view messages</div>
            <div class="header" id="messagesHeader"><i class="fa-solid fa-paper-plane"></i> Chat View</div>
            <div class="chat-history" id="messagesContainer"></div>
        </div>

        <script>
            let activePhone = null;
            let activeChatId = null;

            // Fetch Accounts Array
            async function fetchAccounts() {
                try {
                    const res = await fetch('/api/console/accounts');
                    const accounts = await res.json();
                    const container = document.getElementById('accountsList');
                    container.innerHTML = '';
                    
                    accounts.forEach(acc => {
                        const li = document.createElement('li');
                        li.className = `list-item ${activePhone === acc.phone ? 'active' : ''}`;
                        li.onclick = () => loadDialogs(acc.phone, acc.first_name);
                        
                        const init = (acc.first_name || '?').charAt(0).toUpperCase();
                        li.innerHTML = `
                            <div class="avatar">${init}</div>
                            <div class="info">
                                <span class="name">${acc.first_name || 'Unknown Node'}</span>
                                <span class="sub">+${acc.phone}</span>
                            </div>
                            <div class="status-dot ${acc.status === 'active' ? '' : 'revoked'}"></div>
                        `;
                        container.appendChild(li);
                    });
                } catch(e) { console.error("Accounts fetch error", e); }
            }

            // Fetch Dialogs for Selected Account
            async function loadDialogs(phone, name) {
                activePhone = phone;
                fetchAccounts(); // Update active selection class
                
                const overlay = document.getElementById('dialogsOverlay');
                const list = document.getElementById('dialogsList');
                const header = document.getElementById('dialogsHeader');
                
                header.innerHTML = `<i class="fa-regular fa-comments"></i> Chats (+${phone})`;
                overlay.innerHTML = `<div class="loader"></div><p>Syncing encrypted chats...</p>`;
                overlay.classList.remove('hidden');
                list.innerHTML = '';
                
                // Clear messages pane
                document.getElementById('messagesOverlay').classList.remove('hidden');
                document.getElementById('messagesOverlay').innerText = "Select a chat to view messages";
                activeChatId = null;

                try {
                    const res = await fetch(`/api/console/dialogs/${phone}`);
                    const data = await res.json();
                    
                    if(data.status === 'success') {
                        data.dialogs.forEach(chat => {
                            const li = document.createElement('li');
                            li.className = 'list-item';
                            li.onclick = () => loadMessages(phone, chat.id, chat.title);
                            
                            const init = chat.title.charAt(0).toUpperCase();
                            li.innerHTML = `
                                <div class="avatar" style="background-color: ${chat.is_group ? '#e67e22' : '#9b59b6'}">${init}</div>
                                <div class="info">
                                    <span class="name">${chat.title}</span>
                                    <span class="sub">${chat.last_message}</span>
                                </div>
                            `;
                            list.appendChild(li);
                        });
                        overlay.classList.add('hidden');
                    } else {
                        overlay.innerHTML = `<p style="color: #e74c3c;"><i class="fa-solid fa-triangle-exclamation"></i> ${data.reason}</p>`;
                    }
                } catch(err) {
                    overlay.innerHTML = `<p>Error mapping matrix endpoints.</p>`;
                }
            }

            // Fetch Messages for Selected Chat
            async function loadMessages(phone, chatId, chatTitle) {
                activeChatId = chatId;
                
                // Highlight active dialog (visual only)
                const dialogItems = document.getElementById('dialogsList').children;
                for(let item of dialogItems) { item.classList.remove('active'); }
                event.currentTarget.classList.add('active');

                const overlay = document.getElementById('messagesOverlay');
                const container = document.getElementById('messagesContainer');
                const header = document.getElementById('messagesHeader');
                
                header.innerHTML = `<b>${chatTitle}</b> <span style="font-size:12px; color:#7f8c8d; margin-left:10px;">ID: ${chatId}</span>`;
                overlay.innerHTML = `<div class="loader"></div><p>Fetching encrypted history...</p>`;
                overlay.classList.remove('hidden');
                container.innerHTML = '';

                try {
                    const res = await fetch(`/api/console/messages/${phone}/${chatId}`);
                    const data = await res.json();
                    
                    if(data.status === 'success') {
                        data.messages.forEach(msg => {
                            const div = document.createElement('div');
                            const typeClass = msg.is_self ? 'out' : 'in';
                            div.className = `message-bubble ${typeClass}`;
                            
                            let senderHtml = msg.is_self ? '' : `<div class="msg-sender">${msg.sender_name}</div>`;
                            
                            div.innerHTML = `
                                ${senderHtml}
                                <span>${msg.text}</span>
                                <div class="msg-time">${msg.time}</div>
                            `;
                            container.appendChild(div);
                        });
                        
                        overlay.classList.add('hidden');
                        // Auto-scroll to bottom
                        container.scrollTop = container.scrollHeight;
                    } else {
                        overlay.innerHTML = `<p style="color: #e74c3c;">${data.reason}</p>`;
                    }
                } catch(err) {
                    overlay.innerHTML = `<p>Error loading messages.</p>`;
                }
            }

            // Initialize
            fetchAccounts();
        </script>
    </body>
    </html>
    """
    return HTMLResponse(content=html_content)

@console_router.get("/api/console/accounts")
async def api_console_accounts():
    """Fetches list of all accounts for the Left Sidebar."""
    global _db
    if not _db: return []
    accounts = _db.get_all_suite_sessions()
    catalog = []
    for acc in accounts:
        catalog.append({
            "phone": acc.get("phone"),
            "first_name": acc.get("first_name", acc.get("device_model", "Unknown Node")),
            "status": acc.get("status", "pending")
        })
    return catalog

@console_router.get("/api/console/dialogs/{phone}")
async def api_console_dialogs(phone: str):
    """Connects client momentarily to fetch the top 20 dialogs (Chats/Groups)."""
    global _db
    if not _db: return {"status": "error", "reason": "Database reference pointer uninitialized."}

    clean_phone = phone.replace("+", "").replace(" ", "")
    record = _db.get_session_by_phone(clean_phone)
    
    # 🔥 BUG FIX: Checking both 'session_string' and 'session' to prevent Target Missing Error
    session_token = record.get("session_string") or record.get("session") if record else None
    
    if not record or not session_token:
        return {"status": "error", "reason": "Target session token missing inside database layers."}
        
    device = record.get("device_metadata") or {
        "device_model": record.get("device_model", "PC 64bit"),
        "system_version": record.get("system_version", "Windows 11"),
        "app_version": record.get("app_version", "4.8.4")
    }

    # Maintain strict device fingerprint
    temp_client = TelegramClient(
        StringSession(session_token),
        api_id=int(record.get("api_id", CONFIG["API_ID"])),
        api_hash=str(record.get("api_hash", CONFIG["API_HASH"])),
        device_model=device["device_model"],
        system_version=device["system_version"],
        app_version=device["app_version"]
    )
    
    dialogs_payload = []
    try:
        await temp_client.connect()
        if not await temp_client.is_user_authorized():
            return {"status": "error", "reason": "Authorization trace expired or revoked."}

        dialogs = await temp_client.get_dialogs(limit=25)
        for chat in dialogs:
            title = chat.name or "Unknown Chat"
            last_msg = str(chat.message.message or "[Media File/Sticker]").strip() if chat.message else "No messages"
            
            dialogs_payload.append({
                "id": str(chat.id),
                "title": title,
                "is_group": chat.is_group or chat.is_channel,
                "last_message": last_msg[:40] + "..." if len(last_msg) > 40 else last_msg
            })
            
        return {"status": "success", "phone": clean_phone, "dialogs": dialogs_payload}
        
    except Exception as e:
        logger.error(f"Dialogs fetch exception: {e}")
        return {"status": "error", "reason": str(e)}
    finally:
        # Instantly release memory block
        try: await temp_client.disconnect()
        except: pass

@console_router.get("/api/console/messages/{phone}/{chat_id}")
async def api_console_messages(phone: str, chat_id: str):
    """Connects client momentarily to fetch the last 40 messages of a specific chat."""
    global _db
    if not _db: return {"status": "error", "reason": "Database reference pointer uninitialized."}

    clean_phone = phone.replace("+", "").replace(" ", "")
    record = _db.get_session_by_phone(clean_phone)
    
    # 🔥 BUG FIX: Key alignment matrix validation
    session_token = record.get("session_string") or record.get("session") if record else None
    if not record or not session_token:
        return {"status": "error", "reason": "Session missing."}
        
    device = record.get("device_metadata") or {
        "device_model": record.get("device_model", "PC 64bit"),
        "system_version": record.get("system_version", "Windows 11"),
        "app_version": record.get("app_version", "4.8.4")
    }

    temp_client = TelegramClient(
        StringSession(session_token),
        api_id=int(record.get("api_id", CONFIG["API_ID"])),
        api_hash=str(record.get("api_hash", CONFIG["API_HASH"])),
        device_model=device["device_model"],
        system_version=device["system_version"],
        app_version=device["app_version"]
    )
    
    messages_payload = []
    try:
        await temp_client.connect()
        if not await temp_client.is_user_authorized():
            return {"status": "error", "reason": "Session Expired."}

        # Telegram IDs are integers, checking if it's a negative ID for groups
        target_entity = int(chat_id)
        
        messages = await temp_client.get_messages(target_entity, limit=40)
        
        # Reverse to show oldest first in the chat window (like normal chat flow)
        for msg in reversed(messages):
            if not msg.message and not msg.media: continue
            
            text = str(msg.message or "[Media Document/Photo]").strip()
            time_node = msg.date.strftime("%H:%M")
            
            # Identify sender name
            sender_name = "User"
            if msg.sender:
                sender_name = getattr(msg.sender, 'first_name', '') or getattr(msg.sender, 'title', 'User')
            
            messages_payload.append({
                "id": msg.id,
                "text": text,
                "time": time_node,
                "is_self": msg.out,
                "sender_name": sender_name.strip()
            })
            
        return {"status": "success", "messages": messages_payload}
        
    except Exception as e:
        logger.error(f"Messages fetch exception: {e}")
        return {"status": "error", "reason": str(e)}
    finally:
        # Instantly release memory block
        try: await temp_client.disconnect()
        except: pass