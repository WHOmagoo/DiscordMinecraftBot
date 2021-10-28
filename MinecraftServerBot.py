import os
import shutil
from queue import Queue, Empty

import discord
from mcrcon import MCRcon
import threading
import asyncio
import re
import json
import traceback
import concurrent.futures
import subprocess

import verifier
from os.path import exists

mcr = None
client = discord.Client()

botId = ""
channelId = ""
pathToLogFile = ""
rconPassword = ""
rconAddress = ""
token = ""

minecraftConsoleParser = re.compile(r'''(?P<timestamp>\[\d{2}:\d{2}:\d{2}\]) \[(?P<thread>[^]\/]*)\/(?P<level>[^]\/]*\]):( <(?P<username>[^>]*)> )?(?P<message>.*)''')
minecraftUserChange = re.compile(r' (?P<username>[a-zA-Z0-9_]{3,16})(?P<type>(?P<joined> joined the game)|(?P<left> left the game))')
minecraftAFKPlayers = re.compile(r'Team \[AFK Players\] has [\d]+ member[s]?: (?P<usernames>(?:[a-zA-Z0-9_]{3,16}(?:, )?)+)')
minecraftOnlinePlayers = re.compile(r'There are (?P<online>[1-9]\d?) of a max of (?P<max>\d+) players online: (?P<usernames>(?: [a-zA-Z0-9_]{3,16}(?:, )?)+)')

userCountRe = re.compile(r'[^\d]*(?P<online>\d+)[^\d]*(?P<max>\d+)')
userCount = -1
userMax = -1
userList = None
cur = 0

isConnected = None

verifierMaster = verifier.VerificationMaster()
outputLock = threading.Lock()

guilds = []

displayedWrongPassword = False
displayedChannelError = False

def init_mcr():
    global mcr, displayedWrongPassword, isConnected

    try:
        mcr.disconnect()
    except:
        pass

    try:
        mcr = MCRcon(rconAddress, rconPassword)
        mcr.connect()
        l = mcr.command("list")
        print(l)
        isConnected = True
        return True
    except Exception as e:
        isConnected = False
        if isConnected:
            print("Could not connect to Minecraft RCON")

        for arg in e.args:
            if not displayedWrongPassword and arg == 'Login failed':
                print("[Error] Wrong RCON password entered")
                displayedWrongPassword = True
        return False

def on_verification(pair):
    print("On Verification finished")
    outputLock.acquire()
    try:
        did = str(pair.vDiscord.username.id)
        mcid = pair.vMinecraft.username
        userList["discord"][did] = mcid
        userList["minecraft"][mcid] = did
        with open("users.json", 'wt') as writer:
            json.dump(userList, writer)
    finally:
        outputLock.release()


async def schedule(time, func, args=(), count=0):
    timesRan = 0
    while count == 0 or timesRan < count:
        if count != 0:
            timesRan += 1

        await func(*args)
        await asyncio.sleep(time)

async def idToDiscordNick(id):
    guild = client.get_channel(channelId).guild
    try:
        member = await guild.fetch_member(id)
        if member.nick is not None:
            return member.nick
    except:
        pass

    user = await client.fetch_user(id)
    return user.display_name

async def update_user_count():
    global userCount, userMax, isConnected

    failure = True
    executor = concurrent.futures.ThreadPoolExecutor()

    if isConnected:
        try:
            future = executor.submit(mcr.command, 'list')
            try:
                response = future.result(timeout=5)
            except concurrent.futures.TimeoutError as e:
                raise TimeoutError("MC RCON call timed out")

            # executor.shutdown(wait=False, cancel_futures=True)
            executor.shutdown(wait=False)

            result = userCountRe.match(response)
            if result is not None and result.group('online') is not None and result.group('max') is not None:
                newUserCount = result.group('online')
                newUserMax = result.group('max')

                if newUserMax != userMax or newUserCount != userCount:
                    userMax = newUserMax
                    userCount = newUserCount
                    status = f"server {userCount}/{userMax}"
                    await client.change_presence(activity=discord.Activity(type=discord.ActivityType.listening, name=status))

                failure = False
        except Exception as e:
            if isConnected is None or isConnected:
                print(f'[Error] update_user_count: {e}')

    executor.shutdown(wait=False)

    if failure:
        if isConnected is None or isConnected:
            await client.change_presence(status=discord.Status.idle, activity=discord.Game(name="the waiting game..."))
        init_mcr()


