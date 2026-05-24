#!/usr/bin/env python3
"""
Telegram bot for Vibe-Trading backtest commands.
Uses GitHub Gist as a command queue and starts Codespace via GitHub API.
Includes a Flask health check server for Render compatibility.
"""

import asyncio
import json
import logging
import os
import threading
import time
from datetime import datetime

import requests
from flask import Flask
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

# Configure logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ---------- Environment variable validation ----------
REQUIRED_ENV_VARS = ["TELEGRAM_TOKEN", "GITHUB_TOKEN", "GIST_ID", "CODESPACE_NAME"]
missing_vars = [v for v in REQUIRED_ENV_VARS if not os.getenv(v)]
if missing_vars:
    logger.error(f"Missing required environment variables: {', '.join(missing_vars)}")
    exit(1)

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
GIST_ID = os.getenv("GIST_ID")
CODESPACE_NAME = os.getenv("CODESPACE_NAME")

GIST_API_URL = f"https://api.github.com/gists/{GIST_ID}"
CODESPACE_START_URL = f"https://api.github.com/user/codespaces/{CODESPACE_NAME}/start"

# ---------- Helper functions ----------
def update_gist(prompt: str) -> bool:
    """Write a pending command to the Gist."""
    headers = {"Authorization": f"token {GITHUB_TOKEN}"}
    data = {
        "files": {
            "backtest_prompt.json": {
                "content": json.dumps({
                    "prompt": prompt,
                    "status": "pending",
                    "timestamp": datetime.utcnow().isoformat()
                })
            }
        }
    }
    try:
        response = requests.patch(GIST_API_URL, headers=headers, json=data)
        return response.status_code == 200
    except Exception as e:
        logger.error(f"Failed to update Gist: {e}")
        return False

def start_codespace() -> bool:
    """Wake up the Codespace via GitHub API."""
    headers = {"Authorization": f"token {GITHUB_TOKEN}"}
    try:
        response = requests.post(CODESPACE_START_URL, headers=headers)
        return response.status_code == 201
    except Exception as e:
        logger.error(f"Failed to start Codespace: {e}")
        return False

def get_last_result():
    """Poll the Gist for completed/failed status and result."""
    headers = {"Authorization": f"token {GITHUB_TOKEN}"}
    try:
        response = requests.get(GIST_API_URL, headers=headers)
        if response.status_code == 200:
            data = response.json()
            content = data["files"]["backtest_prompt.json"]["content"]
            result = json.loads(content)
            return result.get("result"), result.get("status")
    except Exception as e:
        logger.error(f"Error fetching Gist result: {e}")
    return None, None

def clear_gist():
    """Reset Gist to idle state."""
    headers = {"Authorization": f"token {GITHUB_TOKEN}"}
    data = {
        "files": {
            "backtest_prompt.json": {
                "content": json.dumps({
                    "prompt": "",
                    "status": "idle",
                    "timestamp": datetime.utcnow().isoformat()
                })
            }
        }
    }
    try:
        requests.patch(GIST_API_URL, headers=headers, json=data)
    except Exception as e:
        logger.error(f"Failed to clear Gist: {e}")

# ---------- Telegram command handlers ----------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🚀 *Trade Commander Online*\n\n"
        "Send a backtest command like:\n"
        "`/backtest Backtest SMA crossover on AAPL for 2024`\n\n"
        "I'll wake up your Codespace, run it, and send the results back here.",
        parse_mode="Markdown"
    )

async def backtest_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    prompt = " ".join(context.args)
    if not prompt:
        await update.message.reply_text("Please provide a backtest prompt. Example: `/backtest AAPL SMA 20 2024`")
        return

    await update.message.reply_text(f"📡 Received. Preparing to launch backtest...\n\n`{prompt[:200]}...`")

    # 1. Store prompt in Gist
    if not update_gist(prompt):
        await update.message.reply_text("❌ Failed to store command. Check GitHub token and Gist ID.")
        return

    # 2. Start Codespace
    if not start_codespace():
        await update.message.reply_text("⚠️ Could not start Codespace. It might already be running or the API token lacks permissions.")
        return

    await update.message.reply_text("🔁 Codespace is waking up. I'll wait for results...")

    # 3. Poll for results (max 10 minutes, check every 10 seconds)
    for _ in range(60):
        await asyncio.sleep(10)
        result, status = get_last_result()
        if status == "completed":
            # Telegram has 4096 character limit; truncate if necessary
            result_preview = (result[:3500] + "...") if len(result) > 3500 else result
            await update.message.reply_text(f"✅ *Backtest Complete!*\n\n```\n{result_preview}\n```", parse_mode="Markdown")
            clear_gist()
            return
        elif status == "failed":
            await update.message.reply_text(f"❌ Backtest failed. Check your Codespace logs.\n\nError: {result[:500]}")
            clear_gist()
            return

    await update.message.reply_text("⏰ Backtest is taking longer than expected. Check your Codespace manually and try again.")

async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    clear_gist()
    await update.message.reply_text("Command canceled. Gist cleared.")

# ---------- Flask web server for Render health checks ----------
flask_app = Flask(__name__)

@flask_app.route('/')
@flask_app.route('/health')
def health_check():
    return "OK", 200

def run_web_server():
    """Run Flask server on the port Render expects."""
    port = int(os.environ.get('PORT', 5000))
    flask_app.run(host='0.0.0.0', port=port, debug=False)

# ---------- Main entry point ----------
def main():
    # Start the Flask web server in a background thread
    web_thread = threading.Thread(target=run_web_server, daemon=True)
    web_thread.start()
    logger.info("Flask health check server started.")

    # Build and run the Telegram bot
    application = Application.builder().token(TELEGRAM_TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("backtest", backtest_command))
    application.add_handler(CommandHandler("cancel", cancel_command))

    logger.info("Telegram bot started. Polling for messages...")
    application.run_polling()

if __name__ == "__main__":
    main()