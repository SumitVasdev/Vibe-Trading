#!/usr/bin/env python3
import asyncio
import logging
import os
import requests
import json
from datetime import datetime
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

# --- Configuration ---
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
GIST_ID = os.getenv("GIST_ID")
CODESPACE_NAME = os.getenv("CODESPACE_NAME")

GIST_API_URL = f"https://api.github.com/gists/{GIST_ID}"
CODESPACE_START_URL = f"https://api.github.com/user/codespaces/{CODESPACE_NAME}/start"

# --- Helper Functions ---
def update_gist(prompt):
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
    response = requests.patch(GIST_API_URL, headers=headers, json=data)
    return response.status_code == 200

def start_codespace():
    headers = {"Authorization": f"token {GITHUB_TOKEN}"}
    response = requests.post(CODESPACE_START_URL, headers=headers)
    return response.status_code == 201

def get_last_result():
    headers = {"Authorization": f"token {GITHUB_TOKEN}"}
    response = requests.get(GIST_API_URL, headers=headers)
    if response.status_code == 200:
        data = response.json()
        content = data["files"]["backtest_prompt.json"]["content"]
        result = json.loads(content)
        return result.get("result"), result.get("status")
    return None, None

def clear_gist():
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
    requests.patch(GIST_API_URL, headers=headers, json=data)

# --- Bot Handlers ---
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

    # Acknowledge command
    await update.message.reply_text(f"📡 Received. Preparing to launch backtest...\n\n`{prompt[:200]}...`")

    # 1. Store the prompt in the Gist
    if not update_gist(prompt):
        await update.message.reply_text("❌ Failed to store command. Check GitHub token and Gist ID.")
        return

    # 2. Wake up the Codespace
    if not start_codespace():
        await update.message.reply_text("⚠️ Could not start Codespace. It might already be running or the API token lacks permissions.")
        return

    await update.message.reply_text("🔁 Codespace is waking up. I'll wait for results...")

    # 3. Poll for results (max 10 minutes)
    for _ in range(60):
        await asyncio.sleep(10)
        result, status = get_last_result()
        if status == "completed":
            # Send results (truncated for Telegram's 4096 char limit)
            result_preview = result[:3500] + "..." if len(result) > 3500 else result
            await update.message.reply_text(f"✅ *Backtest Complete!*\n\n```\n{result_preview}\n```", parse_mode="Markdown")
            # Reset the Gist for next run
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

# --- Main ---
def main():
    if not TELEGRAM_TOKEN or not GITHUB_TOKEN or not GIST_ID or not CODESPACE_NAME:
        raise ValueError("Missing environment variables. Check TELEGRAM_TOKEN, GITHUB_TOKEN, GIST_ID, CODESPACE_NAME")

    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("backtest", backtest_command))
    app.add_handler(CommandHandler("cancel", cancel_command))

    # Run with polling (simple for Render free tier)
    app.run_polling()

if __name__ == "__main__":
    main()