def enqueue_output(reader, queue):
    while True:
        line = reader.stdout.readline().rstrip()
        queue.put(line)

async def follow_tail(filePath):
    tailPath = shutil.which("tail")
    # with subprocess.Popen([tailPath, "-F", "-n", "100", filePath], stdout=subprocess.PIPE) as server_log:
    with subprocess.Popen([tailPath, "-F", "-n", "0", filePath], stdout=subprocess.PIPE, encoding='ascii', bufsize=1, universal_newlines=True) as server_log:
        q = Queue()
        t = threading.Thread(target=enqueue_output, args=(server_log, q))
        t.daemon = True
        t.start()

        while True:
            try:
                yield q.get_nowait()
            except Empty:
                await asyncio.sleep(0.1)

async def follow(filePath):

    logFP = None
    while True:
        if logFP is None and isConnected:
            print("Opening file")
            logFP = open(filePath,'rt')
            logFP.seek(0,2)
        elif logFP is not None and not isConnected:
            print("Closing file")
            logFP.close()
            logFP = None

        line = logFP.readline() if logFP is not None else None
        if not line:
            await asyncio.sleep(0.1)
            continue

        yield line

# def tokenizeMinecraftConsoleLine(line):


async def mcToDc(mcName):
    if mcName is None or mcName not in userList['minecraft']:
        return

    try:
        return await idToDiscordNick(userList['minecraft'][mcName])
    except Exception as e:
        traceback.print_exc(type(e), e, e.__traceback__)
        return None


async def dcToMc(dcName):
    if isinstance(dcName, int):
        dcName = str(dcName)

    if dcName is None or dcName not in userList['discord']:
        return

    try:
        return userList['discord'][dcName]
    except Exception as e:
        traceback.print_exc(type(e), e, e.__traceback__)
        return None

def make_tellraw_for_code(username, code):
    return f'tellraw {username} [{{"text":"Verify your Discord account with the code ", "color":"white"}}, {{"text":"{code}", "color":"green"}}, {{"text":" by DMing it to the bot. Your code will expire in 5 minutes", "color":"white"}}]'

async def read_minecraft_server():
    print("Reading minecraft output")
    loglines = follow_tail(pathToLogFile)

    async for line in loglines:
        print(line)
        parsed = minecraftConsoleParser.match(line)

        username = None
        message = None

        if parsed is not None:
            username = parsed.group('username')
            message = parsed.group('message')

        if username is not None:
            if verifierMaster.hasCodes():
                foundCodes = verifier.codeRe.findall(message)
                for c in foundCodes:
                    result = verifierMaster.verifyMinecraft(c, username)
                    if result is not None:
                        if result.isCompleted():
                            on_verification(result)
                        else:
                            try:
                                result = mcr.command(make_tellraw_for_code(username, result.vDiscord.code))
                                print(result)
                            except Exception as e:
                                print(f'[Error] failed to send verification message to {username}')
                                print(e)

                        break

            if message.startswith("!verify"):
                pair = verifier.VerificationPair(minecraftProfile=username, onVerification=on_verification)
                verifierMaster.add(pair)
                discordCode = pair.vDiscord.code

                try:
                    mcr.command(make_tellraw_for_code(username, discordCode))
                except Exception as e:
                    print(f'[Error] failed to send verification message to {username}')
                    print(e)

            else:
                discordName = await mcToDc(username)
                if discordName is None:
                    nameToShow = f"<{username}>"
                else:
                    nameToShow = f"[{discordName}]"

                channel = client.get_channel(channelId)
                global displayedChannelError
                if channel is not None:
                    await channel.send(f"{nameToShow} {message}")
                elif not displayedChannelError:
                    print(f'[Error] Could not find specified channel with channel id {channelId}')
                    displayedChannelError = True
        elif message is not None:
            userChange = minecraftUserChange.fullmatch(message)
            if userChange is None:
                continue

            username = userChange.group("username")
            discordName = await mcToDc(username)
            if discordName is None:
                nameToShow = f"<{username}>"
            else:
                nameToShow = f"[{discordName}]"

            channel = client.get_channel(channelId)
            if channel is not None:
                connectionType = userChange.group("type")
                await channel.send(f"{nameToShow}{connectionType}")


