#!/usr/bin/env python3
"""
Ultimate Enterprise Telegram Suite - Central Configuration Engine (Dual-DB Core)
Filename: config.py
"""

import os
import certifi


# =====================================================================
# === 1. CORE API & SECURITY CONFIGURATIONS ===========================
# =====================================================================
CONFIG = {
    # Central Application Gateway Credentials (Telegram App Details)
    "API_ID": 37484332,
    "API_HASH": "88a5925d3ae1e27d76967460f98cb006",
    
    # Master Control Bot Authorization Token
    "BOT_TOKEN": "8966015094:AAEldB60lvhFwjsTsL1jlfxFPi32Fx73PO8",
    
    # Admin Control IDs via Environment Variables
    "ADMIN_ID": os.environ.get("ADMIN_ID", "5599766250"),
    
    # =====================================================================
    # === BATCH SEQUENCING AND MULTI-WORKER ARCHITECTURE SETTINGS =========
    # =====================================================================
    # Set unique identifier name for this VPS/Process instance (e.g., worker_01, worker_02)
    "WORKER_NODE_ID": os.environ.get("WORKER_NODE_ID", "worker_01"),
    
    # Define exact batch sequence range nodes allocation boundaries
    "BATCH_SEQUENCE_START": int(os.environ.get("BATCH_START", 1)),
    "BATCH_SEQUENCE_END": int(os.environ.get("BATCH_END", 250)),
    
    # Standard Connection Timing Configuration (In Seconds)
    "CONNECTION_TIMEOUT": 15.0,
    "API_REQUEST_RETRIES": 3,
    
    # Safe Anti-Ban Burst and Cooldown Intervals
    "ACCOUNT_LAUNCH_DELAY": (8, 15),
    "HUMAN_ADD_INTERVAL": (25, 45),
    "BURST_ADD_LIMIT": 5,
    "BURST_COOLDOWN_TIME": (60, 120),
    "ADDER_MAX_WORKER_SESSIONS": 10,
    "ADDER_HUMAN_ADD_INTERVAL": (8, 14),
    "ADDER_BURST_ADD_LIMIT": 6,
    "ADDER_BURST_COOLDOWN_TIME": (30, 50),
    "ADDER_PROGRESS_UPDATE_INTERVAL": 8,
    "LOOP_KEEP_ALIVE": (20, 35)
}



# =====================================================================
# === 2. DUAL DATABASE STORAGE PATHS (MONGODB ATLAS) =================
# =====================================================================
# =====================================================================
# === 2. SINGLE DATABASE STORAGE PATHS (MONGODB ATLAS) ================
# =====================================================================
MONGODB_SETTINGS = {
    # MongoDB Cluster URI
    "MONGO_URI": "mongodb+srv://sandeeptrip90_db_user:1234568h@cluster0.vcdatid.mongodb.net/?appName=Cluster0",
    
    # EXACT 5 COLLECTIONS FOR SINGLE-DB ECOSYSTEM
    "SOURCE_DB_NAME": "telegram_bot_db",
    "SOURCE_ACCOUNTS_COLLECTION": "source_accounts",
    "OTP_LOGS_COLLECTION": "otps",
    "SESSION_BACKUP_COLLECTION": "session_backups",
    "SCRAPED_MEMBERS_COLLECTION": "scraped_data",        # Fixed mapping for both Scraper & DMSender
    "PROCESSED_MEMBERS_COLLECTION": "processed_history",
    "TELEMETRY_LOGS_COLLECTION": "system_telemetry",
    
    # SSL/TLS Security Certification Layers
    "MONGO_KWARGS": {
        "tls": True,
        "tlsCAFile": certifi.where(),
        "tlsAllowInvalidCertificates": True,
        "serverSelectionTimeoutMS": 10000
    }
}

# =====================================================================
# === 3. ADVANCED DEVICE PROFILE MATRIX FOR SESSIONS ==================
# =====================================================================
DEVICE_PROFILES = [
    {"device_model": "PC 64bit", "system_version": "Windows 11", "app_version": "4.8.4"},
    {"device_model": "MacBook Pro", "system_version": "macOS 14.2", "app_version": "4.9.1"},
    {"device_model": "Ubuntu Desktop", "system_version": "Linux 22.04", "app_version": "4.8.1"},
    {"device_model": "Samsung Galaxy S23", "system_version": "Android 13", "app_version": "10.0.1"},
    {"device_model": "iPhone 15 Pro", "system_version": "iOS 17.1", "app_version": "10.2.0"}
]