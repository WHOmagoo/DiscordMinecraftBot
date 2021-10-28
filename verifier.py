from datetime import datetime
from datetime import timedelta
import random
import re

# codeRe = re.compile(r'[a-zA-Z0-9]{6}-[a-zA-Z0-9]{6}-[a-zA-Z0-9]{6}')
codeRe = re.compile(r'[0-9]{6}')

class CodeTimeoutException(Exception):
    def __init__(self):
        self.message = "Code has timed out"

    def __str__(self):
        return self.message


class UsernameMismatchException(Exception):
    def __init__(self, given, expected):
        self.message = f"Code verified with unexpected username. Expected {expected}, received {given}."

    def __str__(self):
        return self.message

class CodeMismatchException(Exception):
    def __init__(self):
        self.message = "The code given did not match the expected code"

    def __str__(self):
        return self.message

class AlreadyVerifiedException(Exception):
    def __init__(self):
        self.message = "Already Verified This platform"

    def __str__(self):
        return self.message


def getRandomChar():
    max = 61

    chari = random.randint(0, 61)

    if chari < 10:
        return chr(48 + chari)
    elif chari < 10 + 26:
        return chr(65 + chari - 10)
    else:
        return chr(97 + chari - 10 - 26)


def generateRandomCode():
    result = ""

    # for i in range(3):
    #     for i in range(6):
    #         result += getRandomChar()
    #
    #     result += "-"
    #
    # return result[:-1]

    result = ""

    for i in range(6):
        result += str(random.randint(0,9))

    return result

class VerificationRecord:
    expireTime = None
    code = ""
    username = None
    verified = False

    def __init__(self, profile, code=None):
        self.code = generateRandomCode() if code is None else code
        self.username = profile
        self.refresh()

    def isExpired(self):
        return datetime.now() > self.expireTime

    def refresh(self):
        self.expireTime = datetime.now() + timedelta(minutes=5)

    def verify_record(self, code, username):
        if self.verified:
            raise AlreadyVerifiedException()
        if self.isExpired():
            raise CodeTimeoutException()

        result = (self.username is None or self.username == username) and self.code == code

        if result:
            self.username = username
            self.verified = True

        return result

class VerificationPair:
    vDiscord = None
    vMinecraft = None

    onVerification = None

    def __init__(self, discordProfile=None, minecraftProfile=None, onVerification=None):
        self.vDiscord = VerificationRecord(discordProfile)
        self.vMinecraft = VerificationRecord(minecraftProfile)
        self.onVerification = onVerification

    def isCompleted(self):
        return (self.vDiscord is None or self.vDiscord.verified) and (self.vMinecraft is None or self.vMinecraft.verified)

    async def verify(self, code, username, enteredFromDiscord):
        verifier = self.vDiscord if enteredFromDiscord else self.vMinecraft
        result = verifier.verify_record(code, username)

        if result:
            other = self.vMinecraft if enteredFromDiscord else self.vDiscord
            if other.verified:
                return await self.onVerification(self)
            else:
                other.refresh()
                return other

class VerificationMaster:
    # discordCodes = {}
    # minecraftCodes = {}

    codePairs = {}

    def add(self, pair):
        discord = pair.vDiscord
        if discord is not None:
            if discord.code in self.codePairs:
                print("Key already exists in DiscordCodes")
                return False

        minecraft = pair.vMinecraft
        if minecraft is not None:
            if minecraft.code in self.codePairs:
                print("Key already exists in MinecraftCodes")
                return
            else:
                self.codePairs[minecraft.code] = pair
                self.codePairs[discord.code] = pair


    def hasCodes(self):
        # return len(self.discordCodes) > 0 or len(self.minecraftCodes) > 0
        return len(self.codePairs) > 0

    def containsCode(self, code):
        # return code in self.discordCodes or code in self.minecraftCodes
        return code in self.codePairs

    async def verify(self, code, username, fromDiscord):
        result = code in self.codePairs and await self.codePairs[code].verify(code, username, fromDiscord)
        if result:
            return self.codePairs[code]

        return None

    async def verifyMinecraft(self, code, minecraftName):
        return await self.verify(code, minecraftName, False)

    async def verifyDiscord(self, code, discordProfile):
        return await self.verify(code, discordProfile, True)
            # pair = self.discordCodes[code]
            # if pair.vDiscord.profile is None:
            #     pair.vDiscord.profile = discordProfile
            # elif pair.vDiscord.profile != discordProfile:
            #     raise Exception("Invalid discord profile")
            # pair.discord = True
            # self.discordCodes.pop(code)
            # if pair.minecraft:
            #     self.completeVerification(pair)
            # else:
            #     newMinecraft = VerificationRecord(vDiscord.profile)



    # def verifyMinecraft(self, code, minecraftName):
    #     if code in self.minecraftCodes:
    #         pair = self.minecraftCodes[code]
    #         if pair.vMinecraft.profile is None:
    #             pair.vMinecraft.profile = minecraftName
    #         elif pair.vMinecraft.profile != minecraftName:
    #             raise Exception("Invalid minecraft name")
    #
    #         pair.minecraft = True
    #         pair.vMinecraft.profile = minecraftName
    #         self.minecraftCodes.pop(code)