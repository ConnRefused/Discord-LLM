prerequisites:

    python 3+ installed.

    pip installed..

get discord bot token:

    go to the discord developer portal: discord.com/developers/applications

    create a new application or select an existing one.

    go to the 'bot' tab, click 'add bot'.

    click 'reset token', copy the token shown. keep it secret.

get gemini api key:

    go to google ai studio: aistudio.google.com

    log in, click 'get api key'.

    create or select a project, copy the generated api key. dont share publically

prepare project & .env file:

    create a folder for the bot.

    put the python script inside the folder.

    inside the folder, create a file named exactly .env.

    add these lines to .env, replacing placeholders with your token and key:

          
    DISCORD_TOKEN=URTOKENGOESHERE
    GEMINI_API_KEY=URKEYGOESHERE


    save the .env file.

install python libraries:

    open your terminal/command prompt.

    go into your project folder using the cd command.

    run: pip install -u discord.py python-dotenv aiohttp

configure discord application:

    go back to the discord developer portal, select your application.

    'bot' tab: enable 'message content intent' under privileged gateway intents.

    'oauth2' tab -> 'general': check the box for 'user install' under installation options.

    click 'save changes'.

run the bot:

    in your terminal (still in the project folder), run: ai.py

    (replace ai.py with the actual filename).

    look for login and sync messages in the terminal. keep the terminal open while the bot runs.
