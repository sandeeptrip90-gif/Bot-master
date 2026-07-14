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
    "API_ID": 38223087, # Leogerwal
    "API_HASH": "f3448783d23ace67fecdef3f392d2e47", # Leogerwal
    
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
    "BATCH_SEQUENCE_START": int(os.environ.get("BATCH_START", 0)),
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
    # 💻 WINDOWS ENTERPRISE WORKSTATION ARRAYS
    {"device_model": "Dell OptiPlex 7010", "system_version": "Windows 11 Pro 23H2", "app_version": "5.1.0"},
    {"device_model": "HP EliteDesk 800 G9", "system_version": "Windows 11 Enterprise", "app_version": "5.0.4"},
    {"device_model": "Lenovo ThinkCentre M90q", "system_version": "Windows 11 Pro", "app_version": "5.1.2"},
    {"device_model": "Microsoft Surface Laptop 6", "system_version": "Windows 11 Home", "app_version": "5.0.0"},
    {"device_model": "ASUS ExpertBook B9", "system_version": "Windows 11 Pro", "app_version": "5.1.1"},
    {"device_model": "Acer TravelMate P6", "system_version": "Windows 11 Pro", "app_version": "5.0.8"},
    {"device_model": "MSI Prestige 16", "system_version": "Windows 11 Pro", "app_version": "5.1.3"},
    {"device_model": "Dell Latitude 7440", "system_version": "Windows 11 Enterprise", "app_version": "5.1.4"},
    {"device_model": "HP ZBook Fury", "system_version": "Windows 11 Workstation", "app_version": "5.1.5"},
    {"device_model": "Lenovo ThinkPad X1 Carbon Gen 12", "system_version": "Windows 11 Pro", "app_version": "5.1.2"},
    
    # 🍏 APPLE macOS SEQUOIA & SONOMA MATRIX CONTROLLERS
    {"device_model": "MacBook Pro M3 Max", "system_version": "macOS 15.0 Sequoia", "app_version": "5.2.1"},
    {"device_model": "MacBook Air M2", "system_version": "macOS 14.5 Sonoma", "app_version": "5.1.0"},
    {"device_model": "Mac Studio Core", "system_version": "macOS 14.6.1 Sonoma", "app_version": "5.0.2"},
    {"device_model": "iMac 24-inch", "system_version": "macOS 15.1 Sequoia", "app_version": "5.2.4"},
    
    # 🐧 LINUX DEBIAN / REDHAT PRODUCTION DEPLOYMENTS
    {"device_model": "Ubuntu Desktop", "system_version": "Linux 24.04 LTS", "app_version": "5.0.0"},
    {"device_model": "Fedora Workstation", "system_version": "Linux Kernel 6.8", "app_version": "4.16.5"},
    {"device_model": "RedHat Enterprise", "system_version": "RHEL 9.4 Node", "app_version": "4.15.2"},
    {"device_model": "Debian GNU/Linux", "system_version": "Debian 12 Bookworm", "app_version": "5.1.1"},

    # 🤖 ANDROID EXTENDED BRAND MATRIX (REDMI, POCO, REALME, VIVO, OPPO, HONOR, INFINIX, MICROMAX)
    {"device_model": "Samsung Galaxy S24 Ultra", "system_version": "Android 14", "app_version": "11.2.4"},
    {"device_model": "Samsung Galaxy Z Fold 6", "system_version": "Android 14", "app_version": "11.2.1"},
    {"device_model": "Google Pixel 9 Pro XL", "system_version": "Android 15", "app_version": "11.3.0"},
    {"device_model": "OnePlus 12", "system_version": "Android 14", "app_version": "11.1.8"},
    
    # 🔴 REDMI & XIAOMI SERIES
    {"device_model": "Redmi Note 13 Pro+ 5G", "system_version": "Android 14 (HyperOS)", "app_version": "11.0.6"},
    {"device_model": "Redmi Note 12 Pro", "system_version": "Android 13", "app_version": "10.5.2"},
    {"device_model": "Redmi 13C", "system_version": "Android 14", "app_version": "11.0.1"},
    {"device_model": "Xiaomi 14 Ultra", "system_version": "Android 14", "app_version": "11.0.5"},
    
    # 🟡 POCO PERFORMANCE ENGINES
    {"device_model": "Poco F6 Pro", "system_version": "Android 14", "app_version": "11.1.2"},
    {"device_model": "Poco X6 Pro 5G", "system_version": "Android 14", "app_version": "11.0.9"},
    {"device_model": "Poco M6 Pro", "system_version": "Android 13", "app_version": "10.4.5"},
    
    # 🟠 REALME DYNAMIC LAYERS
    {"device_model": "Realme GT 6", "system_version": "Android 14", "app_version": "11.1.5"},
    {"device_model": "Realme 12 Pro+ 5G", "system_version": "Android 14", "app_version": "11.0.8"},
    {"device_model": "Realme Narzo 70 Pro", "system_version": "Android 14", "app_version": "11.0.3"},
    {"device_model": "Realme C67", "system_version": "Android 14", "app_version": "11.0.1"},
    
    # 🔵 VIVO FLAGSHIPS & SLOTS
    {"device_model": "Vivo X100 Pro", "system_version": "Android 14", "app_version": "11.1.8"},
    {"device_model": "Vivo V30 Pro", "system_version": "Android 14", "app_version": "11.0.7"},
    {"device_model": "Vivo T3 5G", "system_version": "Android 14", "app_version": "11.0.4"},
    {"device_model": "Vivo Y200 Pro", "system_version": "Android 14", "app_version": "11.0.2"},
    
    # 🟢 OPPO SMARTPHONE MODULES
    {"device_model": "Oppo Find X7 Ultra", "system_version": "Android 14", "app_version": "11.1.6"},
    {"device_model": "Oppo Reno 12 Pro", "system_version": "Android 14", "app_version": "11.1.1"},
    {"device_model": "Oppo F27 Pro+ 5G", "system_version": "Android 14", "app_version": "11.0.5"},
    {"device_model": "Oppo A3 Pro", "system_version": "Android 14", "app_version": "11.0.2"},
    
    # 🛡️ HONOR SECURITY PLATFORMS
    {"device_model": "Honor Magic 6 Pro", "system_version": "Android 14", "app_version": "11.1.7"},
    {"device_model": "Honor 200 Pro", "system_version": "Android 14", "app_version": "11.0.9"},
    {"device_model": "Honor X9b", "system_version": "Android 13", "app_version": "10.6.1"},
    
    # ⚡ INFINIX HIGH-SPEED NODES
    {"device_model": "Infinix GT 20 Pro", "system_version": "Android 14", "app_version": "11.0.8"},
    {"device_model": "Infinix Note 40 Pro+ 5G", "system_version": "Android 14", "app_version": "11.0.5"},
    {"device_model": "Infinix Zero 30", "system_version": "Android 13", "app_version": "10.5.1"},
    {"device_model": "Infinix Smart 8", "system_version": "Android 13", "app_version": "10.2.0"},
    
    # 🇮🇳 MICROMAX LEGACY & RECENT ENTRIES
    {"device_model": "Micromax In Note 2", "system_version": "Android 12", "app_version": "9.8.4"},
    {"device_model": "Micromax In 2c", "system_version": "Android 11", "app_version": "9.2.1"},
    {"device_model": "Micromax In 1b", "system_version": "Android 10", "app_version": "8.9.5"},

    # 🍏 APPLE iOS HIGH-FIDELITY MOBILE PLATFORMS
    {"device_model": "iPhone 16 Pro Max", "system_version": "iOS 18.0", "app_version": "11.4.0"},
    {"device_model": "iPhone 16 Pro", "system_version": "iOS 18.0", "app_version": "11.4.0"},
    {"device_model": "iPhone 15 Pro Max", "system_version": "iOS 17.5.1", "app_version": "11.3.1"},
    {"device_model": "iPhone 15 Pro", "system_version": "iOS 17.5", "app_version": "11.3.0"},
    {"device_model": "iPhone 15", "system_version": "iOS 17.5", "app_version": "11.3.0"},
    {"device_model": "iPhone 14 Plus", "system_version": "iOS 17.4", "app_version": "11.2.0"},
    {"device_model": "iPhone 14 Pro", "system_version": "iOS 17.4", "app_version": "11.2.2"},
    {"device_model": "iPad Pro M4", "system_version": "iPadOS 17.6", "app_version": "11.2.5"},
    {"device_model": "iPad Air M2", "system_version": "iPadOS 17.5", "app_version": "11.2.4"},
]