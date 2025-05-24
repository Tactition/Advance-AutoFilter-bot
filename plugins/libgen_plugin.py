import logging
import asyncio
import urllib.parse
import os
import aiohttp
import aiofiles
from info import *
from Script import *
from datetime import datetime, timedelta
from collections import defaultdict
from pyrogram import Client, filters, enums
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.errors import BadRequest, FloodWait
from libgen_api_enhanced import LibgenSearch

# Initialize LibgenSearch instance
lg = LibgenSearch()
logger = logging.getLogger(__name__)

# Concurrency control
USER_LOCKS = defaultdict(asyncio.Lock)
LAST_PROGRESS_UPDATE = defaultdict(lambda: (0, datetime.min))

def escape_markdown(text: str) -> str:
    escape_chars = r"_*[]()~`>#+-=|{}.!"
    return "".join(f"\\{char}" if char in escape_chars else char for char in text)

async def libgen_search(query: str):
    """Reusable search function"""
    try:
        return lg.search_title_filtered(query, filters={}, exact_match=True)
    except FloodWait as e:
        await asyncio.sleep(e.value + 2)
        return lg.search_title_filtered(query, filters={}, exact_match=True)

async def create_search_buttons(results: list, query: str):
    """Create inline keyboard markup for search results"""
    encoded_query = urllib.parse.quote(query)
    buttons = []
    for idx, result in enumerate(results[:10], 1):
        title = result['Title'][:35] + "..." if len(result['Title']) > 35 else result['Title']
        callback_data = f"lgdl_{encoded_query}_{idx-1}"
        buttons.append([
            InlineKeyboardButton(
                f"{result['Extension'].upper()} ~{result['Size']} - {title}",
                callback_data=callback_data
            )
        ])
    return InlineKeyboardMarkup(buttons)

async def download_libgen_file(url: str, temp_path: str, progress_msg, user_id: int):
    """Reusable file downloader with progress"""
    last_percent = -1
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as response:
            if response.status != 200:
                raise Exception(f"Download failed with status {response.status}")

            total_size = int(response.headers.get('content-length', 0)) or None
            downloaded = 0
            
            async with aiofiles.open(temp_path, 'wb') as f:
                async for chunk in response.content.iter_chunked(1024*1024):
                    if not chunk:
                        continue
                    await f.write(chunk)
                    downloaded += len(chunk)
                    
                    if total_size:
                        percent = round((downloaded / total_size) * 100)
                        now = datetime.now()
                        
                        if percent != last_percent and (
                            percent - last_percent >= 1 or 
                            now - LAST_PROGRESS_UPDATE[user_id][1] > timedelta(seconds=2)
                        ):
                            try:
                                await progress_msg.edit(f"⬇️ Downloading file... ({percent}%)")
                                last_percent = percent
                                LAST_PROGRESS_UPDATE[user_id] = (percent, now)
                            except Exception as e:
                                logger.warning(f"Progress update failed: {e}")

async def upload_to_telegram(client, temp_path: str, book: dict, progress_msg, chat_id: int, user_id: int):
    """Reusable Telegram uploader with progress"""
    last_percent = -1
    
    async def progress(current, total):
        nonlocal last_percent
        percent = round(current * 100 / total)
        now = datetime.now()
        if percent != last_percent or now - LAST_PROGRESS_UPDATE[user_id][1] > timedelta(seconds=2):
            try:
                await progress_msg.edit(f"📤 Uploading... ({percent}%)")
                LAST_PROGRESS_UPDATE[user_id] = (percent, now)
            except Exception as e:
                logger.warning(f"Upload progress update failed: {e}")

    return await client.send_document(
        chat_id=chat_id,
        document=temp_path,
        caption=f"📚 {book.get('Title', 'Unknown')}\n👤 Author: {book.get('Author', 'Unknown')}\n📦 Size: {book.get('Size', 'N/A')}",
        progress=progress
    )

async def handle_auto_delete(client, sent_msg, chat_id: int):
    """Handle auto-delete functionality"""
    if AUTO_DELETE_TIME > 0:
        deleter_msg = await client.send_message(
            chat_id=chat_id,
            text=script.AUTO_DELETE_MSG.format(AUTO_DELETE_MIN),
            reply_to_message_id=sent_msg.id
        )
        
        async def auto_delete_task():
            await asyncio.sleep(AUTO_DELETE_TIME)
            try:
                await sent_msg.delete()
                await deleter_msg.edit(script.FILE_DELETED_MSG)
            except Exception as e:
                logger.error(f"Auto-delete failed: {e}")
        
        asyncio.create_task(auto_delete_task())