@client.event
async def on_ready():
    print(f'{client.user} has connected to Discord!')
    global guilds

    asyncio.get_event_loop().create_task(read_minecraft_server())
    asyncio.get_event_loop().create_task(schedule(5, update_user_count))


async def list_players():
    if mcr is not None:
        response = mcr.command("list")
        afk_players = mcr.command("team list afkDis.afk")
        afk_match = minecraftAFKPlayers.match(afk_players)
        if afk_match is not None:
            afk_player_list = afk_match.group("usernames")
            afk_player_split = afk_player_list.split(", ")
            for player in afk_player_split:
                player_match = "(" + player + ")(,|$)"
                player_replace = r"~~\1~~\2"
                response = re.sub(player_match, player_replace, response)

        return response

    return "Could not communicate with Server"




@client.event
async def on_message(message):
    msg = message.content

    print(msg)

    if message.author.id != botId:
        if message.channel.id == channelId or message.channel.type == discord.ChannelType.private:
            if msg == "!list":
                response = await list_players()
                await message.channel.send(response)
            elif msg == "!verify":
                pair = verifier.VerificationPair(discordProfile=message.author, onVerification=on_verification)
                verifierMaster.add(pair)
                minecraftCode = pair.vMinecraft.code
                try:
                    await message.author.send(f"verify your minecraft account with the code `{minecraftCode}`. Your code will expire in 5 minutes")
                except discord.errors.Forbidden as e:
                    await message.channel.send(e.text)
            else:
                if verifierMaster.hasCodes():
                    foundCodes = verifier.codeRe.findall(msg)
                    for c in foundCodes:
                        verifyResult = verifierMaster.verifyDiscord(c, message.author)
                        if verifyResult is not None:
                            if verifyResult.vMinecraft.verified:
                                on_verification(verifyResult)
                            else:
                                await message.author.send(f"verify your minecraft account with the code `{verifyResult.vMinecraft.code}`. Your code will expire in 5 minutes")

        if message.channel.id == channelId:
            if mcr is not None:
                auth = await dcToMc(message.author.id)
                if auth is None:
                    auth = message.author.nick
                    if auth is None:
                        auth = message.author.name

                    auth = f"[{auth}]"
                else:
                    auth = f"<{auth}>"

                mcr.command(f"say {auth} {msg}")


if __name__ == '__main__':
    if not exists("config.json"):
        botId = input("What is the bot id")
        token = input("What is the token")
        channelId = input("What is the channel id")
        pathToLogFile = input("What is the path to the log file")
        rconAddress = input("What is the RCON address")
        rconPassword = input("What is the RCON password")
        vars = {
            "botId": int(botId),
            "token": token,
            "channelId": int(channelId),
            "pathToLogFile": pathToLogFile,
            "rconAddress": rconAddress,
            "rconPassword": rconPassword
        }

        with open("config.json", 'wt') as outFile:
            json.dump(vars, outFile)
    else:
        with open("config.json", 'rt') as inFile:
            vars = json.load(inFile)
            botId = vars["botId"]
            token = vars["token"]
            channelId = vars["channelId"]
            pathToLogFile = vars["pathToLogFile"]
            rconAddress = vars["rconAddress"]
            rconPassword = vars["rconPassword"]

    try:
        usersPath = "users.json"
        if not exists(usersPath):
            starterJSON = '{"discord":{},"minecraft":{}}'
            userList = json.loads(starterJSON)

            with open(usersPath, 'wt') as outFile:
                outFile.write(starterJSON)
        else:
            with open(usersPath, 'rt') as users:
                userList = json.load(users)
                print(userList)
    except Exception as e:
        print("An excpetion occured in loading the user profiles :(")
        print(e)

    client.run(token)

