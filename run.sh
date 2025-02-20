#!/bin/bash

apk=${1?specify apk file}

cd ui_dumper

./run_ui_dumper.sh &

cd -

sleep 4

pid=$!

adb install ${apk}

timeout 60m python main.py -a ${apk} | ts >> bookscatalog.txt

kill -9 $pid