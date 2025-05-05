import os
import discord
import aiohttp
import logging
import textwrap
from dotenv import load_dotenv

load_dotenv()
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_MODEL = "gemini-1.5-pro-001"
MAX_HISTORY_TURNS = 10
MAX_RESPONSE_LENGTH = 1990
SYSTEM_INSTRUCTION_MAX_LENGTH = 1000

logging.basicConfig(level=logging.INFO, format='%(asctime)s:%(levelname)s:%(name)s: %(message)s')
logger = logging.getLogger(__name__)

async def send_long_message(interaction: discord.Interaction, text: str, followup: bool = True, ephemeral: bool = False):
    """Sends a potentially long message, splitting it if necessary."""
    max_len = MAX_RESPONSE_LENGTH
    chunks = []

    if len(text) <= max_len:
        chunks.append(text)
    else:
        logger.info(f"Splitting long message (length {len(text)}) for user {interaction.user.id}")
        chunks = textwrap.wrap(
            text,
            max_len,
            break_long_words=True,
            replace_whitespace=False,
            break_on_hyphens=False
            )

    first_chunk = True
    sent_initial_response = interaction.response.is_done()

    for chunk in chunks:
        if first_chunk:
            if followup:
                await interaction.followup.send(chunk, ephemeral=ephemeral)
            elif not sent_initial_response:
                await interaction.response.send_message(chunk, ephemeral=ephemeral)
            else:
                 await interaction.channel.send(chunk)
            first_chunk = False
        else:
            await interaction.channel.send(chunk)

intents = discord.Intents.default()
intents.message_content = True

class GeminiBot(discord.Client):
    def __init__(self):
        super().__init__(intents=intents)
        self.tree = discord.app_commands.CommandTree(self)
        self.session = None
        self.histories = {}
        self.system_instructions = {}

    async def setup_hook(self):
        """Initialize resources and sync commands when the bot connects."""
        self.session = aiohttp.ClientSession()
        logger.info("aiohttp ClientSession created.")
        await self.tree.sync()
        logger.info("Slash commands synced globally.")

    async def close(self):
        """Clean up resources when the bot disconnects."""
        if self.session:
            await self.session.close()
            logger.info("aiohttp ClientSession closed.")
        await super().close()

    async def on_ready(self):
        """Called when the bot is ready and connected to Discord."""
        logger.info(f'Logged in as {self.user} (ID: {self.user.id})')
        logger.info('Bot is ready and listening for commands.')
        activity = discord.Activity(type=discord.ActivityType.custom, name="Custom Status", state=" Made by notdoctored")
        await self.change_presence(activity=activity)
        logger.info(f"Set presence: {activity.state}")


