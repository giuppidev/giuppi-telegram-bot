import asyncio
import logging
import os
from typing import Dict, Set
from telegram import Update, ChatPermissions
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from telegram.constants import ChatMemberStatus

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Bot configuration
BOT_TOKEN = ""
REQUIRED_REACTIONS = 5

class ChatLockBot:
    def __init__(self):
        # Store locked chats and their trigger messages
        self.locked_chats: Dict[int, int] = {}  # chat_id -> message_id
        self.original_permissions: Dict[int, ChatPermissions] = {}  # chat_id -> original permissions
        self.required_reactions = REQUIRED_REACTIONS
        
    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /start command"""
        await update.message.reply_text(
            "🤖 Il Bot Blocca Chat è attivo! 🤪\n\n"
            "Quando verrò menzionato in una chat di gruppo, bloccherò la chat finché il messaggio "
            f"non riceverà {self.required_reactions} reazioni. 🥳\n\n"
            "Comandi:\n"
            "/start - Mostra questo messaggio 📜\n"
            "/status - Controlla se la chat è bloccata 🧐\n"
            "/unlock - Sblocco forzato (solo admin) 👮‍♂️\n"
            f"/set_reactions <numero> - Imposta le reazioni richieste (solo admin, attuale: {self.required_reactions}) ⚙️"
        )
    
    async def status_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Check if current chat is locked"""
        chat_id = update.effective_chat.id
        
        if chat_id in self.locked_chats:
            message_id = self.locked_chats[chat_id]
            await update.message.reply_text(
                f"🔒 Questa chat è attualmente bloccata! 🥶\n"
                f"ID messaggio di attivazione: {message_id} 🕵️\n"
                f"Servono {self.required_reactions} reazioni per sbloccare. 🙏"
            )
        else:
            await update.message.reply_text("🔓 Questa chat non è bloccata. Liberi tutti! 🎉")
    
    async def unlock_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Force unlock chat (admin only)"""
        chat_id = update.effective_chat.id
        user_id = update.effective_user.id
        
        # Check if user is admin
        if not await self.is_admin(context.bot, chat_id, user_id):
            await update.message.reply_text("❌ Solo gli amministratori possono forzare lo sblocco. Spiacente! 🤷")
            return
        
        if chat_id in self.locked_chats:
            await self.unlock_chat(context.bot, chat_id)
            await update.message.reply_text("🔓 La chat è stata sbloccata con la forza! 💪")
        else:
            await update.message.reply_text("ℹ️ La chat non è attualmente bloccata. Che ti aspettavi? 🤔")
    
    async def set_reactions_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Set required reactions count (admin only)"""
        chat_id = update.effective_chat.id
        user_id = update.effective_user.id
        
        # Check if user is admin
        if not await self.is_admin(context.bot, chat_id, user_id):
            await update.message.reply_text("❌ Solo gli amministratori possono modificare le impostazioni. Via di qui! 썩 꺼져!")
            return
        
        if not context.args:
            await update.message.reply_text(f"Reazioni richieste attuali: {self.required_reactions} 🤔")
            return
        
        try:
            new_count = int(context.args[0])
            if new_count < 1:
                raise ValueError("Count must be positive")
            
            self.required_reactions = new_count
            await update.message.reply_text(f"✅ Reazioni richieste impostate a {self.required_reactions}. Fatto! 👌")
        except ValueError:
            await update.message.reply_text("❌ Per favore, fornisci un numero positivo valido. Non fare il furbo! 😠")
    
    async def handle_mention(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle when bot is mentioned"""
        message = update.effective_message
        if not message:
            return

        chat_id = update.effective_chat.id
        message_id = message.message_id
        
        # Check if bot is mentioned
        bot_username = (await context.bot.get_me()).username
        message_text = message.text or message.caption or ""
        
        is_mention = f"@{bot_username}" in message_text
        is_reply_to_bot = (
            message.reply_to_message and
            message.reply_to_message.from_user.username == bot_username
        )
        
        if is_mention or is_reply_to_bot:
            # Check if chat is already locked
            if chat_id in self.locked_chats:
                await message.reply_text("⚠️ La chat è già bloccata! Sveglia! ⏰")
                return
            
            # Lock the chat
            await self.lock_chat(context.bot, chat_id, message_id)
            
            await message.reply_text(
                f"🔒 Chat bloccata! Questo messaggio ha bisogno di {self.required_reactions} reazioni per sbloccare la chat. 🔑\n"
                f"Tutti i nuovi messaggi verranno eliminati fino ad allora. 🗑️"
            )
    
    async def restrict_user_temporarily(self, bot, chat_id: int, user_id: int):
        """Temporarily restrict a specific user who tries to send messages in locked chat"""
        try:
            # Restrict the user for a short period
            restricted_permissions = ChatPermissions(can_send_messages=False)
            await bot.restrict_chat_member(
                chat_id=chat_id,
                user_id=user_id,
                permissions=restricted_permissions,
                until_date=None  # Indefinite restriction until chat is unlocked
            )
            logger.info(f"Temporarily restricted user {user_id} in chat {chat_id}")
        except Exception as e:
            logger.error(f"Failed to restrict user {user_id}: {e}")
    
    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle regular messages in locked chats"""
        chat_id = update.effective_chat.id
        user_id = update.effective_user.id
        
        # If chat is locked, delete the message (except from admins and the bot itself)
        if chat_id in self.locked_chats:
            # Don't delete messages from admins or the bot itself
            if await self.is_admin(context.bot, chat_id, user_id) or user_id == context.bot.id:
                return
                
            try:
                await context.bot.delete_message(chat_id, update.message.message_id)
                logger.info(f"Deleted message from user {user_id} in locked chat {chat_id}")
                
                # Optionally, restrict the user temporarily
                await self.restrict_user_temporarily(context.bot, chat_id, user_id)
                
            except Exception as e:
                logger.error(f"Failed to delete message: {e}")
    
    async def handle_reaction_update(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle reaction updates (Note: This requires special bot permissions)"""
        # Note: Telegram Bot API has limited reaction support
        # This is a placeholder for when reaction APIs become more available
        pass
    
    async def lock_chat(self, bot, chat_id: int, trigger_message_id: int):
        """Lock the chat by restricting permissions"""
        try:
            # Get current chat permissions
            chat = await bot.get_chat(chat_id)
            current_permissions = chat.permissions
            
            # Store original permissions
            self.original_permissions[chat_id] = current_permissions
            
            # Create heavily restricted permissions
            restricted_permissions = ChatPermissions(
                can_send_messages=False,
                can_send_polls=False,
                can_send_other_messages=False,
                can_add_web_page_previews=False,
                can_change_info=False,
                can_invite_users=False,
                can_pin_messages=False
            )
            
            # Apply restrictions to the entire chat
            await bot.set_chat_permissions(chat_id, restricted_permissions)
            
            # Store locked state
            self.locked_chats[chat_id] = trigger_message_id
            
            logger.info(f"Locked chat {chat_id} with trigger message {trigger_message_id}")
            
        except Exception as e:
            logger.error(f"Failed to lock chat {chat_id}: {e}")
            raise e
    
    async def unlock_chat(self, bot, chat_id: int):
        """Unlock the chat by restoring permissions"""
        try:
            # Restore original permissions
            if chat_id in self.original_permissions:
                original_permissions = self.original_permissions[chat_id]
                await bot.set_chat_permissions(chat_id, original_permissions)
                del self.original_permissions[chat_id]
            else:
                # Default permissions if we don't have the original ones
                default_permissions = ChatPermissions(
                    can_send_messages=True,
                    can_send_polls=True,
                    can_send_other_messages=True,
                    can_add_web_page_previews=True,
                    can_change_info=False,
                    can_invite_users=True,
                    can_pin_messages=False
                )
                await bot.set_chat_permissions(chat_id, default_permissions)
            
            # Remove from locked chats
            if chat_id in self.locked_chats:
                del self.locked_chats[chat_id]
            
            logger.info(f"Unlocked chat {chat_id}")
            
        except Exception as e:
            logger.error(f"Failed to unlock chat {chat_id}: {e}")
    
    async def is_admin(self, bot, chat_id: int, user_id: int) -> bool:
        """Check if user is admin in the chat"""
        try:
            member = await bot.get_chat_member(chat_id, user_id)
            return member.status in [ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER]
        except Exception as e:
            logger.error(f"Failed to check admin status: {e}")
            return False
    
    async def check_reactions_periodically(self, bot):
        """Periodically check reactions on trigger messages"""
        while True:
            try:
                for chat_id, message_id in list(self.locked_chats.items()):
                    # Note: This is a simplified check
                    # In practice, you'd need to implement reaction counting
                    # which is currently limited in Telegram Bot API
                    
                    # Placeholder for reaction checking logic
                    # You might need to use webhooks or other methods
                    # to get real-time reaction updates
                    
                    pass
                    
            except Exception as e:
                logger.error(f"Error checking reactions: {e}")
            
            await asyncio.sleep(10)  # Check every 10 seconds

def main():
    """Main function to run the bot"""
    # Create bot instance
    bot = ChatLockBot()
    
    # Create application
    application = Application.builder().token(BOT_TOKEN).build()
    
    # Add handlers
    application.add_handler(CommandHandler("start", bot.start_command))
    application.add_handler(CommandHandler("status", bot.status_command))
    application.add_handler(CommandHandler("unlock", bot.unlock_command))
    application.add_handler(CommandHandler("set_reactions", bot.set_reactions_command))
    
    # Handle mentions (messages that contain bot username)
    application.add_handler(MessageHandler(
        filters.TEXT & filters.ChatType.GROUPS & filters.Entity("mention"),
        bot.handle_mention
    ))
    
    # Handle all other messages in groups (for deletion in locked chats)
    application.add_handler(MessageHandler(
        filters.ALL & filters.ChatType.GROUPS,
        bot.handle_message
    ))
    
    # Start the bot
    print("🤖 Avvio di Chat Lock Bot...")
    print(f"Reazioni richieste: {bot.required_reactions}")
    print("Assicurati di:")
    print("1. Creare un file .env con il tuo BOT_TOKEN")
    print("2. Aggiungere il bot al tuo gruppo come amministratore")
    print("3. Concedere al bot le autorizzazioni per eliminare i messaggi e limitare i membri")
    
    # Run the bot
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()