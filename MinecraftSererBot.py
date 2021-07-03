import os

import discord
from mcrcon import MCRcon
import threading
import asyncio
import re
import json
import traceback

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

minecraftConsoleParser = re.compile(r'''(?P<timestamp>\[\d{2}:\d{2}:\d{2}\]) \[(?P<thread>[^]\/]*)\/(?P<level>[^]\/]*\]): (<(?P<username>[^>]*)>)?(?P<message>.*)''')

userCountRe = re.compile(r'There are (\d+) of a max (\d+) players online')
userCount = -1
userMax = -1
userList = None
cur = 0

isConnected = None

verifierMaster = verifier.VerificationMaster()
outputLock = threading.Lock()

guilds = []

def init_mcr():
    try:
        global mcr
        mcr = MCRcon(rconAddress, rconPassword)
        mcr.connect()
        l = mcr.command("list")
        print(l)
        return True
    except Exception as e:
        if isConnected:
            print("Could not connect to Minecraft RCON")
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
    guildList = client.fetch_guilds(limit=2)
    async for guild in guildList:
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
    try:
        response = mcr.command("list")
        result = userCountRe.match(response)
        if result.group(1) is not None and result.group(2) is not None:
            newUserCount = result.group(1)
            newUserMax = result.group(2)

            isConnected = True

            if newUserMax != userMax or newUserCount != userCount:
                userMax = newUserMax
                userCount = newUserCount
                status = f"server {userCount}/{userMax}"
                await client.change_presence(activity=discord.Activity(type=discord.ActivityType.listening, name=status))
    except Exception as e:
        if isConnected is None or isConnected:
            print(e)
            # await client.change_presence(activity=discord.Activity(type=discord.ActivityType., name="the waiting game..."))
        await client.change_presence(status=discord.Status.idle, activity=discord.Activity(type=discord.ActivityType.playing, name="the waiting game..."))
        init_mcr()
        isConnected = False


async def follow(thefile):
    thefile.seek(0,2)
    while True:
        line = thefile.readline()
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

async def read_minecraft_server():
    print("Reading minecraft output")
    logfile = open(pathToLogFile,'rt')
    loglines = follow(logfile)

    async for line in loglines:
        print(line)
        parsed = minecraftConsoleParser.match(line)
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
                            result = mcr.command(mcr.command(f'tellraw {username} [{{"text":"Verify your Discord account with the code ", "color":"white"}}, {{"text":"{result.vDiscord.code}", "color":"green"}}, {{"text":" by DMing it to the bot. Your code will expire in 5 minutes", "color":"white"}}]'))
                            print(result)
                        break

            if message.startswith("!verify"):
                pair = verifier.VerificationPair(minecraftProfile=username, onVerification=on_verification)
                verifierMaster.add(pair)
                discordCode = pair.vDiscord.code
                mcr.command(f"tell {username} Verify your Discord account with the code {discordCode} by DMing it to the bot. Your code will expire in 5 minutes")
            else:
                discordName = await mcToDc(username)
                if discordName is None:
                    nameToShow = f"<{username}>"
                else:
                    nameToShow = f"[{discordName}]"

                await client.get_channel(channelId).send(f"{nameToShow} {message}")

@client.event
async def on_ready():
    print(f'{client.user} has connected to Discord!')
    global guilds
    asyncio.get_event_loop().create_task(read_minecraft_server())
    asyncio.get_event_loop().create_task(schedule(5, update_user_count))
    # th = threading.Thread(target=read_minecraft_server)


@client.event
async def on_message(message):
    msg = message.content

    print(msg)

    if message.author.id != botId:
        if message.channel.id == channelId or message.channel.type == discord.ChannelType.private:
            if msg == "!list":
                if mcr is not None:
                    response = mcr.command("list")
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
            "botId": botId,
            "token": token,
            "channelId": channelId,
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

    init_mcr()


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

