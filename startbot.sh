#!/bin/bash
PANENAME="discordminecraftbot"

cd "$(dirname "$0")" ||  (echo "Couldn't change directory to script location"; exit)
tmux respawn-pane -k -t $PANENAME
if [ $? -eq 0 ]
then
  echo Restarted bot under existing session named $PANENAME
else
  tmux new-session -d -s $PANENAME "python3 MinecraftServerBot.py"
  if [ $? -eq 0 ]
  then
    echo Started bot with new session name $PANENAME
  else
    echo Could not start bot with a tmux session
    exit $?
  fi
fi