async def ask_gemini(user_id: int, question: str, session: aiohttp.ClientSession, histories: dict, system_instructions: dict):
    """
    sends a question to the Gemini API, maintaining conversation history and using system instructions.

    Args:
        user_id: the Discord user ID to manage history and instructions for.
        question: the user's question.
        session: the aiohttp cclientSession for making requests.
        histories: the dictionary storing conversation histories.
        system_instructions: the dictionary storing user-specific system instructions.

    Returns:
        The text response from Gemini or an error message string.
    """
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}"
    headers = {"Content-Type": "application/json"}

    user_history = histories.get(user_id, [])
    user_history.append({"role": "user", "parts": [{"text": question}]})

    if len(user_history) > MAX_HISTORY_TURNS * 2:
        user_history = user_history[-(MAX_HISTORY_TURNS * 2):]

    payload = {
        "contents": user_history,
        "generationConfig": {
            "temperature": 0.7,
            "topP": 1,
            "topK": 1,
        },
         "safetySettings": [
            {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_MEDIUM_AND_ABOVE"},
            {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_MEDIUM_AND_ABOVE"},
            {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_MEDIUM_AND_ABOVE"},
            {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_MEDIUM_AND_ABOVE"},
        ]
    }

    user_system_instruction = system_instructions.get(user_id)
    if user_system_instruction:
        payload["systemInstruction"] = {
            "parts": [{"text": user_system_instruction}]
        }
        logger.debug(f"Using system instruction for user {user_id}")

    try:
        async with session.post(url, headers=headers, json=payload) as response:
            response_data = await response.json()
            logger.debug(f"Gemini API Response Status: {response.status}")

            if response.status == 200:
                if 'candidates' in response_data and response_data['candidates']:
                    candidate = response_data['candidates'][0]
                    if 'content' in candidate and 'parts' in candidate['content'] and candidate['content']['parts']:
                        response_text = candidate['content']['parts'][0]['text']
                        user_history.append({"role": "model", "parts": [{"text": response_text}]})
                        histories[user_id] = user_history
                        return response_text
                    elif 'finishReason' in candidate and candidate['finishReason'] != 'STOP':
                         reason = candidate.get('finishReason', 'UNKNOWN')
                         safety_ratings = candidate.get('safetyRatings', [])
                         logger.warning(f"Gemini response generation stopped for user {user_id}. Reason: {reason}, Safety: {safety_ratings}")
                         histories[user_id] = user_history
                         return f"this response generation was stopped. Reason: `{reason}`. Please modify your prompt or check safety settings. Your message was added to the history for context."
                    else:
                        logger.error(f"Gemini API response missing expected content structure in candidate for user {user_id}: {candidate}")
                        histories[user_id] = user_history
                        return "Sorry, I received an unexpected response structure from the AI after generation. Your message was added to the history."
                elif 'promptFeedback' in response_data:
                     feedback = response_data['promptFeedback']
                     block_reason = feedback.get('blockReason', 'UNKNOWN')
                     safety_ratings = feedback.get('safetyRatings', [])
                     logger.warning(f"Gemini prompt blocked for user {user_id}. Reason: {block_reason}, Safety: {safety_ratings}")
                     if user_history and user_history[-1]["role"] == "user":
                         user_history.pop()
                         histories[user_id] = user_history
                     return f"Your prompt was blocked before generation. Reason: `{block_reason}`. Please rephrase your message. It was not added to the history."
                else:
                    logger.error(f"Gemini API response missing 'candidates' or 'promptFeedback' for user {user_id}: {response_data}")
                    if user_history and user_history[-1]["role"] == "user":
                         user_history.pop()
                         histories[user_id] = user_history
                    return "Sorry, I received an incomplete or unexpected response format from the AI. Your message was not added to the history."
            else:
                error_details = response_data.get('error', {}).get('message', 'No specific error message.')
                logger.error(f"Gemini API error {response.status} for user {user_id}: {error_details} - Response: {response_data}")
                if user_history and user_history[-1]["role"] == "user":
                    user_history.pop()
                    histories[user_id] = user_history
                return f"Sorry, there was an error communicating with the AI (Status {response.status}). Details: {error_details[:300]}. Your message was not added to history."

    except aiohttp.ClientConnectorError as e:
        logger.exception(f"Network error connecting to Gemini API for user {user_id}.")
        if user_history and user_history[-1]["role"] == "user":
            user_history.pop()
            histories[user_id] = user_history
        return f"Sorry, I couldn't connect to the AI service. Please check the bot's network connection. Error: {e}. Your message was not added to history."
    except Exception as e:
        logger.exception(f"An unexpected error occurred during the Gemini API call for user {user_id}.")
        if user_history and user_history[-1]["role"] == "user":
            user_history.pop()
            histories[user_id] = user_history
        return f"An unexpected error occurred while talking to the AI: {e}. Your message was not added to history."

bot = GeminiBot()

@bot.tree.command(name="help", description="Shows a list of available commands.")
async def help_command(interaction: discord.Interaction):
    """Provides a list of all available slash commands."""
    embed = discord.Embed(
        title=f"{bot.user.name} Commands",
        description="Here are the commands you can use with me:",
        color=discord.Color.blue()
    )

    for cmd in bot.tree.get_commands():
        params_desc = ""
        if cmd.parameters:
            params_desc = " " + " ".join(f"`<{p.name}>`" for p in cmd.parameters)
        embed.add_field(name=f"`/{cmd.name}{params_desc}`", value=cmd.description, inline=False)

    embed.set_footer(text="Conversations have history unless reset with /reset_history.")
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="ask", description="ask the AI a question (maintains conversation history).")
@discord.app_commands.describe(question="Your question for the AI")
async def ask(interaction: discord.Interaction, question: str):
    """Handles the /ask slash command."""
    await interaction.response.defer(thinking=True, ephemeral=False)
    user_id = interaction.user.id
    context = "Unknown Context"
    if interaction.guild:
        context = f"Server: {interaction.guild.name} ({interaction.guild_id}), Channel: #{interaction.channel.name}"
    elif interaction.channel.type == discord.ChannelType.private:
        context = "Direct Message"
    elif interaction.channel.type == discord.ChannelType.group:
        context = f"Group DM ({interaction.channel.id})"

    logger.info(f"User {interaction.user} ({user_id}) in {context} asked: '{question[:100]}...'")

    answer = await ask_gemini(user_id, question, bot.session, bot.histories, bot.system_instructions)

    await send_long_message(interaction, answer, followup=True, ephemeral=False)
    logger.info(f"Sent response (length {len(answer)}) to {interaction.user} ({user_id}) in {context}")


@bot.tree.command(name="reset_history", description="Reset your conversation history with the AI.")
async def reset_history(interaction: discord.Interaction):
    """Handles the /reset_history slash command."""
    user_id = interaction.user.id
    if user_id in bot.histories:
        del bot.histories[user_id]
        logger.info(f"Reset history for user {interaction.user} (ID: {user_id})")
        await interaction.response.send_message("🧹 Your chat history with me has been cleared.", ephemeral=True)
    else:
        logger.info(f"User {interaction.user} (ID: {user_id}) attempted to reset non-existent history.")
        await interaction.response.send_message("You don't have any chat history with me yet.", ephemeral=True)


@bot.tree.command(name="set_prompt", description="Set a custom system prompt/instruction for the AI in this chat.")
@discord.app_commands.describe(instruction=f"Instructions for the AI's behavior (max {SYSTEM_INSTRUCTION_MAX_LENGTH} chars)")
async def set_prompt(interaction: discord.Interaction, instruction: str):
    """Sets a system instruction for the user's conversation."""
    user_id = interaction.user.id
    if len(instruction) > SYSTEM_INSTRUCTION_MAX_LENGTH:
        await interaction.response.send_message(f"Error: System instruction is too long (max {SYSTEM_INSTRUCTION_MAX_LENGTH} characters). Please shorten it.", ephemeral=True)
        return

    bot.system_instructions[user_id] = instruction
    logger.info(f"Set system instruction for user {interaction.user} (ID: {user_id})")
    await interaction.response.send_message(f"✅ Understood! I will now try to follow these instructions for our conversation:\n```\n{instruction}\n```\nUse `/reset_prompt` to clear this.", ephemeral=True)


@bot.tree.command(name="reset_prompt", description="Reset the custom AI system prompt/instruction for this chat.")
async def reset_prompt(interaction: discord.Interaction):
    """Resets the system instruction for the user's conversation."""
    user_id = interaction.user.id
    if user_id in bot.system_instructions:
        del bot.system_instructions[user_id]
        logger.info(f"Reset system instruction for user {interaction.user} (ID: {user_id})")
        await interaction.response.send_message(" My custom system instruction for our chat has been reset to default.", ephemeral=True)
    else:
        logger.info(f"User {interaction.user} (ID: {user_id}) attempted to reset non-existent system instruction.")
        await interaction.response.send_message("You haven't set a custom system instruction with me yet.", ephemeral=True)


@bot.tree.command(name="forget", description="Remove the last question and AI answer from your history.")
async def forget_last(interaction: discord.Interaction):
    """Removes the last user message and model response from the history."""
    user_id = interaction.user.id
    if user_id in bot.histories and len(bot.histories[user_id]) >= 2:
        last_entry = bot.histories[user_id][-1]
        second_last_entry = bot.histories[user_id][-2]

        if second_last_entry.get("role") == "user" and last_entry.get("role") == "model":
            last_model = bot.histories[user_id].pop()
            last_user = bot.histories[user_id].pop()
            logger.info(f"User {interaction.user} ({user_id}) used /forget. Removed last user msg and model response.")
            await interaction.response.send_message(f" Okay, I've forgotten our last exchange (your question starting with \"{last_user['parts'][0]['text'][:50]}...\" and my response).", ephemeral=True)
        else:
            bot.histories[user_id].pop()
            bot.histories[user_id].pop()
            logger.warning(f"User {interaction.user} ({user_id}) used /forget. Popped last two entries, roles might have been unusual: {second_last_entry.get('role')}, {last_entry.get('role')}")
            await interaction.response.send_message(f" Okay, I've forgotten the last two messages in our history.", ephemeral=True)

    elif user_id in bot.histories and len(bot.histories[user_id]) == 1:
         last_user = bot.histories[user_id].pop()
         logger.info(f"User {interaction.user} ({user_id}) used /forget. Removed the only message in history (user: '{last_user['parts'][0]['text'][:50]}...')")
         await interaction.response.send_message(" Okay, I've forgotten your last message (there was no response from me yet).", ephemeral=True)
    else:
        logger.info(f"User {interaction.user} ({user_id}) attempted /forget on empty history.")
        await interaction.response.send_message("There's nothing in our recent history for me to forget!", ephemeral=True)


@bot.tree.command(name="show_history", description="View the recent conversation history I remember (Ephemeral).")
async def show_history(interaction: discord.Interaction):
    """Displays the user's current conversation history ephemerally."""
    user_id = interaction.user.id
    if user_id not in bot.histories or not bot.histories[user_id]:
        await interaction.response.send_message("You don't have any chat history with me yet.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True, thinking=True)

    history = bot.histories[user_id]
    num_turns = (len(history) + 1) // 2
    formatted_history = f" **Conversation History (Approx. last {num_turns}/{MAX_HISTORY_TURNS} turns):**\n\n"
    turn_counter = 1
    current_role = None
    for i, entry in enumerate(history):
        role = entry.get("role", "unknown").capitalize()
        text = entry.get("parts", [{}])[0].get("text", "*empty message*")

        if role == "User":
             if current_role != "User":
                 formatted_history += f"**{turn_counter}. You:**\n"
                 turn_counter += 1
             else:
                 formatted_history += f"**(... You continued):**\n"
             current_role = "User"
        elif role == "Model":
             formatted_history += f"**Me ({GEMINI_MODEL}):**\n"
             current_role = "Model"
        else:
            formatted_history += f"**{role}:**\n"
            current_role = role

        display_text = text[:500] + '...' if len(text) > 500 else text
        formatted_history += f"```\n{display_text}\n```\n"

    await send_long_message(interaction, formatted_history, followup=True, ephemeral=True)

@bot.tree.command(name="ping", description="Check the bot's responsiveness.")
async def ping(interaction: discord.Interaction):
    """Checks the bot's latency to Discord."""
    latency_ms = bot.latency * 1000
    logger.info(f"Ping command used by {interaction.user}. Latency: {latency_ms:.2f} ms")
    await interaction.response.send_message(f"Pong! My latency to Discord is {latency_ms:.2f} ms.", ephemeral=True)


if __name__ == "__main__":
    if not DISCORD_TOKEN:
        logger.critical("FATAL ERROR: DISCORD_TOKEN not found in environment variables or .env file.")
    elif not GEMINI_API_KEY:
        logger.critical("FATAL ERROR: GEMINI_API_KEY not found in environment variables or .env file.")
    else:
        try:
            logger.info("Starting bot...")
            bot.run(DISCORD_TOKEN, log_handler=None)
        except discord.LoginFailure:
            logger.critical("FATAL ERROR: Invalid Discord Token. Please check your DISCORD_TOKEN.")
        except discord.errors.PrivilegedIntentsRequired:
             logger.critical("FATAL ERROR: The 'Message Content' Intent is not enabled for this bot in the Discord Developer Portal (Application -> Bot -> Privileged Gateway Intents). It might be needed for future features or certain command argument types.")
        except Exception as e:
            logger.critical(f"FATAL ERROR: Failed to start the bot - {e}", exc_info=True)