async def log_download(client, temp_path: str, book: dict, callback_query):
    """Log download to channel"""
    try:
        await client.send_document(
            LOG_CHANNEL,
            document=temp_path,
            caption=(
                f"📥 User {callback_query.from_user.mention} downloaded:\n"
                f"📖 Title: {escape_markdown(book.get('Title', 'Unknown'))}\n"
                f"👤 Author: {escape_markdown(book.get('Author', 'Unknown'))}\n"
                f"📦 Size: {escape_markdown(book.get('Size', 'N/A'))}\n"
                f"👤 User ID: {callback_query.from_user.id}\n"
                f"🤖 Via: {client.me.first_name}"
            ),
            parse_mode=enums.ParseMode.HTML
        )
    except Exception as log_error:
        logger.error(f"Failed to send log: {log_error}")

@Client.on_message(filters.command('search') & filters.private)
async def handle_search_command(client, message):
    """Handle /search command"""
    try:
        query = message.text.split(' ', 1)[1]
        progress_msg = await message.reply("🔍 Searching in The Torrent Servers of Magical Library...")
        
        results = await libgen_search(query)
        if not results:
            return await progress_msg.edit("❌ No results found for your query.")

        buttons = await create_search_buttons(results, query)
        response = [
            f"📚 Found {len(results)} results for <b>{query}</b>:",
            f"Rᴇǫᴜᴇsᴛᴇᴅ Bʏ ☞ {message.from_user.mention if message.from_user else 'Unknown User'}",
            f"Sʜᴏᴡɪɴɢ ʀᴇsᴜʟᴛs ғʀᴏᴍ ᴛʜᴇ Mᴀɢɪᴄᴀʟ Lɪʙʀᴀʀʏ ᴏғ Lɪʙʀᴀʀʏ Gᴇɴᴇsɪs",
        ]

        await progress_msg.edit(
            "\n".join(response),
            reply_markup=buttons,
            parse_mode=enums.ParseMode.HTML
        )

    except IndexError:
        await message.reply("⚠️ Please provide a search query!\nExample: `/search The Great Gatsby`", 
                          parse_mode=enums.ParseMode.MARKDOWN)
    except Exception as e:
        logger.error(f"Search error: {e}")
        await message.reply(f"❌ Search failed: {str(e)}")

@Client.on_callback_query(filters.regex(r"^lgdl_"))
async def handle_download_callback(client, callback_query):
    """Handle download callback queries"""
    user_id = callback_query.from_user.id
    async with USER_LOCKS[user_id]:
        try:
            data_parts = callback_query.data.split("_", 2)
            encoded_query, index = data_parts[1], int(data_parts[2])
            original_query = urllib.parse.unquote(encoded_query)
            
            await callback_query.answer("📥 Starting download...")
            progress_msg = await callback_query.message.reply("⏳ Downloading book from server...")
            
            results = await libgen_search(original_query)
            if not results or index >= len(results):
                return await progress_msg.edit("❌ Book details not found.")

            book = results[index]
            if not (download_url := book.get('Direct_Download_Link')):
                return await progress_msg.edit("❌ No direct download available for this book.")

            # File handling
            clean_title = "".join(c if c.isalnum() else "_" for c in book['Title'])
            file_ext = book.get('Extension', 'pdf')
            filename = f"{clean_title[:50]}.{file_ext}"
            temp_path = f"downloads/{filename}"
            os.makedirs("downloads", exist_ok=True)

            try:
                await progress_msg.edit("⬇️ Downloading file... (0%)")
                await download_libgen_file(
                    url=download_url,
                    temp_path=temp_path,
                    progress_msg=progress_msg,
                    user_id=user_id
                )

                await progress_msg.edit("📤 Uploading to Telegram...")
                sent_msg = await upload_to_telegram(
                    client=client,
                    temp_path=temp_path,
                    book=book,
                    progress_msg=progress_msg,
                    chat_id=callback_query.message.chat.id,
                    user_id=user_id
                )

                await handle_auto_delete(client, sent_msg, callback_query.message.chat.id)
                await log_download(client, temp_path, book, callback_query)
                await progress_msg.delete()

            except Exception as e:
                logger.error(f"Download error: {e}")
                await progress_msg.edit(f"❌ Download failed: {str(e)}")
                await asyncio.sleep(5)
            
            finally:
                if os.path.exists(temp_path):
                    try: os.remove(temp_path)
                    except: pass

        except Exception as e:
            logger.error(f"Callback error: {e}")
            await callback_query.answer("❌ Error processing request")